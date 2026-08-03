"""Watcher + reload tests (REL-01..06, K8S-01..09)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.config.models import AgentConfig
from app.engine.agent import AppliedConfig
from app.watcher.reload import ReloadManager, changed_paths, classify_change
from app.watcher.watcher import ConfigMapWatcher, FakeKubeClient, _extract_overlay

REPO_CONFIG = str(Path(__file__).resolve().parents[2] / "config")


def _resolved_config():
    from app.config.resolver import resolve
    from app.config.validate import validate_resolution

    res = resolve(env={}, bundled_dir=REPO_CONFIG, argv=[])
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
