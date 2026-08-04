"""Validation pipeline (REQUIREMENTS.md CFG-12 – CFG-15, CAP-01, CAP-02).

Order: alias-only shape walk (CFG-13) -> ``AgentConfig.model_validate()``
(CFG-12) -> cross-field checks (CFG-14) -> capability gating (CAP-01).

Every independent error is reported in one deterministic aggregate sorted by
config path and error code; secret values are omitted. Cross-field and
capability checks run against the merged document (with schema-default
fallback), so they aggregate with schema errors even when ``model_validate``
fails — per CFG-12's "every independent error" requirement.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from typing import Any, TypeGuard, get_args, get_origin

from pydantic import BaseModel, ValidationError

from ..security import redact
from . import models
from .models import AgentConfig
from .resolver import Resolution

# Schema defaults as a camelCase document. Built by validating a minimal
# document so EVERY defaulted field (including nested models) is populated;
# used as the fallback layer so cross-field checks see declared defaults even
# when raw data omits them.
_DEFAULT_DOC = AgentConfig.model_validate(
    {"name": "x", "engine": {"systemInstruction": "s"}, "llm": {"model": "m"}}
).model_dump(by_alias=True, mode="json")


@dataclass(frozen=True)
class ConfigIssue:
    path: str
    code: str
    message: str
    tier: int = 0

    def __lt__(self, other: ConfigIssue) -> bool:
        return (self.path, self.code) < (other.path, other.code)


@dataclass
class ValidationResult:
    config: AgentConfig | None
    issues: list[ConfigIssue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.config is not None and not self.issues

    def report(self) -> str:
        return "\n".join(
            f"{i.path}: {i.code}: {i.message}" + (f" (tier {i.tier})" if i.tier else "")
            for i in self.issues
        )


def _path_join(path: str, seg: str) -> str:
    return f"{path}.{seg}" if path else seg


def _is_model(t: Any) -> TypeGuard[type[BaseModel]]:
    return isinstance(t, type) and issubclass(t, BaseModel)


def _shape_walk(
    doc: dict[str, Any],
    model_cls: type[models.BaseModel],
    issues: list[ConfigIssue],
    tier_of: Any,
    path: str = "",
) -> None:
    aliases = models.field_aliases(model_cls)
    for key in doc:
        if key not in aliases:
            issues.append(
                ConfigIssue(
                    _path_join(path, str(key)),
                    "unknown_field",
                    f"unknown field {key!r}",
                    tier_of(_path_join(path, str(key))),
                )
            )
    for fname, field_ in model_cls.model_fields.items():
        alias = field_.alias or models.to_camel(fname.replace("_", ""))  # type: ignore[attr-defined]
        if alias not in doc:
            continue
        value = doc[alias]
        ann = field_.annotation
        origin = get_origin(ann)
        child = _path_join(path, alias)
        if origin is dict:
            inner = get_args(ann)[0] if get_args(ann) else None
            if _is_model(inner) and isinstance(value, dict):
                # dict[str, SecretHeaderRef]: keys arbitrary, values validated.
                for k, v in value.items():
                    if isinstance(v, dict):
                        _shape_walk(v, inner, issues, tier_of, _path_join(child, str(k)))
            continue
        if origin is list:
            inner = get_args(ann)[0] if get_args(ann) else None
            if _is_model(inner) and isinstance(value, list):
                for i, item in enumerate(value):
                    if isinstance(item, dict):
                        _shape_walk(item, inner, issues, tier_of, f"{child}[{i}]")
            continue
        if _is_model(ann) and isinstance(value, dict):
            _shape_walk(value, ann, issues, tier_of, child)


# ---------------------------------------------------------------------------
# Merged-document reader with schema-default fallback
# ---------------------------------------------------------------------------


def _split_path(path: str) -> list[str | int]:
    parts: list[str | int] = []
    for segment in path.split("."):
        if "[" in segment:
            base, idx = segment.split("[", 1)
            if base:
                parts.append(base)
            try:
                parts.append(int(idx.rstrip("]")))
            except ValueError:
                parts.append(segment)
        else:
            parts.append(segment)
    return parts


def _s(value: Any) -> Any:
    """Normalize enum members to their literal values for comparison."""
    return value.value if hasattr(value, "value") else value


def _descend(node: Any, part: str | int) -> Any:
    """Return the child at ``part`` or the sentinel when absent."""
    if isinstance(node, dict) and isinstance(part, str) and part in node:
        return node[part]
    if isinstance(node, list) and isinstance(part, int) and -len(node) <= part < len(node):
        return node[part]
    return _MISSING


_MISSING = object()


class _Doc:
    """Reads a merged camelCase document, falling back to schema defaults."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    def raw(self, path: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in _split_path(path):
            node = _descend(node, part)
            if node is _MISSING:
                return default
        return node

    def get(self, path: str, default: Any = None) -> Any:
        value = self.raw(path, default)
        if value is not None:
            return _s(value)
        node: Any = _DEFAULT_DOC
        for part in _split_path(path):
            node = _descend(node, part)
            if node is _MISSING:
                return default
        return _s(node) if node is not None else default


