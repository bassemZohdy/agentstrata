"""Tier 1–7 config resolver (REQUIREMENTS.md §3, CFG-01 – CFG-11a).

Assembles the Agent Definition from bundled/mounted files, environment
variables, inline JSON, and CLI flags; deep-merges with provenance tracking
(CFG-04 – CFG-06); binds environment variables schema-aware (CFG-07 – CFG-09);
and parses CLI dotted-path flags (CFG-10). The result is a camelCase document
plus a per-leaf provenance map consumed by validation and ``--dump-config``.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import parse
from .models import AgentConfig, iter_schema_fields

# Reserved resolver variables (CFG-08) — never treated as schema bindings.
RESERVED_ENV = {
    "AGENT_PROFILE",
    "AGENT_CONFIG_DIR",
    "AGENT_APPLICATION_JSON",
    "AGENT_BUNDLED_DIR",  # dev-only escape hatch for the tier-1/2 dir (cli.run)
}

DEFAULT_CONFIG_DIR = "/etc/agent"
DEFAULT_BUNDLED_DIR = "/app/config"

_PROFILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_UNDERSCORE = re.compile(r"_+")


class ConfigError(ValueError):
    """Fatal configuration error (boot/CLI: exit 78)."""


class UsageError(ConfigError):
    """Usage error (CFG-10: exit 64 EX_USAGE)."""


class AmbiguousEnvError(ConfigError):
    """More than one binding matched an environment variable (CFG-07)."""


def _strip(key: str) -> str:
    return _UNDERSCORE.sub("", key).lower()


def camel_to_env_alias(path: str) -> str:
    """``engine.systemInstruction`` -> ``AGENT_ENGINE_SYSTEM_INSTRUCTION``."""
    parts = []
    for segment in path.split("."):
        out = []
        for i, ch in enumerate(segment):
            if ch.isupper() and i > 0:
                out.append("_")
            out.append(ch)
        parts.append("".join(out).upper())
    return "AGENT_" + "_".join(parts)


@dataclass
class Provenance:
    tier: int
    reset: bool = False
    source: str = ""

    def label(self) -> str:
        if self.reset:
            return (
                f"tier {self.tier}: {self.source} (reset-to-default)"
                if self.source
                else f"tier {self.tier}: reset-to-default"
            )
        return f"tier {self.tier}: {self.source}" if self.source else f"tier {self.tier}"


@dataclass
class Resolution:
    data: dict[str, Any]
    provenance: dict[str, Provenance] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    profile: str = ""
    config_dir: str = ""

    def prov(self, path: str) -> Provenance | None:
        return self.provenance.get(path)


def _apply_tier(
    merged: dict[str, Any],
    prov: dict[str, Provenance],
    doc: dict[str, Any],
    tier: int,
    source: str,
    path: str = "",
) -> None:
    for key, value in doc.items():
        p = f"{path}.{key}" if path else key
        existing = merged.get(key)
        if isinstance(value, dict):
            # CFG-04 recursive merge; a higher-tier dict replaces a lower-tier
            # scalar, and nulls inside it still get CFG-06 reset semantics.
            if not isinstance(existing, dict):
                merged[key] = {}
            _apply_tier(merged[key], prov, value, tier, source, p)
        elif value is None:
            # CFG-06 null reset: remove lower-tier value, request the default.
            merged.pop(key, None)
            prov[p] = Provenance(tier=tier, reset=True, source=source)
        else:
            merged[key] = value
            prov[p] = Provenance(tier=tier, reset=False, source=source)


# ---------------------------------------------------------------------------
# CFG-09 target-aware value parsing
# ---------------------------------------------------------------------------


def parse_scalar(value: str, ann: Any, path: str, source: str) -> Any:
    """Convert an env/CLI string to the schema target type (CFG-09).

    ``null`` (case-insensitive) returns None (a reset, CFG-06). Failure raises
    :class:`ConfigError` naming the source, path, and expected type without
    echoing values from secret-sensitive paths.
    """
    if value.strip().lower() == "null":
        return None

    import enum

    if isinstance(ann, type) and issubclass(ann, enum.Enum):
        for member in ann:
            if member.value == value:
                return member
        raise ConfigError(
            f"{source}: invalid value for {path}: expected one of "
            + ", ".join(m.value for m in ann)
        )

    # Optional[X] / Union[X, None] (PEP 604 unions included)
    from typing import get_args, get_origin

    origin = get_origin(ann)
    if origin is not None:
        non_none = [a for a in get_args(ann) if a is not type(None)]
        if non_none:
            return parse_scalar(value, non_none[0], path, source)

    if ann is str:
        return value
    if ann is bool:
        low = value.lower()
        if low == "true":
            return True
        if low == "false":
            return False
        raise ConfigError(f"{source}: invalid boolean for {path}: expected 'true' or 'false'")
    if ann is int:
        try:
            return int(value, 10)
        except ValueError:
            raise ConfigError(f"{source}: invalid integer for {path}: {value!r}") from None
    if ann is float:
        try:
            f = float(value)
        except ValueError:
            raise ConfigError(f"{source}: invalid float for {path}: {value!r}") from None
        if not math.isfinite(f):
            raise ConfigError(f"{source}: non-finite float for {path}")
        return f
    raise ConfigError(f"{source}: unsupported scalar type for {path}")


def _closest_paths(candidates: list[str], target: str, n: int = 3) -> list[str]:
    def dist(a: str, b: str) -> int:
        # simple edit distance over stripped aliases
        dp = list(range(len(b) + 1))
        for i, ca in enumerate(a, 1):
            prev = dp[0]
            dp[0] = i
            for j, cb in enumerate(b, 1):
                cur = dp[j]
                dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev + (ca != cb))
                prev = cur
        return dp[len(b)]

    stripped = _strip(target)
    scored = sorted((dist(_strip(c), stripped), c) for c in candidates)
    return [c for _, c in scored[:n]]


# ---------------------------------------------------------------------------
# Tier loading
# ---------------------------------------------------------------------------


def resolve_config_dir(env: dict[str, str] | None = None, cli_config_dir: str | None = None) -> str:
    """CFG-02: ``AGENT_CONFIG_DIR`` then ``--config-dir``; default ``/etc/agent``."""
    env = dict(env) if env is not None else dict(os.environ)
    value = cli_config_dir or env.get("AGENT_CONFIG_DIR") or DEFAULT_CONFIG_DIR
    if not os.path.isabs(value):
        raise ConfigError(f"configDir must be an absolute path, got {value!r}")
    return value


def resolve_profile(env: dict[str, str] | None = None, cli_profile: str | None = None) -> str:
    """CFG-03: profile from ``AGENT_PROFILE`` then ``--profile``."""
    env = dict(env) if env is not None else dict(os.environ)
    value = cli_profile if cli_profile is not None else env.get("AGENT_PROFILE", "")
    if value and not _PROFILE_RE.match(value):
        raise ConfigError(f"invalid profile {value!r}: must match {_PROFILE_RE.pattern}")
    return value


def _first_existing(base: Path, names: list[str], warnings: list[str]) -> tuple[Path, str] | None:
    found: list[Path] = [base / n for n in names if (base / n).is_file()]
    if not found:
        return None
    if len(found) > 1:
        warnings.append(
            "config source selection: multiple candidate files present; loading only "
            + f"{found[0]}, ignoring siblings {[str(p) for p in found[1:]]}"
        )
    return found[0], found[0].suffix.lower()


def load_file_tiers(
    bundled_dir: str,
    config_dir: str,
    profile: str,
    env: dict[str, str] | None = None,
    cli_config_dir: str | None = None,
) -> tuple[list[tuple[int, dict[str, Any], str]], list[str]]:
    """Load tiers 1–4. Returns ``(tier, doc, source)`` triples plus warnings;
    the tier label is the real CFG-01 tier number (a skipped tier shifts
    nothing)."""
    docs: list[tuple[int, dict[str, Any], str]] = []
    warnings: list[str] = []

    # Tier 1 — bundled base (CFG-01; E1-2/CFG-16: skipped when absent so
    # env-only boot works — the release image still ships it per BASE-01).
    tier1 = Path(bundled_dir) / "agent.yaml"
    if tier1.is_file():
        docs.append((1, parse.parse_file(tier1), str(tier1)))
    else:
        warnings.append(f"bundled config {tier1} not found; continuing with tiers 2-7 (CFG-16)")

    # Tier 2 — bundled profile (skipped if absent).
    if profile:
        tier2 = Path(bundled_dir) / f"agent-{profile}.yaml"
        if tier2.is_file():
            docs.append((2, parse.parse_file(tier2), str(tier2)))

    # Tier 3 — mounted base: first existing of the four candidates (CFG-01/03b).
    base_dir = Path(config_dir)
    found = _first_existing(
        base_dir,
        ["agent.yaml", "agent.yml", "agent.json", "config.yaml"],
        warnings,
    )
    if found:
        docs.append((3, parse.parse_file(found[0]), str(found[0])))

    # Tier 4 — mounted profile (skipped if absent).
    if profile:
        found = _first_existing(
            base_dir,
            [f"agent-{profile}.yaml", f"agent-{profile}.yml", f"agent-{profile}.json"],
            warnings,
        )
        if found:
            docs.append((4, parse.parse_file(found[0]), str(found[0])))

    return docs, warnings


# ---------------------------------------------------------------------------
# Environment binding (CFG-07/08)
# ---------------------------------------------------------------------------


# E1-4 (CFG-07 item 4): closed short-alias table — FROZEN once
# published.  Keys are the env-var suffix after ``AGENT_``; values are
# target schema paths.  Canonical names win over aliases (bind_env).
ENV_ALIASES: dict[str, str] = {
    "MODEL": "llm.model",
    "INSTRUCTION": "engine.systemInstruction",
    "API_KEY": "llm.apiKeyEnv",
    "PROVIDER": "llm.provider",
}


def _build_binding_index() -> dict[str, list[tuple[str, str, str, Any]]]:
    """Map stripped env alias -> [(canonical env name, path, kind, ann)].

    Includes the closed short-alias table (E1-4, CFG-07 item 4) — the
    alias entries are ordinary index entries, so they participate in
    ambiguity detection; canonical-wins is enforced by ``bind_env``.
    """
    index: dict[str, list[tuple[str, str, str, Any]]] = {}
    by_path: dict[str, tuple[str, Any]] = {}
    for path, kind, ann, bindable in iter_schema_fields(AgentConfig):
        if not bindable:
            continue  # CFG-07: list-item paths are not bindable
        alias = camel_to_env_alias(path)
        index.setdefault(_strip(alias.removeprefix("AGENT_")), []).append((alias, path, kind, ann))
        by_path[path] = (kind, ann)
    for short, target in ENV_ALIASES.items():
        kind, ann = by_path[target]
        index.setdefault(_strip(short), []).append((f"AGENT_{short}", target, kind, ann))
    return index


def _canonically_bound_paths(env: dict[str, str], index: dict) -> set[str]:
    """Paths for which a CANONICAL AGENT_* variable exists in the env.

    CFG-07 item 4: a canonical name always wins over an alias for the
    same target path, regardless of OS enumeration order — so alias
    entries for these paths are skipped in bind_env.
    """
    bound: set[str] = set()
    for var in env:
        if not var.upper().startswith("AGENT_") or var.upper() in {r.upper() for r in RESERVED_ENV}:
            continue
        suffix = var[len("AGENT_") :]
        candidates = index.get(_strip(suffix), [])
        for _, path, _, _ in candidates:
            # canonical name = the schema-derived env alias of the target
            # path — an exact-match var whose name IS that alias; alias
            # entries (AGENT_MODEL etc.) never count as canonical.
            if var.upper() == camel_to_env_alias(path).upper():
                bound.add(path)
    return bound


def bind_env(
    env: dict[str, str],
    warnings: list[str],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Bind AGENT_* variables to schema paths (CFG-07/08).

    Returns ``(doc, var_to_path)``. Ambiguous matches are fatal (CFG-07);
    unmatched variables warn with up to three closest paths (CFG-08).
    """
    doc: dict[str, Any] = {}
    var_to_path: dict[str, str] = {}
    index = _build_binding_index()
    all_paths = [p for p, _, _, _ in iter_schema_fields(AgentConfig)]
    canonical_bound = _canonically_bound_paths(env, index)

    for var, value in env.items():
        if not var.upper().startswith("AGENT_") or var.upper() in {r.upper() for r in RESERVED_ENV}:
            continue
        suffix = var[len("AGENT_") :]
        stripped = _strip(suffix)
        candidates = index.get(stripped, [])
        if not candidates:
            sensitive = re.search(r"KEY|TOKEN|SECRET|PASSWORD", var, re.IGNORECASE)
            close = _closest_paths(all_paths, suffix)
            hint = f"; closest paths: {', '.join(close)}" if close else ""
            if sensitive:
                warnings.append(
                    f"environment variable {var} matches no schema path{hint}; value not logged"
                )
            elif _looks_like_list_index(suffix):
                # E1-3 (CFG-08): collection items are not env-bindable —
                # signpost the JSON transport so the dead end is explicit.
                warnings.append(
                    f"environment variable {var} matches no schema path{hint}; "
                    f"collection items are not env-bindable — use "
                    f"AGENT_APPLICATION_JSON instead"
                )
            else:
                warnings.append(f"environment variable {var} matches no schema path{hint}; ignored")
            continue
        if len(candidates) > 1:
            targets = sorted({c[1] for c in candidates})
            raise AmbiguousEnvError(f"environment variable {var} is ambiguous: binds {targets}")
        _, path, kind, ann = candidates[0]
        if path in canonical_bound and var.upper() != camel_to_env_alias(path).upper():
            # E1-4 (CFG-07 item 4): an alias loses to the canonical name
            # for the same target path, regardless of env order.
            continue
        _set_path(doc, path, value, kind, ann, source=f"env:{var}", warnings=warnings)
        var_to_path[var] = path
    return doc, var_to_path


