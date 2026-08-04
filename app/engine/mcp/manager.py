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
from .filtering import apply_tool_filter, rename_collision_safe
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

logger = logging.getLogger(__name__)

BACKOFF_CAP_SECONDS = 60.0
JITTER_MAX_SECONDS = 0.25


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
    final_names: list[str] = field(default_factory=list)
    backoff_seconds: float = 1.0
    last_error: str | None = None
    ref_count: int = 0
    _conn_retry: int = 0

    @property
    def connected(self) -> bool:
        return self.state == ServerState.CONNECTED


class ServerManager:
    """MCP-05 lifecycle manager + MCP-01 reconcilers + MCP-02 readiness."""

    def __init__(self, applied: AppliedConfig) -> None:
        self._applied = applied
        self._handles: dict[str, ServerHandle] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._closed = False
        self._started = False

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

    def handles(self) -> list[ServerHandle]:
        return list(self._handles.values())

    def handle(self, name: str) -> ServerHandle | None:
        return self._handles.get(name)

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
            if not handle.connected:
                await self._connect(handle)
            await asyncio.sleep(min(handle.backoff_seconds, BACKOFF_CAP_SECONDS))

    async def _connect(self, handle: ServerHandle) -> None:
        handle.state = ServerState.CONNECTING
        try:
            params = self._build_params(handle)
            toolset = McpToolset(connection_params=params)
            tools = await toolset.get_tools()
            # MCP-03: filter + collision-safe rename
            raw_names = [t.name for t in tools]
            filtered = apply_tool_filter(
                raw_names, handle.tool_filter_allow, handle.tool_filter_deny
            )
            final = rename_collision_safe(filtered, handle.name)
            handle.tools = tools
            handle.raw_names = raw_names
            handle.final_names = final
            handle.toolset = toolset
            handle.state = ServerState.CONNECTED
            handle.last_error = None
            handle.backoff_seconds = 1.0  # reset on success
            logger.info("MCP server %s connected (%d tools)", handle.name, len(final))
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
            return params
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