# ---------------------------------------------------------------------------
# CFG-14 cross-field checks
# ---------------------------------------------------------------------------


def _cross_field(doc: _Doc, res: Resolution, issues: list[ConfigIssue]) -> None:
    def issue(path: str, msg: str) -> None:
        issues.append(ConfigIssue(path, "cross_field", msg, _tier_of(res, path)))

    storage_type = doc.get("storage.type")
    if storage_type in ("redis", "postgres") and not (
        doc.get("storage.connectionStringEnv") or doc.get("storage.connectionStringFile")
    ):
        issue(
            "storage.connectionStringEnv",
            "redis/postgres storage requires connectionStringEnv or connectionStringFile",
        )
    if storage_type == "file" and not doc.get("storage.path"):
        issue("storage.path", "file storage requires a path")
    session_ttl = doc.get("storage.sessionTtlSeconds")
    if session_ttl is not None and session_ttl != 0 and session_ttl < 60:
        issue("storage.sessionTtlSeconds", "sessionTtlSeconds must be 0 or >= 60")
    lock = doc.get("storage.lockAcquireSeconds")
    if lock is not None and lock != 0.0 and not (0 < lock <= 5):
        issue("storage.lockAcquireSeconds", "lockAcquireSeconds must be 0 or in (0, 5]")

    servers = doc.raw("tools.mcpServers", [])
    if isinstance(servers, list):
        for i, srv in enumerate(servers):
            if not isinstance(srv, dict):
                continue
            prefix = f"tools.mcpServers[{i}]"
            transport = _s(srv.get("transport"))
            if transport == "stdio":
                if not srv.get("command"):
                    issue(f"{prefix}.command", "stdio MCP server requires a command")
                if srv.get("url"):
                    issue(f"{prefix}.url", "stdio MCP server must not set url")
            else:
                if not srv.get("url"):
                    issue(f"{prefix}.url", f"{transport} MCP server requires a url")
                if srv.get("command"):
                    issue(f"{prefix}.command", f"{transport} MCP server must not set command")
            result_bytes = srv.get("maxResultBytes")
            transport_bytes = srv.get("maxTransportMessageBytes")
            if (
                isinstance(result_bytes, int)
                and isinstance(transport_bytes, int)
                and result_bytes > transport_bytes
            ):
                issue(
                    f"{prefix}.maxResultBytes", "maxResultBytes must be <= maxTransportMessageBytes"
                )
            headers = srv.get("headers")
            if isinstance(headers, dict):
                bad = redact.sensitive_headers_present(headers)
                if bad:
                    issue(
                        f"{prefix}.headers",
                        f"static headers contain secret-sensitive keys: {sorted(bad)}",
                    )
        names = [_s(s.get("name")) for s in servers if isinstance(s, dict)]
        dupes = sorted({n for n in names if n and names.count(n) > 1})
        if dupes:
            issue("tools.mcpServers", f"duplicate MCP server names: {dupes}")
    else:
        names = []

    # MA-01: sub-agent definitions — unique DNS-label names distinct from the
    # root, and every toolServers reference must exist (absent toolServers
    # defaults to every configured MCP server). Nested/cyclic definitions are
    # structurally impossible (AgentDef has no agents field).
    server_names = set(names)
    agents = doc.raw("agents", [])
    if isinstance(agents, list) and agents:
        root_name = _s(doc.get("name"))
        agent_names: list[str] = []
        for i, agent in enumerate(agents):
            if not isinstance(agent, dict):
                continue
            prefix = f"agents[{i}]"
            aname = _s(agent.get("name"))
            if aname:
                agent_names.append(aname)
                if root_name and aname == root_name:
                    issue(f"{prefix}.name", "sub-agent name must differ from the root name (MA-01)")
            ts = agent.get("toolServers")
            if isinstance(ts, list):
                for j, ref in enumerate(ts):
                    if not isinstance(ref, str) or ref not in server_names:
                        issue(
                            f"{prefix}.toolServers[{j}]",
                            f"unknown MCP server reference: {ref} (MA-01)",
                        )
        dupes = sorted({n for n in agent_names if n and agent_names.count(n) > 1})
        if dupes:
            issue("agents", f"duplicate sub-agent names: {dupes} (MA-01)")

    auth_mode = doc.get("server.auth.mode")
    if auth_mode == "apiKey" and not (
        doc.get("server.auth.apiKeyEnv") or doc.get("server.auth.apiKeyFile")
    ):
        issue("server.auth.apiKeyEnv", "apiKey auth requires apiKeyEnv or apiKeyFile")
    if auth_mode == "jwt" and (
        not doc.get("server.auth.jwt.issuer") or not doc.get("server.auth.jwt.jwksUrl")
    ):
        issue("server.auth.jwt.jwksUrl", "jwt auth requires jwt.issuer and jwt.jwksUrl")
    if not doc.get("server.auth.jwt.principalClaim"):
        issue("server.auth.jwt.principalClaim", "principalClaim must be non-empty")
    cidrs = doc.raw("server.trustedProxyCidrs", [])
    if isinstance(cidrs, list):
        for i, cidr in enumerate(cidrs):
            try:
                ipaddress.ip_network(str(cidr), strict=False)
            except ValueError:
                issue(f"server.trustedProxyCidrs[{i}]", f"invalid CIDR: {cidr!r}")

    origins = doc.raw("server.corsOrigins", [])
    if isinstance(origins, list) and "*" in origins and doc.get("server.corsAllowCredentials"):
        issue(
            "server.corsAllowCredentials",
            "corsAllowCredentials must be false when corsOrigins contains '*'",
        )

    if not doc.get("server.protocols.openaiCompat") and not doc.get("server.protocols.acp"):
        issue(
            "server.protocols",
            "at least one protocol must be enabled (health-only runtime is invalid)",
        )

    if doc.get("llm.provider") == "ollama" and not doc.get("llm.baseUrl"):
        issue("llm.baseUrl", "ollama provider requires baseUrl")
    if doc.get("llm.vertex.enabled"):
        if doc.get("llm.provider") != "gemini":
            issue("llm.vertex.enabled", "vertex requires provider 'gemini'")
        if not doc.get("llm.vertex.project"):
            issue("llm.vertex.project", "vertex requires a project")
        if doc.get("llm.apiKeyEnv") or doc.get("llm.apiKeyFile"):
            issue("llm.apiKeyEnv", "vertex must not set API-key refs (uses ADC)")
    elif doc.raw("llm.vertex.project") or doc.get("llm.vertex.location") != "us-central1":
        issue("llm.vertex.enabled", "non-default vertex fields require vertex.enabled: true")

    ctx = doc.get("llm.contextWindowTokens")
    max_tokens = doc.get("engine.maxTokens")
    if ctx and max_tokens and ctx <= max_tokens:
        issue("llm.contextWindowTokens", "contextWindowTokens must be 0 or > engine.maxTokens")

    temp_max = doc.get("engine.overrides.temperatureMax")
    temperature = doc.get("engine.temperature")
    if (
        isinstance(temp_max, (int, float))
        and isinstance(temperature, (int, float))
        and temp_max < temperature
    ):
        issue(
            "engine.overrides.temperatureMax",
            "temperatureMax must be >= configured temperature",
        )
    tokens_max = doc.get("engine.overrides.maxTokensMax")
    if tokens_max is not None and max_tokens is not None and tokens_max < max_tokens:
        issue("engine.overrides.maxTokensMax", "maxTokensMax must be >= configured maxTokens")

    msg_bytes = doc.get("server.maxMessageBytes")
    request_bytes = doc.get("server.maxRequestBytes")
    if msg_bytes is not None and request_bytes is not None and msg_bytes > request_bytes:
        issue("server.maxMessageBytes", "maxMessageBytes must be <= maxRequestBytes")

    if doc.get("k8s.required") and not doc.get("k8s.enabled"):
        issue("k8s.enabled", "k8s.required: true requires k8s.enabled: true")


