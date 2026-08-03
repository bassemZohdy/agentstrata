"""MCP tool filtering, collision-safe renaming, and result handling
(REQUIREMENTS.md MCP-03, MCP-04).
"""

from __future__ import annotations

import json
from typing import Any


def apply_tool_filter(
    raw_names: list[str],
    allow: list[str] | None = None,
    deny: list[str] | None = None,
) -> list[str]:
    """MCP-03: exact-match filter; deny wins over allow."""
    allow = allow or []
    deny = deny or []
    out: list[str] = []
    for name in raw_names:
        if name in deny:
            continue
        if allow and name not in allow:
            continue
        out.append(name)
    return out


def rename_collision_safe(
    raw_names: list[str],
    server_name: str,
) -> list[str]:
    """MCP-03: first unused raw name retained; else ``{server}_{raw}``;
    else ``_2``, ``_3``, ... until unique."""
    used: set[str] = set()
    out: list[str] = []
    for raw in raw_names:
        if raw not in used:
            out.append(raw)
            used.add(raw)
            continue
        candidate = f"{server_name}_{raw}"
        if candidate not in used:
            out.append(candidate)
            used.add(candidate)
            continue
        n = 2
        while True:
            candidate = f"{server_name}_{raw}_{n}"
            if candidate not in used:
                out.append(candidate)
                used.add(candidate)
                break
            n += 1
    return out


def canonical_json(value: Any) -> str:
    """MCP-04: structured results serialize to canonical JSON (stable
    separators, sorted keys); text stays text."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def truncate_codepoint_safe(text: str, max_bytes: int) -> tuple[str, bool]:
    """MCP-04: UTF-8-encode and truncate at a code-point boundary so the
    final bytes (including the suffix) fit ``max_bytes``. Returns
    (truncated, truncated_flag)."""
    suffix = "\n[truncated by runtime]"
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    budget = max_bytes - len(suffix.encode("utf-8"))
    if budget <= 0:
        return suffix[:max_bytes], True
    out: list[str] = []
    used = 0
    for ch in text:
        size = len(ch.encode("utf-8"))
        if used + size > budget:
            break
        out.append(ch)
        used += size
    return "".join(out) + suffix, True


def redact_preview(value: Any, max_codepoints: int = 500) -> str:
    """MCP-04: external event previews capped at 500 code points and
    recursively redacted (SEC-02)."""
    from ...security import redact

    masked = redact.mask_value(value)
    text = masked if isinstance(masked, str) else canonical_json(masked)
    return text[:max_codepoints]


def enforce_max_result_bytes(result: str, max_bytes: int) -> str:
    """MCP-04: admit at most ``max_result_bytes`` UTF-8 bytes into model
    context, truncated at a code-point boundary with the marker."""
    truncated, _ = truncate_codepoint_safe(result, max_bytes)
    return truncated
