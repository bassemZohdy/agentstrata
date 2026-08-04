"""CNT-07 graceful-shutdown tests.

Unit tests drive the ``ShutdownManager`` state machine with fake components
(no real signals, no ``os._exit``). Integration tests toggle the draining flag
through the real FastAPI app to verify ``/readyz`` 503, new chat 503, and
``/healthz`` staying 200.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.lifecycle import ShutdownManager
from app.protocol.app import create_app

from .conftest import build_components, make_config


class _FakeStop:
    """Async stop() (watcher shape) that records call order."""

    def __init__(self, name: str, log: list[str]) -> None:
        self.name = name
        self.log = log
        self.stopped = False

    async def stop(self) -> None:
        self.log.append(self.name)
        self.stopped = True


class _FakeClosable:
    """Async close() that records call order."""

    def __init__(self, name: str, log: list[str], *, raises: bool = False) -> None:
        self.name = name
        self.log = log
        self.raises = raises
        self.closed = False

    async def close(self) -> None:
        self.log.append(self.name)
        self.closed = True
        if self.raises:
            raise RuntimeError("boom")


class _FakeServer:
    def __init__(self) -> None:
        self.should_exit = False


class TestShutdownManagerStateMachine:
    async def test_grace_expiry_cancels_inflight_runs_before_close(self):
        order: list[str] = []

        async def held_run():
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                order.append("run_cancelled")
                raise

        task = asyncio.create_task(held_run())
        components = {
            "run_registry": {task},
            "watcher": _FakeStop("watcher", order),
            "mcp": _FakeClosable("mcp", order),
            "backend": _FakeClosable("storage", order),
            "observability": SimpleNamespace(shutdown=lambda: order.append("otel")),
        }
        server = _FakeServer()
        mgr = ShutdownManager(components, grace_seconds=0, server=server)

        await mgr.request_shutdown()
        assert mgr._drain_task is not None
        await mgr._drain_task

        # CNT-07: the in-flight run was cancelled and its teardown ran BEFORE
        # any component closed (so terminal states persist while storage is
        # still open).
        assert task.cancelled()
        assert order == ["run_cancelled", "watcher", "mcp", "storage", "otel"]

    async def test_first_signal_drains_and_closes_components_in_order(self):
        order: list[str] = []
        components = {
            "watcher": _FakeStop("watcher", order),
            "mcp": _FakeClosable("mcp", order),
            "backend": _FakeClosable("storage", order),
            "observability": SimpleNamespace(shutdown=lambda: order.append("otel")),
        }
        server = _FakeServer()
        mgr = ShutdownManager(components, grace_seconds=0, server=server)

        assert not mgr.is_draining()
        await mgr.request_shutdown()

        # Draining is set synchronously before the grace wait.
        assert mgr.is_draining()
        # Wait for the drain task to flush + stop the listener.
        assert mgr._drain_task is not None
        await mgr._drain_task
        assert mgr.closed
        assert mgr.close_ok is True
        assert server.should_exit is True
        # CNT-07 close order: watcher -> mcp -> storage -> otel.
        assert order == ["watcher", "mcp", "storage", "otel"]

    async def test_second_signal_hard_exits_1(self, monkeypatch):
        exited: list[int] = []
        mgr = ShutdownManager({}, grace_seconds=10, server=_FakeServer())
        monkeypatch.setattr(mgr, "_hard_exit", exited.append)

        await mgr.request_shutdown()
        await mgr.request_shutdown()

        assert exited == [1]

    async def test_failed_close_marks_exit_code_1(self):
        components = {
            "backend": _FakeClosable("storage", [], raises=True),
        }
        server = _FakeServer()
        mgr = ShutdownManager(components, grace_seconds=0, server=server)
        await mgr.request_shutdown()
        assert mgr._drain_task is not None
        await mgr._drain_task

        assert mgr.closed
        assert mgr.close_ok is False  # -> exit 1
        # The listener is still stopped after a best-effort close.
        assert server.should_exit is True

    async def test_second_signal_while_already_draining_forces_exit(self, monkeypatch):
        exited: list[int] = []
        mgr = ShutdownManager({}, grace_seconds=10, server=_FakeServer())
        monkeypatch.setattr(mgr, "_hard_exit", exited.append)
        # First signal already arms a 10s grace timer; do not wait for it.
        await mgr.request_shutdown()
        assert mgr.is_draining()
        await mgr.request_shutdown()
        assert exited == [1]
        assert mgr._drain_task is not None
        mgr._drain_task.cancel()


class _App:
    """Build a real app with a ShutdownManager installed and not yet draining."""

    def __init__(self) -> None:
        self.config = make_config()
        self.components = build_components(self.config)
        self.mgr = ShutdownManager(self.components, grace_seconds=25)
        self.components["shutdown"] = self.mgr


@pytest.fixture()
def draining_app():
    ctx = _App()
    ctx.mgr.draining = True  # enter drain without the async state machine
    return ctx


class TestDrainingRouteWiring:
    def test_readyz_returns_503_draining(self, draining_app):
        from fastapi.testclient import TestClient

        with TestClient(create_app(draining_app.config, draining_app.components)) as c:
            r = c.get("/readyz")
            assert r.status_code == 503
            assert r.json()["status"] == "draining"

    def test_healthz_stays_200_while_draining(self, draining_app):
        from fastapi.testclient import TestClient

        with TestClient(create_app(draining_app.config, draining_app.components)) as c:
            assert c.get("/healthz").status_code == 200

    def test_new_chat_rejected_503_while_draining(self, draining_app):
        from fastapi.testclient import TestClient

        with TestClient(create_app(draining_app.config, draining_app.components)) as c:
            r = c.post(
                "/v1/chat/completions",
                json={"model": "mock", "messages": [{"role": "user", "content": "hi"}]},
            )
            assert r.status_code == 503
            assert r.json()["error"]["code"] == "service_unavailable"

    def test_not_draining_serves_normally(self):
        from fastapi.testclient import TestClient

        ctx = _App()  # draining stays False
        with TestClient(create_app(ctx.config, ctx.components)) as c:
            assert c.get("/readyz").status_code == 200
            r = c.post(
                "/v1/chat/completions",
                json={"model": "mock", "messages": [{"role": "user", "content": "hi"}]},
            )
            assert r.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