# ---------------------------------------------------------------------------
# CAP-01 capability gating (P1 build)
# ---------------------------------------------------------------------------


def _capability(doc: _Doc, res: Resolution, issues: list[ConfigIssue]) -> None:
    def issue(path: str, msg: str) -> None:
        issues.append(ConfigIssue(path, "capability_error", msg, _tier_of(res, path)))

    if doc.raw("agents"):
        issue("agents", "this P1 build does not implement the multi-agent capability (CAP-01)")
    if doc.get("server.protocols.acp"):
        issue(
            "server.protocols.acp", "this P1 build does not implement the ACP capability (CAP-01)"
        )
    if doc.get("approval.enabled"):
        issue(
            "approval.enabled", "this P1 build does not implement the approval capability (CAP-01)"
        )
    if doc.get("rag.enabled"):
        issue("rag.enabled", "this P1 build does not implement the RAG capability (CAP-01)")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def validate_resolution(res: Resolution) -> ValidationResult:
    issues: list[ConfigIssue] = []

    def tier_of(path: str) -> int:
        p = res.provenance.get(path)
        return p.tier if p else 0

    _shape_walk(res.data, AgentConfig, issues, tier_of)

    config: AgentConfig | None = None
    try:
        config = AgentConfig.model_validate(res.data)
    except ValidationError as exc:
        for err in exc.errors():
            loc = err.get("loc", ())
            path = ".".join(str(part) for part in loc) if loc else ""
            camel_path = _external_path(path)
            if _sensitive_path(camel_path):
                message = "invalid value for secret field (value omitted)"
            else:
                message = err.get("msg", "invalid value")
            issues.append(
                ConfigIssue(
                    camel_path,
                    str(err.get("type", "validation_error")),
                    message,
                    tier_of(camel_path),
                )
            )

    doc = _Doc(res.data)
    _cross_field(doc, res, issues)
    _capability(doc, res, issues)

    # Deduplicate identical findings (e.g. unknown_field from both the shape
    # walk and pydantic's extra="forbid") while keeping every distinct error.
    seen: set[tuple[str, str, str]] = set()
    unique: list[ConfigIssue] = []
    for issue in sorted(issues):
        key = (issue.path, issue.code, issue.message)
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    issues = unique

    return ValidationResult(config=config, issues=issues, warnings=list(res.warnings))


def _tier_of(res: Resolution, path: str) -> int:
    p = res.provenance.get(path)
    return p.tier if p else 0


def _external_path(python_path: str) -> str:
    """Map a pydantic python-name path to camelCase (CFG-13 external form),
    rendering list indexes as ``[i]``."""
    if not python_path:
        return ""
    parts: list[str] = []
    for segment in python_path.split("."):
        if segment.isdigit() and parts:
            parts[-1] += f"[{segment}]"
            continue
        parts.append(_python_to_camel(segment))
    return ".".join(parts)


def _python_to_camel(name: str) -> str:
    if name == "$schema":
        return "$schema"
    words = name.split("_")
    return words[0] + "".join(w.capitalize() for w in words[1:])


def _sensitive_path(path: str) -> bool:
    last = path.split(".")[-1].split("[")[0]
    return redact.is_sensitive_key(last)
