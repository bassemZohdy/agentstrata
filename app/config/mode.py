"""Operational-mode selection (REQUIREMENTS.md §2, MODE-01 – MODE-04).

Standalone mode never watches local files; Kubernetes watcher mode is
standalone plus the tier-8 watcher (§10, Milestone 6). ``k8s.required`` with
no cluster is fail-closed (exit 78) before bind.
"""

from __future__ import annotations

from .models import AgentConfig
from .resolver import ConfigError

STANDALONE = "standalone"
WATCHER = "watcher"


def select_mode(config: AgentConfig, env: dict[str, str]) -> tuple[str, list[str]]:
    """MODE-01/03: pick the operational mode and collect boot warnings."""
    warnings: list[str] = []
    if not config.k8s.enabled:
        return STANDALONE, warnings
    if "KUBERNETES_SERVICE_HOST" in env:
        return WATCHER, warnings
    if config.k8s.required:
        raise ConfigError(
            "k8s.enabled is true but KUBERNETES_SERVICE_HOST is absent and "
            "k8s.required is true: a mandatory tier-8 source cannot become "
            "available (MODE-03, exit 78)"
        )
    warnings.append("k8s.enabled ignored: not running in a cluster")
    return STANDALONE, warnings