def _looks_like_list_index(suffix: str) -> bool:
    """CFG-08 (E1-3): ``…_0_NAME`` / ``…_0`` — the shape an indexed
    collection convention would use."""
    return bool(re.search(r"_\d+($|_)", suffix))


def _set_path(
    doc: dict[str, Any],
    path: str,
    raw_value: str,
    kind: str,
    ann: Any,
    source: str,
    warnings: list[str],
) -> None:
    """Insert a value at a dotted path, parsing per CFG-09."""
    segments = path.split(".")
    node: dict[str, Any] = doc
    for seg in segments[:-1]:
        node = node.setdefault(seg, {})
    leaf = segments[-1]

    if kind in ("model", "list", "passthrough"):
        try:
            parsed = parse.parse_json_value(raw_value, source)
        except parse.SourceError as exc:
            raise ConfigError(str(exc)) from exc
        node[leaf] = parsed
        return

    node[leaf] = parse_scalar(raw_value, ann, path, source)


# ---------------------------------------------------------------------------
# CLI flags (CFG-10)
# ---------------------------------------------------------------------------


def parse_cli_values(argv: list[str], warnings: list[str]) -> dict[str, Any]:
    """Extract ``--<dotted.path>=<value>`` flags; last occurrence wins (CFG-10)."""
    doc: dict[str, Any] = {}
    bindings = {
        path: (kind, ann)
        for path, kind, ann, bindable in iter_schema_fields(AgentConfig)
        if bindable
    }
    seen: set[str] = set()

    for arg in argv:
        if not arg.startswith("--"):
            continue
        body = arg[2:]
        if "=" not in body:
            continue  # bootstrap flags handled by the CLI layer
        path, value = body.split("=", 1)
        if path in ("profile", "config-dir", "validate", "dump-config", "version", "help"):
            continue
        if path not in bindings:
            raise UsageError(
                f"unknown config path --{path}; closest: "
                + ", ".join(_closest_paths([p for p in bindings], path))
            )
        kind, ann = bindings[path]
        if path in seen:
            warnings.append(f"CLI flag --{path} specified more than once; last occurrence wins")
        seen.add(path)
        _set_path(doc, path, value, kind, ann, source=f"cli:--{path}", warnings=warnings)
    return doc


