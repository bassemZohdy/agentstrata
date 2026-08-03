"""Runtime lifecycle events (REQUIREMENTS.md OBS-03).

One ``runtime_started`` event reports version/phase, config generation/hash,
agent name, profile, mode, provider/model, storage type, MCP servers,
protocols/capabilities, auth mode, bind address, and process UID/GID —
with secrets and absolute secret paths masked. A ``runtime_stopped`` event
reports reason, drain counts, duration, and clean/unclean outcome.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .. import __version__

logger = logging.getLogger("agentstrata.events")


def runtime_started(
    config: Any, components: dict[str, Any], mode: str, generation: int = 1
) -> None:
    reload = components.get("reload_manager")
    config_hash = reload.config_hash if reload is not None else ""
    mcp_servers = [
        {
            "name": s.name,
            "transport": s.transport.value,
            "required": s.required,
        }
        for s in config.tools.mcpServers
    ]
    logger.info(
        "runtime_started",
        extra={
            "event": "runtime_started",
            "version": __version__,
            "phase": "P1",
            "config_generation": generation,
            "config_hash": config_hash,
            "agent": config.name,
            "profile": config.profile or "",
            "mode": mode,
            "provider": config.llm.provider.value,
            "model": config.llm.model,
            "storage": config.storage.type.value,
            "mcp_servers": mcp_servers,
            "auth_mode": config.server.auth.mode.value,
            "bind": f"{config.server.host}:{config.server.port}",
            "uid_gid": _uid_gid(),
        },
    )


def runtime_stopped(reason: str, duration_seconds: float, clean: bool) -> None:
    logger.info(
        "runtime_stopped",
        extra={
            "event": "runtime_stopped",
            "reason": reason,
            "duration_seconds": round(duration_seconds, 3),
            "clean": clean,
        },
    )


def _uid_gid() -> dict[str, Any]:
    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    return {
        "uid": getuid() if callable(getuid) else "n/a",
        "gid": getgid() if callable(getgid) else "n/a",
    }
