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

    async def test_early_drain_when_runs_finish_before_grace(self):
        """R-12: the drain waits on the in-flight runs bounded by the grace
        timeout instead of sleeping the whole grace — a fleet that finishes
        early shortens pod termination."""
        order: list[str] = []

        async def quick_run():
            await asyncio.sleep(0.05)

        task = asyncio.create_task(quick_run())
        components = {
            "run_registry": {task},
            "watcher": _FakeStop("watcher", order),
            "mcp": _FakeClosable("mcp", order),
            "backend": _FakeClosable("storage", order),
            "observability": SimpleNamespace(shutdown=lambda: order.append("otel")),
        }
        mgr = ShutdownManager(components, grace_seconds=30, server=_FakeServer())
        await mgr.request_shutdown()
        assert mgr._drain_task is not None
        loop = asyncio.get_running_loop()
        started = loop.time()
        await asyncio.wait_for(mgr._drain_task, timeout=2.0)
        elapsed = loop.time() - started
        assert mgr.closed
        assert elapsed < 2.0  # did NOT sleep the full 30 s grace
        assert order == ["watcher", "mcp", "storage", "otel"]

    async def test_background_tasks_cancelled_before_components_close(self):
        """R-12: the lifespan background tasks (approval reconciler, storage
        sweep, watcher) are cancelled first so nothing touches components
        that are mid-close."""
        order: list[str] = []

        async def looper(name: str):
            try:
                while True:
                    await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                order.append(name)
                raise

        tasks = {
            name: asyncio.create_task(looper(name))
            for name in ("reconcile_task", "sweep_task", "watcher_task")
        }
        components = {
            **tasks,
            "watcher": _FakeStop("watcher", order),
            "backend": _FakeClosable("storage", order),
            "observability": SimpleNamespace(shutdown=lambda: order.append("otel")),
        }
        mgr = ShutdownManager(components, grace_seconds=0, server=_FakeServer())
        await mgr.request_shutdown()
        assert mgr._drain_task is not None
        await mgr._drain_task
        assert all(t.cancelled() for t in tasks.values())
        assert order == [
            "reconcile_task",
            "sweep_task",
            "watcher_task",
            "watcher",
            "storage",
            "otel",
        ]

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


class TestStorageSweepScheduling:
    @pytest.mark.asyncio
    async def test_startup_sweep_reconciles_stale_run_and_reports_metric(self):
        """R-04: the lifespan starts the storage sweep; the startup pass
        reconciles a stale nonterminal run and records the OBS-05 counter.
        (Driven through ``_lifespan`` directly so seeding and the sweep
        share one event loop.)"""
        from datetime import timedelta

        from app.config.models import AgentConfig
        from app.observability.otel import Observability
        from app.protocol.app import _lifespan
        from app.storage.memory import MemoryBackend
        from app.storage.model import utcnow

        config = make_config()
        doc = config.model_dump(by_alias=True, mode="json")
        doc["storage"] = {"sweepIntervalSeconds": 60}
        doc["observability"] = {"prometheus": {"enabled": True}}
        config = AgentConfig.model_validate(doc)
        obs = Observability(config)
        components = build_components(config, obs)
        backend = components["backend"]
        assert isinstance(backend, MemoryBackend)

        # Seed a stale nonterminal run (created before runTtl).
        stale = utcnow() - timedelta(seconds=config.storage.runTtlSeconds + 60)
        await backend.create_session(
            agent_name="agent", principal_id="p1", session_id="sid", now=stale
        )
        await backend.create_run(
            agent_name="agent",
            principal_id="p1",
            session_id="sid",
            run_id="r1",
            run_input={},
            now=stale,
        )

        app = create_app(config, components, mode="standalone")
        async with _lifespan(app, components, config):
            runs = list(backend._runs.values())
            assert runs and runs[0].status == "failed"
            assert runs[0].outcome == {"error_code": "run_interrupted"}
            text = components["observability"].registry.render()
            assert 'agentbase_storage_sweeps_total{kind="interrupted"} 1' in text


class TestShutdownSummaryAudit:
    async def test_summary_line_reports_duration_and_failures(self, caplog):
        """The structured shutdown summary logs duration + per-component
        failure detail in one line."""
        import logging

        from app.lifecycle import ShutdownManager

        class _FailingBackend:
            async def close(self):
                raise RuntimeError("flush failed")

        components = {"backend": _FailingBackend()}
        mgr = ShutdownManager(components, 0)
        with caplog.at_level(logging.ERROR, logger="app.lifecycle"):
            mgr.close_ok, failures = await mgr.close_components()
        assert mgr.close_ok is False
        assert failures == ["storage"]
        assert any("shutdown_summary" in r.message for r in caplog.records) is False
        # the summary itself is emitted by _drain_after_grace; verify the
        # failure detail surfaces there
        await mgr.request_shutdown()
        assert mgr._drain_task is not None
        with caplog.at_level(logging.ERROR, logger="app.lifecycle"):
            await mgr._drain_task
        assert any(
            "shutdown_complete_with_errors" in r.message
            and "failed_components=['storage']" in r.message
            and "duration_ms=" in r.message
            for r in caplog.records
        )
