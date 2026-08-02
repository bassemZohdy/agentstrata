"""Operational-mode tests (MODE-01 – MODE-04)."""

from __future__ import annotations

import pytest

from app.config.mode import STANDALONE, WATCHER, select_mode
from app.config.models import AgentConfig
from app.config.resolver import ConfigError


def cfg(**k8s) -> AgentConfig:
    return AgentConfig.model_validate(
        {
            "name": "a",
            "engine": {"systemInstruction": "s"},
            "llm": {"model": "m"},
            "k8s": k8s,
        }
    )


class TestMode:
    def test_k8s_disabled_standalone(self):
        mode, warnings = select_mode(cfg(), {})
        assert mode == STANDALONE
        assert warnings == []

    def test_k8s_enabled_with_cluster_watcher(self):
        mode, warnings = select_mode(cfg(enabled=True), {"KUBERNETES_SERVICE_HOST": "10.0.0.1"})
        assert mode == WATCHER
        assert warnings == []

    def test_k8s_enabled_required_no_cluster_fail_closed(self):
        with pytest.raises(ConfigError, match="exit 78"):
            select_mode(cfg(enabled=True, required=True), {})

    def test_k8s_enabled_optional_no_cluster_warn_standalone(self):
        mode, warnings = select_mode(cfg(enabled=True), {})
        assert mode == STANDALONE
        assert any("k8s.enabled ignored" in w for w in warnings)
