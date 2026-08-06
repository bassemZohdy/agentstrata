"""Global MCP server lifecycle manager (REQUIREMENTS.md MCP-01, MCP-02,
MCP-05).

One McpToolset per configured server, shared across sessions. Each server has
an independent reconciler with exponential backoff (1s → 2s → 4s → … capped
at 60s plus 0–250ms jitter) that resets on success. Readiness gates on all
``required: true`` servers being connected. Close is ref-counted: removed
components close only after their in-flight reference count reaches zero or
the shutdown deadline expires.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from google.adk.tools import McpToolset

from ..agent import AppliedConfig
from .bounds import bounded_httpx_client_factory
from .filtering import apply_tool_filter
from .stdio_sandbox import build_stdio_params, wrap_stdio_params

logger = logging.getLogger(__name__)

# Stdio connect+initialize deadline (seconds). google-adk 2.6.1 wraps plain
# StdioServerParameters in its own StdioConnectionParams with a hardcoded
# timeout=5; on slow platforms (cold start, arm64 under QEMU: the initialize
# handshake alone can exceed 10 s) that deadline fires and the session teardown
# race surfaces as anyio.WouldBlock, failing the connect. Passing ADK's own
# StdioConnectionParams with an explicit timeout is the documented way to set
# it (google/adk/tools/mcp_tool/mcp_toolset.py). The value stays bounded:
# reconnect pacing still comes from the backoff loop.
STDIO_CONNECT_TIMEOUT_SECONDS = 30.0

BACKOFF_CAP_SECONDS = 60.0
JITTER_MAX_SECONDS = 0.25

# R-11: how often a CONNECTED handle is liveness-probed (a dead-but-
# connected session is re-established, not trusted forever).
_LIVENESS_PROBE_SECONDS = 30.0


class ServerState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DEGRADED = "degraded"


@dataclass
class ServerHandle:
    """One configured MCP server's runtime state."""

    name: str
    transport: str
    required: bool
    tool_filter_allow: list[str] = field(default_factory=list)
    tool_filter_deny: list[str] = field(default_factory=list)
    max_transport_message_bytes: int = 1_048_576
    max_tools: int = 128
    toolset: McpToolset | None = None
    state: ServerState = ServerState.DISCONNECTED
    tools: list[Any] = field(default_factory=list)
    raw_names: list[str] = field(default_factory=list)
    filtered_names: list[str] = field(default_factory=list)
    final_names: list[str] = field(default_factory=list)
    backoff_seconds: float = 1.0
    last_error: str | None = None
    ref_count: int = 0
    _conn_retry: int = 0

    @property
    def connected(self) -> bool:
        return self.state == ServerState.CONNECTED


