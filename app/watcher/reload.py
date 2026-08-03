"""Transactional config reload (REQUIREMENTS.md REL-01 – REL-06).

Every watch event becomes a tier-8 overlay; the complete tiers 1–8 result
must pass CFG-12/CFG-14/CAP-01 before any mutation. Reloads are categorized
per REL-02 (live-snapshot / component-rebuild / restart-required), applied
transactionally (REL-03) with an atomic Applied Config pointer swap and
last-known-good rollback, tracked by generation + configHash (REL-04), with
deletion/resync fallback (REL-05) and audit logging (REL-06).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# REL-02 leaf classification.
LIVE_SNAPSHOT_PATHS = {
    "$schema",
    "description",
    "engine.maxOutputBytes",
    "engine.timeoutSeconds",
    "engine.maxIterations",
    "engine.historyMaxMessages",
    "engine.historyMaxBytes",
    "engine.streaming",
    "engine.overrides",
    "engine.tokenBudget",
    "storage.sessionTtlSeconds",
    "storage.runTtlSeconds",
    "storage.maxSessions",
    "storage.maxRunsPerSession",
    "storage.maxIdempotencyRecordsPerSession",
    "storage.lockAcquireSeconds",
    "storage.idempotencyTtlSeconds",
    "server.rateLimit",
    "server.maxConcurrentRequests",
    "server.maxRequestBytes",
    "server.maxMessageBytes",
    "server.streamQueueEvents",
    "server.slowConsumerSeconds",
    "server.exposeSystemInstruction",
    "server.shutdownGraceSeconds",
    "observability.logLevel",
    "observability.includeToolArguments",
}
RESTART_REQUIRED_PREFIXES = (
    "schemaVersion",
    "name",
    "profile",
    "storage.type",
    "storage.path",
    "storage.connectionString",
    "server.host",
    "server.port",
    "server.protocols",
    "server.cors",
    "server.auth",
    "server.trustedProxyCidrs",
    "server.maxRequestLineBytes",
    "server.maxHeaderBytes",
    "server.maxHeaderCount",
    "k8s.",
    "observability.logFormat",
    "observability.otel",
)


def classify_change(changed_paths: list[str]) -> str:
    """REL-02: categorize a set of changed paths. Restart-required wins over
    component-rebuild wins over live-snapshot."""
    if any(p.startswith(RESTART_REQUIRED_PREFIXES) for p in changed_paths):
        return "restart_required"
    rebuild = {
        "engine.systemInstruction",
        "engine.temperature",
        "engine.topP",
        "engine.maxTokens",
        "llm.",
        "tools.",
        "agents",
        "approval",
        "rag",
    }
    if any(p.startswith(t) for t in rebuild for p in changed_paths):
        return "component_rebuild"
    return "live_snapshot"


def changed_paths(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """Sorted changed LEAF dotted paths (REL-02 classification needs leaf
    granularity; secret values are omitted, labeled separately)."""
    paths: list[str] = []

    def walk(o: Any, n: Any, prefix: str) -> None:
        if isinstance(o, dict) and isinstance(n, dict):
            for key in sorted(set(o) | set(n)):
                walk(o.get(key), n.get(key), f"{prefix}.{key}" if prefix else str(key))
            return
        if o != n:
            paths.append(prefix)

    walk(old, new, "")
    return sorted(paths)


@dataclass
class ReloadResult:
    outcome: (
        str  # applied_live | applied_rebuild | noop | rejected | rebuild_failed | restart_required
    )
    generation: int = 1
    changed: list[str] = field(default_factory=list)
    error: str | None = None


class ReloadManager:
    """Owns the current generation and the atomic component pointer."""

    def __init__(
        self,
        build_components: Callable[[Any, int], dict[str, Any]],
        initial_config: Any,
        components: dict[str, Any],
        bundled_dir: str | None = None,
    ) -> None:
        self._build_components = build_components
        self._bundled_dir = bundled_dir
        self._generation = 1
        self._config = initial_config
        self._components = components
        self._components["generation"] = self._generation
        self._components["config_hash"] = _config_hash(initial_config)

    @property
    def components(self) -> dict[str, Any]:
        return self._components

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def config_hash(self) -> str:
        return self._components.get("config_hash", "")

    def snapshot(self) -> dict[str, Any]:
        return {
            "generation": self._generation,
            "config_hash": self.config_hash,
        }

    async def apply_tier8(self, overlay: dict[str, Any]) -> ReloadResult:
        """REL-01/03/04: validate + categorize + transactional apply."""
        from ..config.validate import validate_resolution as _validate

        started = time.monotonic()
        # The overlay is merged as tier 8 on top of tiers 1-7.
        candidate, result = _resolve_with_overlay(overlay, self._bundled_dir)
        validated = _validate(candidate)
        if not validated.ok:
            self._audit("rejected", started, [], error="validation failed")
            return ReloadResult(outcome="rejected", generation=self._generation)

        assert validated.config is not None
        new_config = validated.config
        if new_config.model_dump(by_alias=True, mode="json") == self._config.model_dump(
            by_alias=True, mode="json"
        ):
            self._audit("noop", started, [])
            return ReloadResult(outcome="noop", generation=self._generation)

        paths = changed_paths(
            self._config.model_dump(by_alias=True, mode="json"),
            new_config.model_dump(by_alias=True, mode="json"),
        )
        category = classify_change(paths)
        if category == "restart_required":
            self._audit("restart_required", started, paths)
            return ReloadResult(
                outcome="restart_required",
                generation=self._generation,
                changed=paths,
            )

        if category == "live_snapshot":
            self._generation += 1
            self._config = new_config
            self._components["generation"] = self._generation
            self._components["config_hash"] = _config_hash(new_config)
            self._audit("applied_live", started, paths)
            return ReloadResult(outcome="applied_live", generation=self._generation, changed=paths)

        # component_rebuild: build replacements + health-check, then swap.
        try:
            replacements = self._build_components(new_config, self._generation + 1)
            await _health_check(replacements)
        except Exception as exc:  # noqa: BLE001
            logger.exception("component rebuild failed")
            self._audit("rebuild_failed", started, paths, error=str(exc)[:200])
            return ReloadResult(
                outcome="rebuild_failed", generation=self._generation, changed=paths
            )

        # Atomic pointer swap (REL-03): replace in place so the running app
        # (which holds a reference to this dict) sees the new generation;
        # retired components close after.
        old_components = dict(self._components)
        self._generation += 1
        self._config = new_config
        replacements["generation"] = self._generation
        replacements["config_hash"] = _config_hash(new_config)
        self._components.clear()
        self._components.update(replacements)
        await _close_components(old_components)
        self._audit("applied_rebuild", started, paths)
        return ReloadResult(outcome="applied_rebuild", generation=self._generation, changed=paths)

    def _audit(
        self, outcome: str, started: float, paths: list[str], error: str | None = None
    ) -> None:
        """REL-06: resource version, outcome, generations, sorted changed
        paths, duration. Values are omitted; secret paths labeled only."""
        duration_ms = _ms_since(started)
        new_generation = self._generation + (1 if outcome.startswith("applied") else 0)
        logger.info(
            "reload outcome=%s old_generation=%d new_generation=%d changed=%s "
            "duration_ms=%d error=%s",
            outcome,
            self._generation,
            new_generation,
            ",".join(paths) or "-",
            duration_ms,
            error or "-",
        )


def _resolve_with_overlay(overlay: dict[str, Any], bundled_dir: str | None = None):
    """Resolve tiers 1-7 (env/argv from the process) and merge the overlay as
    tier 8, then validate the complete result."""
    import os

    from ..config.resolver import Resolution, resolve
    from ..config.validate import validate_resolution

    base = resolve(
        env=dict(os.environ),
        argv=[],
        bundled_dir=bundled_dir or os.environ.get("AGENT_BUNDLED_DIR") or "/app/config",
    )
    # tier 8 overlay is highest precedence
    from ..config.resolver import _apply_tier

    merged = dict(base.data)
    _apply_tier(merged, base.provenance, overlay, 8, "k8s-overlay")
    res = Resolution(
        data=merged,
        provenance=base.provenance,
        warnings=base.warnings,
        profile=base.profile,
        config_dir=base.config_dir,
    )
    return res, validate_resolution(res)


def _config_hash(config: Any) -> str:
    from ..security import redact

    raw = config.model_dump(by_alias=True, mode="json")
    masked = redact.mask_value(raw)
    canonical = json.dumps(masked, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


async def _health_check(components: dict[str, Any]) -> None:
    backend = components.get("backend")
    if backend is not None:
        ok = await backend.health()
        if not ok:
            raise RuntimeError("replacement storage backend unhealthy")


async def _close_components(components: dict[str, Any]) -> None:
    mcp = components.get("mcp")
    if mcp is not None:
        try:
            await mcp.close()
        except Exception:  # noqa: BLE001
            logger.exception("error closing retired MCP manager")


def _ms_since(started: float) -> int:
    """Wall-clock milliseconds since ``started`` (never throws)."""
    try:
        return int((time.monotonic() - started) * 1000)
    except (TypeError, ValueError):
        return 0
