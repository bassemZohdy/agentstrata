"""CNT-07 graceful shutdown lifecycle.

Owns the drain/cancel/flush/close sequence driven by SIGTERM/SIGINT:

1. First signal atomically enters draining: readiness fails (``/readyz`` 503),
   new chat runs are rejected (503), in-flight runs keep their deadline up to
   ``server.shutdownGraceSeconds``, and ``/healthz`` stays live.
2. At grace expiry: cancel in-flight runs, then close components in dependency
   order — watcher (stops the reload loop), MCP reconcilers, storage flush,
   OTel flush — and stop the listener (``server.should_exit``).
3. Exit 0 only if every required flush/close succeeded; otherwise exit 1.
4. A second signal immediately hard-exits 1.

The signal wiring is Linux/production (``loop.add_signal_handler``); the
Windows path falls back to ``signal.signal`` for dev only. The manager is
fully testable without real signals by calling ``request_shutdown`` directly.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from contextlib import suppress
from typing import Any

logger = logging.getLogger(__name__)


class ShutdownManager:
    """CNT-07 graceful-shutdown coordinator.

    A single instance lives in ``components["shutdown"]`` and is read by the
    readiness/chat routes (``is_draining``) and driven by the signal handlers
    (``request_shutdown``).
    """

    def __init__(self, components: dict[str, Any], grace_seconds: int, server: Any = None) -> None:
        self.components = components
        self.grace_seconds = grace_seconds
        self.server = server
        self.draining: bool = False
        self.closed: bool = False
        self.close_ok: bool = True
        self._signals = 0
        self._drain_task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._installed = False

    # -- public state -------------------------------------------------------

    def is_draining(self) -> bool:
        return self.draining

    # -- signal wiring ------------------------------------------------------

    def install_signal_handlers(self) -> None:
        """Register SIGTERM/SIGINT. Idempotent. Linux uses
        ``loop.add_signal_handler``; Windows dev falls back to ``signal.signal``."""
        if self._installed:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        self._loop = loop
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._schedule_request)
            except (NotImplementedError, RuntimeError):
                # Windows ProactorEventLoop / non-main thread: best-effort fallback.
                with suppress(OSError, ValueError):
                    signal.signal(sig, self._on_signal_win)
        self._installed = True

    def _schedule_request(self) -> None:
        if self._loop is not None and self._loop.is_running():
            self._loop.create_task(self.request_shutdown())

    def _on_signal_win(self, _signum: int, _frame: Any) -> None:
        self._schedule_request()

    # -- shutdown state machine --------------------------------------------

    async def request_shutdown(self) -> None:
        """First call: enter draining + arm the grace timer. Second call:
        hard-exit 1 immediately (CNT-07)."""
        self._signals += 1
        if self._signals >= 2:
            logger.warning("shutdown_forced second signal -> exit 1")
            self._hard_exit(1)
            return
        if self.draining:
            return
        self.draining = True
        logger.info("shutdown_draining grace_seconds=%d", self.grace_seconds)
        try:
            from .security.audit import audit

            audit("shutdown_draining", grace_seconds=self.grace_seconds)
        except Exception:  # noqa: BLE001
            pass
        self._drain_task = asyncio.create_task(self._drain_after_grace())

    async def _drain_after_grace(self) -> None:
        # R-12: instead of sleeping the full grace, wait for the in-flight
        # runs themselves (bounded by the grace timeout) — when every run
        # finishes early the drain proceeds immediately, shortening pod
        # termination.  On timeout the runs are cancelled below, as before.
        registry = self.components.get("run_registry")
        pending = [t for t in list(registry or ()) if not t.done()]
        try:
            if pending:
                await asyncio.wait_for(
                    asyncio.wait(pending, return_when=asyncio.ALL_COMPLETED),
                    timeout=self.grace_seconds,
                )
            else:
                await asyncio.sleep(self.grace_seconds)
        except (TimeoutError, asyncio.CancelledError):
            pass
        # CNT-07: cancel in-flight runs FIRST so the runner persists terminal
        # states/usage while storage is still open.
        started = time.monotonic()
        await self._cancel_inflight_runs()
        self.close_ok, failures = await self.close_components()
        self.closed = True
        if self.server is not None:
            self.server.should_exit = True  # type: ignore[attr-defined]
        exit_code = 0 if self.close_ok else 1
        try:
            from .security.audit import audit

            audit("shutdown_complete", exit_code=exit_code, close_ok=self.close_ok)
        except Exception:  # noqa: BLE001
            pass
        # Structured summary: duration + per-component failure detail in ONE
        # line (the audit event carries the machine-readable record).
        duration_ms = round((time.monotonic() - started) * 1000, 1)
        summary = (
            f"shutdown_summary exit_code={exit_code} duration_ms={duration_ms} "
            f"failed_components={failures or 'none'}"
        )
        if exit_code != 0:
            # A flush/close step failed: surface it before the process exits.
            logger.error("shutdown_complete_with_errors %s", summary)
        else:
            logger.info("%s", summary)

    async def _cancel_inflight_runs(self) -> None:
        """CNT-07: cancel admitted runs at grace expiry and await their
        terminal-state commit (the runner's CancelledError path) before
        storage closes."""
        registry = self.components.get("run_registry")
        if not registry:
            return
        tasks = [t for t in list(registry) if not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def close_components(self) -> tuple[bool, list[str]]:
        """CNT-07 close order: watcher (stops reload loop) -> MCP reconcilers
        -> storage flush -> OTel flush. Best-effort per component; returns
        (ok, failed_component_labels) — the caller logs the structured
        summary."""
        ok = True
        failures: list[str] = []

        async def _safe(coro: Any, label: str) -> None:
            nonlocal ok
            try:
                result = coro
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:  # noqa: BLE001
                ok = False
                failures.append(label)
                logger.warning("shutdown_close_failed component=%s err=%s", label, exc)

        # 0. R-12: cancel the lifespan background tasks FIRST so nothing
        # keeps running against components being closed (the approval
        # reconciler and storage sweep would otherwise touch a backend that
        # is mid-close; the watcher loop would keep polling).
        for label in ("reconcile_task", "sweep_task", "watcher_task"):
            task = self.components.get(label)
            if isinstance(task, asyncio.Task) and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

        # 1. Stop the watcher so no more tier-8 reloads race the teardown.
        watcher = self.components.get("watcher")
        if watcher is not None and hasattr(watcher, "stop"):
            await _safe(watcher.stop(), "watcher")
        # 2. MCP reconcilers (closes stdio processes / HTTP sessions).
        mcp = self.components.get("mcp")
        if mcp is not None and hasattr(mcp, "close"):
            await _safe(mcp.close(), "mcp")
        # 3. Storage flush + close (run records / sessions persisted).
        backend = self.components.get("backend")
        if backend is not None and hasattr(backend, "close"):
            await _safe(backend.close(), "storage")
        # 4. OTel flush (sync).
        obs = self.components.get("observability")
        if obs is not None and hasattr(obs, "shutdown"):
            try:
                obs.shutdown()
            except Exception as exc:  # noqa: BLE001
                ok = False
                logger.warning("shutdown_close_failed component=otel err=%s", exc)
                failures.append("otel")
        return ok, failures

    def _hard_exit(self, code: int) -> None:
        """Immediately terminate the process (second signal). Overridable in tests."""
        os._exit(code)


class ManagedServer:
    """Thin wrapper over ``uvicorn.Server`` that routes SIGTERM/SIGINT into the
    ``ShutdownManager`` (CNT-07) instead of uvicorn's default stop-on-first-signal
    behavior. Built lazily so the uvicorn import stays out of the config/boot path."""

    def __init__(self, config: Any, shutdown_manager: ShutdownManager) -> None:
        import uvicorn

        self._server = uvicorn.Server(config)
        self._shutdown_manager = shutdown_manager
        # uvicorn's own graceful-shutdown timeout must not preempt the drain
        # timer; leave it to the ShutdownManager to set should_exit. uvicorn's
        # Server.install_signal_handlers is a public method but not in its type
        # stubs (mypy attr-defined; ruff B010 rejects setattr, so direct assign).
        self._server.install_signal_handlers = self._install_managed_handlers  # type: ignore[attr-defined]

    def _install_managed_handlers(self) -> None:
        self._shutdown_manager.install_signal_handlers()

    @property
    def should_exit(self) -> bool:
        return self._server.should_exit

    def run(self) -> None:
        self._server.run()
