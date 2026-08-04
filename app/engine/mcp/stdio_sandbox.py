"""MCP stdio sandbox (REQUIREMENTS.md MCP-06).

Stdio processes launch with ``shell=False``. Their environment consists only
of present values from PATH, LANG, LC_ALL, and TMPDIR plus the configured
``env`` map — never the full runtime environment. Each exact ``${VAR}`` is
resolved from the parent environment at connection time; an unset reference
fails that server's connection attempt without revealing the variable value.
"""

from __future__ import annotations

import os
import re
from typing import Any

_MINIMAL_ENV_KEYS = ("PATH", "LANG", "LC_ALL", "TMPDIR")
_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def minimal_stdio_env(
    configured: dict[str, str] | None = None,
    parent: dict[str, str] | None = None,
) -> dict[str, str]:
    """MCP-06: minimal child environment — only present base vars + the
    configured env map."""
    parent = dict(parent) if parent is not None else dict(os.environ)
    env: dict[str, str] = {}
    for key in _MINIMAL_ENV_KEYS:
        value = parent.get(key)
        if value is not None:
            env[key] = value
    env.update(configured or {})
    return env


def interpolate_env_vars(
    env: dict[str, str],
    parent: dict[str, str] | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Resolve exact ``${VAR}`` references from the parent environment at
    connection time. Returns (resolved_env, unresolved_vars); an unset
    reference fails the connection attempt (MCP-06) without revealing the
    variable value."""
    parent = dict(parent) if parent is not None else dict(os.environ)
    resolved: dict[str, str] = {}
    unresolved: list[str] = []
    for key, value in env.items():

        def repl(match: re.Match[str]) -> str:
            var = match.group(1)
            if var in parent:
                return parent[var]
            unresolved.append(var)
            return ""

        resolved[key] = _VAR_RE.sub(repl, value)
    return resolved, unresolved


def build_stdio_params(
    command: str,
    args: list[str],
    configured_env: dict[str, str] | None,
    parent: dict[str, str] | None = None,
) -> tuple[Any, list[str]]:
    """Build mcp StdioServerParameters with shell=False semantics (the SDK
    never uses a shell) and the sandboxed environment. Returns
    (params, unresolved_vars)."""
    from mcp import StdioServerParameters

    env, unresolved = interpolate_env_vars(minimal_stdio_env(configured_env, parent), parent)
    return StdioServerParameters(command=command, args=list(args), env=env), unresolved


def wrap_stdio_params(params: Any, timeout_seconds: float = 30.0) -> Any:
    """Return the ADK connection-params object for a stdio server.

    google-adk 2.6.1 wraps a bare ``StdioServerParameters`` in its own
    ``StdioConnectionParams`` with a hardcoded ``timeout=5`` (seconds) for
    connect + initialize; on slow platforms (cold start, arm64 under QEMU)
    that deadline fires mid-handshake and surfaces as an anyio.WouldBlock
    teardown race. Passing ADK's own class with an explicit timeout is the
    documented way to set it. Falls back to the bare params if the ADK
    class is unavailable in a future version."""
    try:
        from google.adk.tools.mcp_tool import StdioConnectionParams
    except ImportError:  # pragma: no cover - future-ADK fallback
        return params
    return StdioConnectionParams(server_params=params, timeout=timeout_seconds)