# ---------------------------------------------------------------------------
# Full resolution pipeline
# ---------------------------------------------------------------------------


def resolve(
    *,
    env: dict[str, str] | None = None,
    argv: list[str] | None = None,
    bundled_dir: str = DEFAULT_BUNDLED_DIR,
    cli_profile: str | None = None,
    cli_config_dir: str | None = None,
) -> Resolution:
    """Resolve tiers 1–7 into a merged camelCase document with provenance.

    ``env``/``argv`` are injectable for determinism tests (NFR-05); defaults
    read ``os.environ`` / ``sys.argv``.
    """
    env = dict(env) if env is not None else dict(os.environ)
    argv = list(argv) if argv is not None else []

    profile = resolve_profile(env, cli_profile)
    config_dir = resolve_config_dir(env, cli_config_dir)

    merged: dict[str, Any] = {}
    prov: dict[str, Provenance] = {}
    warnings: list[str] = []

    # Tiers 1–4 (files).
    docs, file_warnings = load_file_tiers(bundled_dir, config_dir, profile, env, cli_config_dir)
    warnings.extend(file_warnings)
    for tier, doc, source in docs:
        _apply_tier(merged, prov, doc, tier, source)

    # Tier 5 — environment.
    env_doc, var_to_path = bind_env(env, warnings)
    _apply_tier(merged, prov, env_doc, 5, "env")
    # CFG-18 (E1-6): name the SPECIFIC variable in every env-bound leaf's
    # provenance (`# tier 5: env:AGENT_LLM_MODEL`), not just the tier.
    for var, path in var_to_path.items():
        source = f"env:{var}"
        for leaf in list(prov):
            if leaf == path or leaf.startswith(path + "."):
                # CFG-06 null resets keep their reset flag (label reads
                # "reset-to-default"); only the source names the variable.
                existing = prov[leaf]
                prov[leaf] = Provenance(tier=5, reset=existing.reset, source=source)

    # Tier 6 — AGENT_APPLICATION_JSON (invalid JSON is fatal, CFG-01).
    inline = env.get("AGENT_APPLICATION_JSON")
    if inline is not None:
        try:
            inline_doc = parse.parse_json_text(inline, "AGENT_APPLICATION_JSON")
        except parse.SourceError as exc:
            raise ConfigError(str(exc)) from exc
        _apply_tier(merged, prov, inline_doc, 6, "AGENT_APPLICATION_JSON")

    # Tier 7 — CLI flags.
    cli_doc = parse_cli_values(argv, warnings)
    _apply_tier(merged, prov, cli_doc, 7, "cli")

    _finalize(merged, prov, warnings)
    return Resolution(
        data=merged, provenance=prov, warnings=warnings, profile=profile, config_dir=config_dir
    )


