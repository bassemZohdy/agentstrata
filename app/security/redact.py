"""Recursive secret redaction (REQUIREMENTS.md SEC-02).

One shared masking utility protects config dumps, ``/config``, logs, traces,
health details, status patches, events, exceptions, and test failure output.
M1 consumes it for ``--dump-config`` masking (CFG-11) and the sensitive-header
check (CFG-14); M5 wires it into every other surface.
"""

from __future__ import annotations

import re
from typing import Any

_SENSITIVE_SUFFIXES = (
    "authorization",
    "cookie",
    "apikey",
    "token",
    "secret",
    "password",
    "credential",
    "privatekey",
    "connectionstring",
)
_REF_SUFFIXES = ("env", "file", "ref")
_STRIP_RE = re.compile(r"[-_.]")

# SEC-02: values become *** in text/YAML and <redacted> in APIs.
MASK_TEXT = "***"
MASK_API = "<redacted>"


def _normalize_key(key: str) -> str:
    return _STRIP_RE.sub("", key).lower()


def is_sensitive_key(key: str) -> bool:
    """True when a key is sensitive per SEC-02 (equals/ends with a sensitive
    base, including the ``env``/``file``/``ref`` suffixes)."""
    n = _normalize_key(str(key))
    for base in _SENSITIVE_SUFFIXES:
        if n == base or n.endswith(base):
            return True
        for suffix in _REF_SUFFIXES:
            if n.endswith(base + suffix):
                return True
    return False


def mask_value(value: Any, *, api: bool = False) -> Any:
    """Recursively mask values whose keys are sensitive. Secret ref contents
    (env/file pairs) are sensitive by key; arbitrary maps (passthrough,
    headers) are masked per key name."""
    mask = MASK_API if api else MASK_TEXT
    if isinstance(value, dict):
        return {
            str(k): (mask if is_sensitive_key(k) else mask_value(v, api=api))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [mask_value(v, api=api) for v in value]
    return value


def sensitive_headers_present(headers: dict[str, Any]) -> list[str]:
    """Keys of a static-header map that violate SEC-02 (must be config errors,
    not silently accepted)."""
    return [k for k in headers if is_sensitive_key(k)]