class ServerManager:
    """MCP-05 lifecycle manager + MCP-01 reconcilers + MCP-02 readiness.

    ``tool_targets`` (MA-03): ``(agent, allowed_server_names | None)`` pairs;
    None means every configured server. On connect, the server's FINAL
    (filtered + collision-renamed) tools are attached to every target that
    may see the server; on disconnect/close they are detached.
    """

    def __init__(
        self, applied: AppliedConfig, tool_targets: list[tuple[Any, list[str] | None]] | None = None
    ) -> None:
        self._applied = applied
        self._handles: dict[str, ServerHandle] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._closed = False
        self._started = False
        # (agent, allowed server names | None = all). Agents' tool lists are
        # mutated only under this lock (reconcilers run concurrently).
        self._tool_targets: list[tuple[Any, list[str] | None]] = list(tool_targets or [])
        self._attach_lock = asyncio.Lock()
        # server -> final tool names currently attached to the targets.
        self._attached: dict[str, list[str]] = {}
        # server -> last computed final names (stable across reconnects).
        self._final_by_server: dict[str, list[str]] = {}

    def set_tool_targets(self, tool_targets: list[tuple[Any, list[str] | None]]) -> None:
        """(Re)bind which agents see which servers (component rebuild)."""
        self._tool_targets = list(tool_targets)
        self._attached = {}

    def _targets_for(self, server_name: str) -> list[Any]:
        return [
            agent
            for agent, allowed in self._tool_targets
            if allowed is None or server_name in allowed
        ]

    async def _attach_tools(self, handle: ServerHandle) -> None:
        """MA-03: attach final-named tools to the agents that may see the
        server (idempotent). Every attach re-syncs ALL connected servers:
        raw tools are renamed in place to their CURRENT global finals (a
        later-connecting server may change an earlier one's final name), and
        each target's tool list is rebuilt from the servers it may see."""
        if handle.toolset is None or not handle.tools:
            return
        async with self._attach_lock:
            self._recompute_finals()
            # Rename every connected server's raw tools to the current finals.
            for _name, other in self._handles.items():
                if not other.tools:
                    continue
                final_of = dict(zip(other.raw_names, other.final_names, strict=False))
                for tool in other.tools:
                    if tool.name in final_of:
                        tool.name = final_of[tool.name]
            # Rebuild each target's list from the servers it may see.
            attached: dict[str, list[Any]] = {}
            for name, other in self._handles.items():
                if other.tools:
                    attached[name] = [t for t in other.tools if t.name in other.final_names]
            for agent, allowed in self._tool_targets:
                allowed_names = [n for n in self._handles if allowed is None or n in allowed]
                agent.tools = [t for n in allowed_names for t in attached.get(n, [])]
            self._attached = {n: [t.name for t in ts] for n, ts in attached.items()}

    async def _detach_tools(self, handle: ServerHandle) -> None:
        """Remove the server's tools from every target (disconnect/close)."""
        async with self._attach_lock:
            self._handles[handle.name].tools = []
            self._attached.pop(handle.name, None)
            attached: dict[str, list[Any]] = {}
            for name, other in self._handles.items():
                if other.tools:
                    attached[name] = [t for t in other.tools if t.name in other.final_names]
            for agent, allowed in self._tool_targets:
                allowed_names = [n for n in self._handles if allowed is None or n in allowed]
                agent.tools = [t for n in allowed_names for t in attached.get(n, [])]

    # -- configuration ----------------------------------------------------------

    def configure(self, servers: list[Any]) -> None:
        """Register/replace the configured server set (rebuild per REL-02)."""
        self._handles = {}
        for server in servers:
            self._handles[server.name] = ServerHandle(
                name=server.name,
                transport=server.transport.value,
                required=server.required,
                tool_filter_allow=list(server.toolFilter.allow),
                tool_filter_deny=list(server.toolFilter.deny),
                max_transport_message_bytes=server.maxTransportMessageBytes,
                max_tools=server.maxTools,
            )
        self._final_by_server = {}

    def _recompute_finals(self) -> None:
        """MCP-03: cross-server collision-safe final names.

        The final name space is GLOBAL (a raw name claimed by one server is
        ``{server}_{raw}`` for the next) and deterministic: computed from all
        servers' current filtered names in configured order, so reconnects
        keep stable final names."""
        used: set[str] = set()
        for name, handle in self._handles.items():
            finals: list[str] = []
            for raw in handle.filtered_names:
                if raw not in used:
                    finals.append(raw)
                    used.add(raw)
                else:
                    candidate = f"{name}_{raw}"
                    suffix = 2
                    while candidate in used:
                        candidate = f"{name}_{raw}_{suffix}"
                        suffix += 1
                    finals.append(candidate)
                    used.add(candidate)
            handle.final_names = finals
            self._final_by_server[name] = finals

    def handles(self) -> list[ServerHandle]:
        return list(self._handles.values())

    def handle(self, name: str) -> ServerHandle | None:
        return self._handles.get(name)

    def lookup_tool(self, final_name: str) -> tuple[str, str] | None:
        """HITL-02: map a FINAL (renamed) tool name back to its
        (server_name, raw_tool_name) pair, so approval patterns can match
        server/rawTool BEFORE renaming."""
        for name, handle in self._handles.items():
            for raw, final in zip(handle.raw_names, handle.final_names, strict=False):
                if final == final_name:
                    return (name, raw)
        return None

    # -- lifecycle ---------------------------------------------------------------

    async def start(self) -> None:
        if self._started or self._closed:
            return
        self._started = True
        for name in self._handles:
            self._tasks[name] = asyncio.create_task(self._reconcile_loop(name))

    async def close(self) -> None:
        """Ref-counted close: only shut down when no in-flight users remain
        (or the shutdown deadline expires)."""
        self._closed = True
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        for handle in self._handles.values():
            # Shutdown: close the shared toolset regardless of reference count.
            await self._close_toolset(handle)

    async def acquire(self, name: str) -> ServerHandle | None:
        """MCP-05: acquire a reference for a run; used by the engine."""
        handle = self._handles.get(name)
        if handle is not None:
            handle.ref_count += 1
        return handle

    async def release(self, name: str) -> None:
        handle = self._handles.get(name)
        if handle is not None:
            await self._release(handle)

    async def _release(self, handle: ServerHandle) -> None:
        if handle.ref_count <= 0:
            # Spurious release (no matching acquire) must not destroy the
            # shared toolset other users may still hold (MCP-05).
            logger.warning("MCP release without acquire: %s", handle.name)
            return
        handle.ref_count -= 1
        if handle.ref_count == 0:
            await self._close_toolset(handle)

    async def _close_toolset(self, handle: ServerHandle) -> None:
        if handle.toolset is None:
            return
        # MA-03: the server's tools leave the agents' surfaces on close.
        try:
            await self._detach_tools(handle)
        except Exception:  # noqa: BLE001
            logger.exception("MCP tool detach failed for %s", handle.name)
        try:
            await handle.toolset.close()
        except Exception:  # noqa: BLE001
            logger.exception("error closing MCP toolset %s", handle.name)
        handle.toolset = None
        handle.state = ServerState.DISCONNECTED

    # -- readiness ---------------------------------------------------------------

    def readiness(self) -> bool:
        """MCP-02: /readyz 503 while any required server is disconnected."""
        return all(not h.required or h.connected for h in self._handles.values())

    def health(self) -> list[dict[str, Any]]:
        return [
            {
                "name": h.name,
                "transport": h.transport,
                "status": h.state.value,
                "tools": len(h.final_names),
                "lastError": h.last_error,
            }
            for h in self._handles.values()
        ]

    # -- reconciler ----------------------------------------------------------------

    async def _reconcile_loop(self, name: str) -> None:
        while not self._closed:
            handle = self._handles.get(name)
            if handle is None:
                return
            if handle.connected:
                # R-11: a CONNECTED handle is not blindly trusted — sleep on
                # the liveness cadence (not a 1 s flag poll) and probe;
                # a dead-but-connected session flips back to DISCONNECTED so
                # the next tick reconnects and readiness reflects the loss.
                await asyncio.sleep(_LIVENESS_PROBE_SECONDS)
                if not await self._probe(handle):
                    handle.state = ServerState.DISCONNECTED
                    handle.last_error = "liveness probe failed"
                    handle.backoff_seconds = min(
                        handle.backoff_seconds * 2, BACKOFF_CAP_SECONDS
                    ) + random.uniform(0, JITTER_MAX_SECONDS)
                    await self._detach_tools(handle)
                continue
            await self._connect(handle)
            await asyncio.sleep(min(handle.backoff_seconds, BACKOFF_CAP_SECONDS))

    async def _probe(self, handle: ServerHandle) -> bool:
        """R-11: liveness probe — a dead-but-connected session raises here
        (or leaves no live session) and gets reconnected; the flag alone
        could never move CONNECTED back."""
        try:
            if handle.toolset is None:
                return True
            # A clean close leaves the session manager with no live
            # sessions; a dead transport raises on the next call.
            mgr = getattr(handle.toolset, "_mcp_session_manager", None)
            sessions = getattr(mgr, "_sessions", None)
            if sessions is not None and not sessions:
                return False
            await handle.toolset.list_resources()
            return True
        except Exception:  # noqa: BLE001 — probe failures are expected
            return False

    async def _connect(self, handle: ServerHandle) -> None:
        handle.state = ServerState.CONNECTING
        try:
            params = self._build_params(handle)
            toolset = McpToolset(connection_params=params)
            tools = await toolset.get_tools()
            # MCP-03: filter, then compute the GLOBAL collision-safe final
            # names across all servers (deterministic in configured order).
            raw_names = [t.name for t in tools]
            filtered = apply_tool_filter(
                raw_names, handle.tool_filter_allow, handle.tool_filter_deny
            )
            # R-11: enforce maxTools at connect — truncate + warn (an
            # over-limit server still connects with its first N tools).
            if len(filtered) > handle.max_tools:
                logger.warning(
                    "MCP server %s exposes %d tools after filtering; "
                    "maxTools=%d caps the attached set",
                    handle.name,
                    len(filtered),
                    handle.max_tools,
                )
                filtered = filtered[: handle.max_tools]
            handle.tools = tools
            handle.raw_names = raw_names
            handle.filtered_names = filtered
            handle.toolset = toolset
            handle.state = ServerState.CONNECTED
            handle.last_error = None
            handle.backoff_seconds = 1.0  # reset on success
            logger.info(
                "MCP server %s connected (%d tools)", handle.name, len(handle.filtered_names)
            )
            # MA-03: recompute the GLOBAL final names + attach under the lock
            # (best-effort; a failed attach must not drop the connection).
            try:
                await self._attach_tools(handle)
            except Exception:  # noqa: BLE001
                logger.exception("MCP tool attach failed for %s", handle.name)
        except Exception as exc:  # noqa: BLE001
            handle.state = ServerState.DISCONNECTED
            handle.last_error = str(exc)[:200]
            handle.backoff_seconds = min(
                handle.backoff_seconds * 2, BACKOFF_CAP_SECONDS
            ) + random.uniform(0, JITTER_MAX_SECONDS)
            logger.warning(
                "MCP server %s connect failed (backoff %.1fs): %s",
                handle.name,
                handle.backoff_seconds,
                handle.last_error,
            )

    def _build_params(self, handle: ServerHandle) -> Any:
        server = self._find_config(handle.name)
        if handle.transport == "stdio":
            params, unresolved = build_stdio_params(server.command, server.args, dict(server.env))
            if unresolved:
                raise ValueError(
                    f"unresolved env references for stdio server {handle.name}: "
                    + ", ".join(unresolved)
                )
            return wrap_stdio_params(params)
        if handle.transport in ("sse", "streamable-http"):
            from google.adk.tools.mcp_tool.mcp_session_manager import (
                SseConnectionParams,
                StreamableHTTPConnectionParams,
            )

            base_factory = _default_httpx_factory()
            factory = bounded_httpx_client_factory(handle.max_transport_message_bytes, base_factory)
            if handle.transport == "sse":
                return SseConnectionParams(
                    url=server.url,
                    headers=dict(server.headers),
                    httpx_client_factory=factory,
                )
            return StreamableHTTPConnectionParams(
                url=server.url,
                headers=dict(server.headers),
                httpx_client_factory=factory,
            )
        raise ValueError(f"unsupported MCP transport {handle.transport!r}")

    def _find_config(self, name: str) -> Any:
        config = self._applied.config
        for server in config.tools.mcpServers:
            if server.name == name:
                return server
        raise KeyError(f"MCP server {name!r} not in config")


def _default_httpx_factory() -> Any:
    from google.adk.tools.mcp_tool.mcp_session_manager import (
        create_mcp_http_client,
    )

    return create_mcp_http_client
