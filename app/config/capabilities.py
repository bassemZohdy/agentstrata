"""Build-time phase and capabilities (REQUIREMENTS.md CAP-02).

The image exposes its phase and capabilities at build time and through
``GET /health`` (Milestone 5). A capability is reported ``true`` only when its
acceptance suite is present and passing — P2 (multi-agent + ACP) is now
implemented and its acceptance suite is in the tree; P3/P4 remain fail-closed.
"""

from __future__ import annotations

from .resolver import ConfigError

PHASE = "P2"

# CAP-01: a P2 build rejects approval.enabled and rag.enabled (fail closed,
# never warn-and-continue); multi-agent and ACP are implemented and gated on
# their acceptance suite (CAP-02).
CAPABILITY_PATHS = {
    "multiAgent": "agents",
    "acp": "server.protocols.acp",
    "approval": "approval.enabled",
    "rag": "rag.enabled",
}

BUILD_CAPABILITIES: dict[str, bool] = {
    "multiAgent": True,
    "acp": True,
    "approval": False,
    "rag": False,
}


def capability_status() -> dict[str, str | bool]:
    """Capability booleans for ``GET /health`` (CAP-02)."""
    return {"phase": PHASE, **BUILD_CAPABILITIES}


def assert_capabilities_supported(config) -> None:
    """Fail closed when the config would enable an unimplemented capability."""
    for capability, path in CAPABILITY_PATHS.items():
        if _enabled(config, path):
            raise ConfigError(
                f"capability {capability!r} (path {path}) is not implemented in this "
                f"{PHASE} build (CAP-01): configuration must disable it"
            )


def _enabled(config, path: str) -> bool:
    node: object = config
    for segment in path.split("."):
        if isinstance(node, dict):
            node = node.get(segment)
        else:
            node = getattr(node, segment, None)
    return bool(node)
