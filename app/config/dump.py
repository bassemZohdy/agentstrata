"""Canonical masked YAML dump (REQUIREMENTS.md CFG-11, NFR-05).

Deterministic emitter: schema field order, lexicographically sorted
passthrough/arbitrary-map keys, stable double-quoted scalars, UTF-8/LF, one
final newline, no timestamps, and a winning-source comment on every leaf
(``# tier 7: cli``, ``# default``, ``# tier 5: env (reset-to-default)``).
Secrets are masked per SEC-02. Identical inputs produce byte-identical output.
"""

from __future__ import annotations

import json
from typing import Any, get_args, get_origin

from pydantic import BaseModel

from ..security import redact
from .models import AgentConfig, field_order
from .resolver import Provenance, Resolution


def _quote(text: str) -> str:
    """Stable double-quoted YAML scalar with JSON-style escaping."""
    out = ['"']
    for ch in text:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    if value is None:
        return "null"
    return _quote(str(value))


def _comment(prov: Provenance | None) -> str:
    if prov is None:
        return "# default"
    return f"# {prov.label()}"


def _emit(
    value: dict[str, Any] | list[Any],
    model_cls: type[BaseModel] | None,
    path: str,
    indent: int,
    prov_map: dict[str, Provenance],
    out: list[str],
    list_dash: str = "",
) -> None:
    pad = "  " * indent
    if isinstance(value, list):
        for i, item in enumerate(value):
            child_path = f"{path}[{i}]"
            if isinstance(item, dict):
                _emit(item, model_cls, child_path, indent, prov_map, out, list_dash="- ")
            else:
                out.append(
                    f"{pad}{list_dash}{_scalar(item)}  {_comment(prov_map.get(child_path))}\n"
                )
        return

    if model_cls is not None:
        keys = field_order(model_cls)
        by_alias: dict[str, Any] = {
            field.alias or name: field for name, field in model_cls.model_fields.items()
        }
    else:
        keys = sorted(value)  # passthrough / arbitrary maps: sorted for determinism
        by_alias = {}

    for key in keys:
        if key not in value:
            continue
        child = value[key]
        child_path = f"{path}.{key}" if path else key
        field_info = by_alias.get(key) if model_cls else None
        ann = field_info.annotation if field_info is not None else None
        if isinstance(child, dict):
            inner_model = _dict_value_model(ann)
            out.append(f"{pad}{list_dash}{key}:\n")
            _emit(child, None, child_path, indent + 1, prov_map, out)
            _ = inner_model
        elif isinstance(child, list):
            inner_model = _list_value_model(ann)
            out.append(f"{pad}{list_dash}{key}:\n")
            if inner_model is not None:
                _emit(child, inner_model, child_path, indent + 1, prov_map, out)
            else:
                for i, item in enumerate(child):
                    ipath = f"{child_path}[{i}]"
                    if isinstance(item, dict):
                        _emit(item, None, ipath, indent + 1, prov_map, out, list_dash="- ")
                    else:
                        pad2 = "  " * (indent + 1)
                        out.append(f"{pad2}- {_scalar(item)}  {_comment(prov_map.get(ipath))}\n")
        else:
            out.append(
                f"{pad}{list_dash}{key}: {_scalar(child)}  {_comment(prov_map.get(child_path))}\n"
            )


def _list_value_model(ann: Any) -> type[BaseModel] | None:
    origin = get_origin(ann)
    if origin is list:
        inner = get_args(ann)[0] if get_args(ann) else None
        if isinstance(inner, type) and issubclass(inner, BaseModel):
            return inner
    return None


def _dict_value_model(ann: Any) -> type[BaseModel] | None:
    if ann is None:
        return None
    origin = get_origin(ann)
    if origin is dict:
        inner = get_args(ann)[1] if len(get_args(ann)) > 1 else None
        if isinstance(inner, type) and issubclass(inner, BaseModel):
            return inner
    return None


def dump_config(res: Resolution, config: AgentConfig) -> str:
    """Canonical masked YAML for ``--dump-config`` (CFG-11)."""
    raw = config.model_dump(by_alias=True, mode="json")
    masked = redact.mask_value(raw)
    out: list[str] = []
    _emit(masked, AgentConfig, "", 0, res.provenance, out)
    text = "".join(out)
    if not text.endswith("\n"):
        text += "\n"
    return text
