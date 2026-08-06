"""Watcher + reload tests (REL-01..06, K8S-01..09)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config.models import AgentConfig
from app.engine.agent import AppliedConfig
from app.watcher.reload import ReloadManager, changed_paths, classify_change
from app.watcher.watcher import ConfigMapWatcher, FakeKubeClient, _extract_overlay

REPO_CONFIG = str(Path(__file__).resolve().parents[2] / "config")


def _resolved_config(agents=None):
    import json as _json

    from app.config.resolver import resolve
    from app.config.validate import validate_resolution

    env = {}
    if agents:
        env["AGENT_APPLICATION_JSON"] = _json.dumps({"agents": agents})
    res = resolve(env=env, bundled_dir=REPO_CONFIG, argv=[])
    result = validate_resolution(res)
    assert result.ok
    assert result.config is not None
    return result.config


def _config(**overrides) -> AgentConfig:
    doc = {
        "name": "agent",
        "engine": {"systemInstruction": "t", "temperature": 0.7},
        "llm": {"provider": "gemini", "model": "mock"},
    }
    doc.update(overrides)
    return AgentConfig.model_validate(doc)


def _build_components(config, generation):
    from app.storage.memory import MemoryBackend

    backend = MemoryBackend()
    return {
        "applied": AppliedConfig.from_config(config, generation),
        "backend": backend,
        "generation": generation,
    }


class TestClassification:
    def test_live_snapshot(self):
        assert classify_change(["engine.maxIterations"]) == "live_snapshot"

    def test_component_rebuild(self):
        assert classify_change(["llm.model"]) == "component_rebuild"
        # RAG-05: any store/embedding/chunk-identity change is a component
        # rebuild (no silent re-embed of old documents).
        assert classify_change(["rag.store.collection"]) == "component_rebuild"
        assert classify_change(["rag.embedding.model"]) == "component_rebuild"
        assert classify_change(["rag.chunkChars"]) == "component_rebuild"
        assert classify_change(["rag.topK"]) == "component_rebuild"
        # REL-02: the cost table is baked into the immutable AppliedConfig,
        # so a costs change must rebuild components.
        assert classify_change(["costs.enabled"]) == "component_rebuild"
        assert classify_change(["costs.models"]) == "component_rebuild"

    def test_restart_required_wins(self):
        assert classify_change(["llm.model", "server.port"]) == "restart_required"

    def test_changed_paths_sorted(self):
        old = {"a": 1, "b": 2}
        new = {"a": 2, "c": 3}
        # deletions and additions are both leaf changes, sorted
        assert changed_paths(old, new) == ["a", "b", "c"]


class TestReloadManager:
    @pytest.mark.asyncio
    async def test_noop_does_not_increment(self):
        config = _resolved_config()
        manager = ReloadManager(
            _build_components, config, _build_components(config, 1), bundled_dir=REPO_CONFIG
        )
        overlay = {"engine": {"temperature": 0.7}}  # same as current
        result = await manager.apply_tier8(overlay)
        assert result.outcome == "noop"
        assert manager.generation == 1

    @pytest.mark.asyncio
    async def test_live_snapshot_increments(self):
        config = _resolved_config()
        manager = ReloadManager(
            _build_components, config, _build_components(config, 1), bundled_dir=REPO_CONFIG
        )
        result = await manager.apply_tier8({"engine": {"maxIterations": 42}})
        assert result.outcome == "applied_live"
        assert manager.generation == 2

    @pytest.mark.asyncio
    async def test_restart_required_rejected(self):
        config = _resolved_config()
        manager = ReloadManager(
            _build_components, config, _build_components(config, 1), bundled_dir=REPO_CONFIG
        )
        result = await manager.apply_tier8({"server": {"port": 9999}})
        assert result.outcome == "restart_required"
        assert manager.generation == 1

    @pytest.mark.asyncio
    async def test_invalid_overlay_rejected(self):
        config = _resolved_config()
        manager = ReloadManager(
            _build_components, config, _build_components(config, 1), bundled_dir=REPO_CONFIG
        )
        result = await manager.apply_tier8({"engine": {"temperature": "not-a-float"}})
        assert result.outcome == "rejected"
        assert manager.generation == 1

    @pytest.mark.asyncio
    async def test_component_rebuild_swaps(self):
        config = _resolved_config()
        manager = ReloadManager(
            _build_components, config, _build_components(config, 1), bundled_dir=REPO_CONFIG
        )
        result = await manager.apply_tier8({"llm": {"model": "gpt-5"}})
        assert result.outcome == "applied_rebuild"
        assert manager.generation == 2
        assert manager.components["generation"] == 2

    @pytest.mark.asyncio
    async def test_rebuild_starts_replacement_mcp_manager(self):
        """R-05: the replacement MCP manager produced by a component rebuild
        is started before the swap (a not-started manager would silently drop
        every MCP server)."""
        from google.adk.runners import Runner as AdkRunner

        from app.engine.agent import build_agent_component
        from app.engine.mcp.manager import ServerManager
        from app.engine.runner import AgentRunner
        from app.storage.adk_adapter import AdkSessionService
        from app.storage.memory import MemoryBackend

        def builder(cfg, generation):
            component = build_agent_component(cfg, generation)
            backend = MemoryBackend()
            service = AdkSessionService(backend)
            adk = AdkRunner(
                agent=component.agent, app_name="agent", session_service=service
            )
            applied = AppliedConfig.from_config(cfg, generation)
            runner = AgentRunner(applied, adk, backend, app_name="agent")
            mcp = ServerManager(applied, tool_targets=list(component.tool_targets))
            mcp.configure(cfg.tools.mcpServers)
            return {
                "applied": applied,
                "agent": component,
                "runner": runner,
                "backend": backend,
                "mcp": mcp,
                "generation": generation,
            }

        config = _resolved_config()
        components = builder(config, 1)
        original_mcp = components["mcp"]
        await components["mcp"].start()
        manager = ReloadManager(builder, config, components, bundled_dir=REPO_CONFIG)
        result = await manager.apply_tier8({"engine": {"temperature": 0.3}})
        assert result.outcome == "applied_rebuild"
        assert manager.components["mcp"]._started is True
        assert manager.components["mcp"] is not original_mcp

    @pytest.mark.asyncio
    async def test_audit_logs_true_generation_pair(self, caplog):
        """R-15: the reload audit logs the true before/after generation
        pair (was: post-increment values, e.g. 1->2 logged as 2->3)."""
        import logging

        from app.watcher.reload import logger as reload_logger

        config = _resolved_config()

        async def _run(outcome, overlay):
            manager = ReloadManager(
                _build_components,
                config,
                _build_components(config, 1),
                bundled_dir=REPO_CONFIG,
            )
            with caplog.at_level(logging.INFO, logger=reload_logger.name):
                result = await manager.apply_tier8(overlay)
            assert result.outcome == outcome
            return [
                r.message
                for r in caplog.records
                if r.name == reload_logger.name and "reload outcome=" in r.message
            ]

        caplog.clear()
        live = await _run("applied_live", {"server": {"maxRequestBytes": 2_097_152}})
        assert live and "old_generation=1 new_generation=2" in live[0]
        caplog.clear()
        rebuild = await _run("applied_rebuild", {"llm": {"model": "gpt-5"}})
        assert rebuild and "old_generation=1 new_generation=2" in rebuild[0]
        caplog.clear()
        rejected = await _run("rejected", {"server": {"port": 99999}})
        assert rejected and "old_generation=1 new_generation=1" in rejected[0]

    @pytest.mark.asyncio
    async def test_component_rebuild_preserves_manager_owned_singletons(self):
        """M8 gate regression: the rebuild swap wiped keys that
        build_components does not produce (reload_manager, watcher, shutdown,
        run_slots, run_registry), breaking /health generation reporting, the
        drain gate, and the run cap after the first rebuild."""
        config = _resolved_config()
        components = _build_components(config, 1)
        sentinel = object()
        for key in ("reload_manager", "watcher", "shutdown", "run_slots", "run_registry"):
            components[key] = sentinel
        manager = ReloadManager(_build_components, config, components, bundled_dir=REPO_CONFIG)
        result = await manager.apply_tier8({"llm": {"model": "gpt-5"}})
        assert result.outcome == "applied_rebuild"
        for key in ("reload_manager", "watcher", "shutdown", "run_slots", "run_registry"):
            assert manager.components[key] is sentinel, key
        assert manager.components["generation"] == 2

    @pytest.mark.asyncio
    async def test_config_hash_tracks_generation(self):
        config = _resolved_config()
        manager = ReloadManager(
            _build_components, config, _build_components(config, 1), bundled_dir=REPO_CONFIG
        )
        h1 = manager.config_hash
        await manager.apply_tier8({"engine": {"maxIterations": 42}})
        h2 = manager.config_hash
        assert h1 != h2
        assert len(h2) == 64  # sha256


class TestWatcher:
    @pytest.mark.asyncio
    async def test_overlay_extraction_only_reads_agent_yaml(self):
        cm = {
            "data": {
                "agent.yaml": "engine:\n  maxIterations: 42\n",
                "other.yaml": "ignored: true",
            }
        }
        overlay = _extract_overlay(cm)
        assert overlay == {"engine": {"maxIterations": 42}}

    @pytest.mark.asyncio
    async def test_watcher_missing_configmap_falls_back(self):
        applied: list[dict | None] = []
        client = FakeKubeClient(cm=None)

        async def on_overlay(overlay):
            applied.append(overlay)

        watcher = ConfigMapWatcher(
            client, "default", "cfg", required=False, resync_seconds=1, on_overlay=on_overlay
        )
        task = asyncio.create_task(watcher.run())
        await asyncio.sleep(0.3)
        await watcher.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        # REL-05: deletion/missing -> None overlay (tiers 1-7 fallback)
        assert None in applied
        assert not watcher.health["connected"]

    @pytest.mark.asyncio
    async def test_watcher_applies_overlay_on_start(self):
        applied: list[dict | None] = []
        client = FakeKubeClient(
            cm={
                "resourceVersion": "5",
                "uid": "uid-1",
                "data": {"agent.yaml": "engine:\n  maxIterations: 42\n"},
            }
        )

        async def on_overlay(overlay):
            applied.append(overlay)

        watcher = ConfigMapWatcher(
            client, "default", "cfg", required=True, resync_seconds=1, on_overlay=on_overlay
        )
        task = asyncio.create_task(watcher.run())
        await asyncio.sleep(0.3)
        await watcher.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert applied and applied[0] == {"engine": {"maxIterations": 42}}
        assert watcher.health["connected"]


def test_production_wiring_starts_the_watcher_on_app_startup():
    """M8 gate regression: main.py constructs the ConfigMapWatcher but never
    called run(); the app startup hook must start the watch loop (the tier-8
    reload path was dead in production)."""
    from fastapi.testclient import TestClient

    from app.protocol.app import create_app

    started = asyncio.Event()

    class RecordingWatcher:
        async def run(self) -> None:
            started.set()
            # Stay alive until stopped (like the real watch loop).
            while True:
                await asyncio.sleep(1)

        async def stop(self) -> None:
            return None

    config = AgentConfig.model_validate(
        {
            "name": "agent",
            "engine": {"systemInstruction": "t"},
            "llm": {"provider": "gemini", "model": "mock"},
        }
    )
    from app.storage.memory import MemoryBackend

    components = {
        "applied": AppliedConfig.from_config(config),
        "backend": MemoryBackend(),
        "mcp": SimpleNamespace(readiness=lambda: True, health=lambda: []),
        "watcher": RecordingWatcher(),
    }
    app = create_app(config, components, mode="watcher")
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        # The startup hook must have launched the watch loop.
        assert started.is_set(), "watcher.run() was never started"
    # The task reference is retained (no GC collection of a pending task).
    assert "watcher_task" in components


def test_production_wiring_starts_mcp_reconcilers_on_app_startup():
    """M8 gate regression: main.py configures the ServerManager but never
    calls start(), so the per-server reconcilers (MCP-01 connect/reconnect)
    never ran in production and every MCP server stayed disconnected."""
    from fastapi.testclient import TestClient

    from app.protocol.app import create_app

    started = asyncio.Event()

    class RecordingMcp:
        async def start(self) -> None:
            started.set()

        def readiness(self) -> bool:
            return True

        def health(self) -> list:
            return []

    config = AgentConfig.model_validate(
        {
            "name": "agent",
            "engine": {"systemInstruction": "t"},
            "llm": {"provider": "gemini", "model": "mock"},
        }
    )
    from app.storage.memory import MemoryBackend

    components = {
        "applied": AppliedConfig.from_config(config),
        "backend": MemoryBackend(),
        "mcp": RecordingMcp(),
    }
    app = create_app(config, components, mode="standalone")
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert started.is_set(), "mcp.start() was never called"


def test_health_marker_written_on_app_startup(monkeypatch, tmp_path):
    """CNT-10 regression: the bound-port marker (/tmp/agentbase.ready by
    default) must be written once the listener binds — without it the
    container HEALTHCHECK fails forever."""
    from fastapi.testclient import TestClient

    from app.protocol.app import create_app

    marker = tmp_path / "agentbase.ready"
    monkeypatch.setenv("AGENT_HEALTH_MARKER", str(marker))
    config = AgentConfig.model_validate(
        {
            "name": "agent",
            "engine": {"systemInstruction": "t"},
            "llm": {"provider": "gemini", "model": "mock"},
            "server": {"port": 8080},
        }
    )
    from app.storage.memory import MemoryBackend

    components = {
        "applied": AppliedConfig.from_config(config),
        "backend": MemoryBackend(),
        "mcp": SimpleNamespace(readiness=lambda: True, health=lambda: []),
    }
    app = create_app(config, components, mode="standalone")
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert marker.is_file(), "health marker was never written"
        assert marker.read_text() == "8080", marker.read_text()


class TestMultiAgentReloadMA05:
    @pytest.mark.asyncio
    async def test_agents_change_is_component_rebuild(self):
        """MA-05: a change to agents[] is a component rebuild (transactional
        apply with rollback on failure), not a live snapshot."""
        from app.engine.agent import AppliedConfig

        config = _resolved_config()

        def builder(cfg, generation):
            from app.storage.memory import MemoryBackend

            return {
                "applied": AppliedConfig.from_config(cfg, generation),
                "backend": MemoryBackend(),
                "generation": generation,
            }

        manager = ReloadManager(builder, config, builder(config, 1), bundled_dir=REPO_CONFIG)
        result = await manager.apply_tier8(
            {"agents": [{"name": "worker", "systemInstruction": "w"}]}
        )
        assert result.outcome == "applied_rebuild"
        assert manager.generation == 2
        assert manager.components["generation"] == 2
        # an invalid agents change rolls back (no generation advance)
        before = manager.generation
        failed = await manager.apply_tier8(
            {"agents": [{"name": "Bad_Name", "systemInstruction": "x"}]}
        )
        assert failed.outcome == "rejected"
        assert manager.generation == before

    @pytest.mark.asyncio
    async def test_rebuild_replaces_runner_with_sub_agents(self):
        """MA-05: after the rebuild, the live runner carries the new agent
        tree (sub-agents included)."""
        from app.engine.agent import AppliedConfig, build_agent_component
        from app.storage.memory import MemoryBackend

        config = _resolved_config()

        def builder(cfg, generation):
            component = build_agent_component(cfg, generation)
            backend = MemoryBackend()
            return {
                "applied": AppliedConfig.from_config(cfg, generation),
                "agent": component,
                "backend": backend,
                "generation": generation,
            }

        manager = ReloadManager(builder, config, builder(config, 1), bundled_dir=REPO_CONFIG)
        result = await manager.apply_tier8(
            {
                "agents": [
                    {"name": "worker", "systemInstruction": "w"},
                    {"name": "helper", "systemInstruction": "h"},
                ]
            }
        )
        assert result.outcome == "applied_rebuild"
        component = manager.components["agent"]
        assert [a.name for a in component.sub_agents] == ["worker", "helper"]
        assert [a.name for a in component.agent.sub_agents] == ["worker", "helper"]


class TestReloadWithInFlightRunsMA05:
    @pytest.mark.asyncio
    async def test_rebuild_during_inflight_run_is_safe(self):
        """MA-05: a component rebuild while a run is in flight is safe — the
        in-flight run finishes against its original generation and the new
        runner serves subsequent requests."""
        import asyncio

        from google.adk.models import BaseLlm
        from google.adk.models.llm_response import LlmResponse
        from google.adk.runners import Runner as AdkRunner
        from google.genai import types

        from app.engine.agent import AppliedConfig, build_agent_component
        from app.engine.runner import AgentRunner, RunRequest
        from app.storage.adk_adapter import AdkSessionService
        from app.storage.memory import MemoryBackend

        class HeldLlm(BaseLlm):
            model: str = "mock"
            gate: asyncio.Event | None = None
            text: str = "old-gen"

            async def generate_content_async(self, llm_request, stream: bool = False):
                assert self.gate is not None
                await self.gate.wait()
                yield LlmResponse(
                    content=types.Content(role="model", parts=[types.Part(text=self.text)])
                )

        def builder(cfg, generation):
            component = build_agent_component(cfg, generation)
            backend = MemoryBackend()
            service = AdkSessionService(backend)
            adk = AdkRunner(agent=component.agent, app_name="agent", session_service=service)
            runner = AgentRunner(
                AppliedConfig.from_config(cfg, generation),
                adk,
                backend,
                app_name="agent",
            )
            return {
                "applied": AppliedConfig.from_config(cfg, generation),
                "agent": component,
                "runner": runner,
                "backend": backend,
                "generation": generation,
            }

        config = _resolved_config(agents=[])
        components = builder(config, 1)
        held_model = HeldLlm(gate=asyncio.Event(), text="old-gen")
        components["agent"].agent.model = held_model
        manager = ReloadManager(builder, config, components, bundled_dir=REPO_CONFIG)

        async def _collect(gen):
            return [e async for e in gen]

        in_flight = asyncio.create_task(
            _collect(
                components["runner"].execute(
                    RunRequest(principal_id="p1", user_message="hi", request_id="r-inflight")
                )
            )
        )
        await asyncio.sleep(0.3)
        assert not in_flight.done(), "run should be held in the model call"

        # rebuild (agents change) while the run is in flight
        result = await manager.apply_tier8(
            {"agents": [{"name": "worker", "systemInstruction": "w"}]}
        )
        assert result.outcome == "applied_rebuild"
        assert manager.generation == 2

        # release: the in-flight run completes against the OLD generation
        # (the rebuild swapped components["agent"]; the held model reference
        # belongs to the retired generation's agent)
        assert held_model.gate is not None
        held_model.gate.set()
        events = await in_flight
        from app.engine.events import Done

        done = [e for e in events if isinstance(e, Done)]
        assert done and done[0].finish_reason == "stop"
        # the new runner carries the sub-agent tree
        new_component = manager.components["agent"]
        assert [a.name for a in new_component.sub_agents] == ["worker"]
