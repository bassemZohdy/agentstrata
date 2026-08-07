"""CFG-17: schema-derived environment-variable catalog (E1-1/E1-4).

``catalog_rows()`` walks the AgentConfig schema exactly like the env
resolver does (``iter_schema_fields``), attaches schema defaults from an
unvalidated ``model_construct`` instance, SEC-02 secret markers, and the
closed short-alias table from the resolver.  The same code drives the
``--print-env`` CLI action (CFG-10b) and ``scripts/gen-env-reference.py``
so the published ``docs/env-reference.md`` cannot drift from the runtime
catalog.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast

from pydantic import BaseModel
from pydantic_core import PydanticUndefined

from ..security.redact import is_sensitive_key
from .models import AgentConfig, _camel, iter_schema_fields
from .resolver import ENV_ALIASES, camel_to_env_alias

# Distinguishes "no default (required)" from "default is None (optional)".
_REQUIRED = object()


def _default_value(model_cls: type[BaseModel], path: str) -> Any:
    """Field default by dotted ALIAS path, walking the pydantic field
    metadata (model_construct cannot be used: required nested models are
    left unset, which would mislabel defaulted leaves as required)."""
    node: type[BaseModel] = model_cls
    segments = path.split(".")
    for i, seg in enumerate(segments):
        fname = next(
            (n for n, f in node.model_fields.items() if (f.alias or _camel(n)) == seg),
            None,
        )
        if fname is None:
            return _REQUIRED
        field = node.model_fields[fname]
        if i < len(segments) - 1:
            annotation = field.annotation
            if not isinstance(annotation, type) or not issubclass(annotation, BaseModel):
                return _REQUIRED
            node = annotation
            continue
        if field.default is not PydanticUndefined:
            return field.default
        factory = field.default_factory
        if callable(factory):
            return cast(Callable[[], Any], factory)()
        return _REQUIRED
    return _REQUIRED


def _describe(kind: str, ann: Any) -> str:
    if kind == "leaf":
        from typing import get_args, get_origin

        origin = get_origin(ann)
        if origin is not None:  # Optional[...] / str | None
            args = get_args(ann)
            if args:
                return _describe("leaf", args[0])
        name = getattr(ann, "__name__", str(ann))
        if name == "bool":
            return "boolean"
        if name == "int":
            return "integer"
        if name == "float":
            return "number"
        if name == "str":
            return "string"
        return name  # enum / other
    if kind == "model":
        return "model (JSON object)"
    if kind == "list":
        return "list (JSON array)"
    return "map (JSON object)"


def catalog_rows() -> list[dict[str, Any]]:
    """One row per bindable path: canonical env name, path, kind, type,
    default (or ``required``), secret flag, and short aliases."""
    rows: list[dict[str, Any]] = []
    for path, kind, ann, bindable in iter_schema_fields(AgentConfig):
        if not bindable or path.startswith("$"):
            continue  # CFG-07: list-item paths are not bindable; the
            # informational $schema field is not an env surface
        value = _default_value(AgentConfig, path)
        if value is _REQUIRED:
            default = "required"
        elif value is None:
            default = "null"
        elif isinstance(value, BaseModel):
            default = json.dumps(
                value.model_dump(mode="json", by_alias=True),
                sort_keys=True,
                separators=(",", ":"),
            )
        elif isinstance(value, (list, dict)):
            default = json.dumps(value, sort_keys=True, separators=(",", ":"))
        elif isinstance(value, bool):
            default = "true" if value else "false"
        else:
            default = str(value)
        rows.append(
            {
                "env": camel_to_env_alias(path),
                "path": path,
                "kind": kind,
                "type": _describe(kind, ann),
                "default": default,
                "secret": is_sensitive_key(path.rsplit(".", 1)[-1]),
                "aliases": sorted(alias for alias, target in ENV_ALIASES.items() if target == path),
            }
        )
    return rows


def render_catalog() -> str:
    """Human-readable catalog text (CFG-10b stdout format)."""
    rows = catalog_rows()
    lines = [
        "# Agentbase environment-variable reference (CFG-17)",
        "",
        "Schema-derived from `AgentConfig`; regenerate with "
        "`scripts/gen-env-reference.py` (CI zero-diff).",
        "Aliases lose to canonical names (CFG-07); collection items are "
        "not env-bindable — use `AGENT_APPLICATION_JSON` (CFG-08).",
        "",
        "| Variable | Path | Type | Default | Secret |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        alias_text = (
            f" (alias: {', '.join('AGENT_' + a for a in row['aliases'])})" if row["aliases"] else ""
        )
        lines.append(
            f"| `{row['env']}`{alias_text} | `{row['path']}` | {row['type']} "
            f"| `{row['default']}` | {'yes' if row['secret'] else ''} |"
        )
    return "\n".join(lines) + "\n"
