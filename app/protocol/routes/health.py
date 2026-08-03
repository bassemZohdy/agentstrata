"""Health/metadata endpoints (REQUIREMENTS.md API-01 – API-04).

- GET /healthz — no I/O, live from bind to exit.
- GET /readyz — full readiness rule (config valid, auth key material, storage
  healthy, required MCP connected, required tier-8 synced).
- GET /health — per-component status, degraded vs ok semantics.
- GET /config — Applied Config with recursive redaction (SEC-02).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def register(app: Any, config: Any, components: dict[str, Any], mode: str) -> None:
    router = APIRouter()
    backend = components["backend"]
    mcp = components["mcp"]

    @router.get("/healthz")
    async def healthz():
        # API-01: no I/O; always 200 while the process is live.
        return JSONResponse(status_code=200, content={"status": "ok"})

    @router.get("/readyz")
    async def readyz(request: Request):
        # API-02: full readiness.
        # CNT-07: once draining begins, readiness fails immediately so the
        # platform stops sending new requests while in-flight runs drain.
        shutdown = components.get("shutdown")
        if shutdown is not None and shutdown.is_draining():
            return JSONResponse(
                status_code=503,
                content={
                    "status": "draining",
                    "request_id": getattr(request.state, "request_id", ""),
                },
            )
        storage_ok = await backend.health()
        mcp_ok = mcp.readiness()
        if storage_ok and mcp_ok:
            return JSONResponse(status_code=200, content={"status": "ready"})
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "storage": storage_ok,
                "mcp": mcp_ok,
                "request_id": getattr(request.state, "request_id", ""),
            },
        )

    @router.get("/health")
    async def health(request: Request):
        # API-03: per-component status; degraded vs ok.
        # REL-04: expose the Applied Config generation + configHash.
        reload = components.get("reload_manager")
        generation = reload.generation if reload is not None else 1
        config_hash = reload.config_hash if reload is not None else ""
        storage_ok = await backend.health()
        components_status = {
            "storage": {
                "type": config.storage.type.value,
                "status": "ok" if storage_ok else "unavailable",
            },
            "mcp": {"status": "ok" if mcp.readiness() else "degraded", "servers": mcp.health()},
            "llm": {"status": "unknown"},
            "auth": {"mode": config.server.auth.mode.value},
        }
        overall = "ok" if storage_ok and mcp.readiness() else "degraded"
        return JSONResponse(
            status_code=200,
            content={
                "status": overall,
                "components": components_status,
                "mode": mode,
                "capabilities": _capabilities(),
                "configGeneration": generation,
                "configHash": config_hash,
                "request_id": getattr(request.state, "request_id", ""),
            },
        )

    @router.get("/config")
    async def config_endpoint(request: Request):
        # API-04: Applied Config with recursive redaction (SEC-02); the
        # system instruction is excluded unless exposeSystemInstruction.
        from ...security import redact

        raw = _applied_dump(config, components)
        masked = redact.mask_value(raw, api=True)
        if not config.server.exposeSystemInstruction:
            _strip_system_instruction(masked)
        masked["capabilities"] = _capabilities()
        return JSONResponse(
            content=masked,
            headers={"Cache-Control": "no-store"},
        )

    app.include_router(router)


def _applied_dump(config: Any, components: dict[str, Any]) -> dict[str, Any]:
    return config.model_dump(by_alias=True, mode="json")


def _strip_system_instruction(doc: dict[str, Any]) -> None:
    engine = doc.get("engine")
    if isinstance(engine, dict):
        engine.pop("systemInstruction", None)


def _capabilities() -> dict[str, Any]:
    from ...config.capabilities import capability_status

    return capability_status()