def _finalize(
    merged: dict[str, Any],
    prov: dict[str, Provenance],
    warnings: list[str],
) -> None:
    """Post-merge normalization before validation:

    - SCH-04 deprecated ``http`` transport -> ``streamable-http`` (warning).
    - k8s.name defaults to the top-level name (SCH-07).
    - observability.otel.serviceName defaults to the top-level name (SCH-08).
    """
    name = merged.get("name")
    tools = merged.get("tools")
    if isinstance(tools, dict):
        servers = tools.get("mcpServers")
        if isinstance(servers, list):
            for i, server in enumerate(servers):
                if isinstance(server, dict) and server.get("transport") == "http":
                    old = prov.get(f"tools.mcpServers[{i}].transport")
                    server["transport"] = "streamable-http"
                    prov[f"tools.mcpServers[{i}].transport"] = Provenance(
                        tier=old.tier if old else 7,
                        source=f"{old.source}; normalized http -> streamable-http"
                        if old
                        else "normalized http -> streamable-http",
                    )
                    warnings.append(
                        f"tools.mcpServers[{i}].transport: deprecated 'http' alias "
                        "normalized to 'streamable-http'"
                    )
    k8s = merged.setdefault("k8s", {})
    if isinstance(k8s, dict) and not k8s.get("name") and isinstance(name, str) and name:
        k8s["name"] = name
        prov["k8s.name"] = Provenance(tier=0, source="derived from top-level name")
    obs = merged.setdefault("observability", {})
    otel = obs.setdefault("otel", {})
    if isinstance(otel, dict) and not otel.get("serviceName") and isinstance(name, str) and name:
        otel["serviceName"] = name
        prov["observability.otel.serviceName"] = Provenance(
            tier=0, source="derived from top-level name"
        )
