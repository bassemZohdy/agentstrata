"""Session management endpoints (REQUIREMENTS.md API-09).

POST /v1/sessions creates explicitly; GET/DELETE scoped by principal; no
enumeration; identical 404 for unknown/expired/foreign sessions.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ...storage.model import validate_session_id
from ..errors import PublicErrorResponse, error_body


def register(app: Any, config: Any, components: dict[str, Any]) -> None:
    router = APIRouter(prefix="/v1/sessions")
    backend = components["backend"]
    agent_name = config.name

    def principal_of(request: Request) -> str:
        return getattr(request.state, "principal", "anonymous")

    @router.post("")
    async def create_session(request: Request, body: dict | None = None):
        body = body or {}
        session_id = body.get("session_id")
        if session_id is not None and not validate_session_id(session_id):
            raise PublicErrorResponse("invalid_session_id", "invalid session id", 400)
        try:
            record = await backend.create_session(
                agent_name=agent_name,
                principal_id=principal_of(request),
                session_id=session_id,
            )
        except Exception as exc:  # noqa: BLE001
            raise PublicErrorResponse("storage_unavailable", "storage unavailable") from exc
        return JSONResponse(
            status_code=200,
            content={"session_id": record.session_id},
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/{session_id}")
    async def get_session(request: Request, session_id: str):
        record = await backend.get_session(
            agent_name=agent_name,
            principal_id=principal_of(request),
            session_id=session_id,
        )
        if record is None:
            # API-09: identical 404 for unknown/expired/foreign.
            return JSONResponse(
                status_code=404,
                content=error_body(
                    "session_not_found",
                    "session not found",
                    getattr(request.state, "request_id", ""),
                ),
            )
        return JSONResponse(
            content={"session_id": record.session_id, "event_count": len(record.events)},
            headers={"Cache-Control": "no-store"},
        )

    @router.delete("/{session_id}")
    async def delete_session(request: Request, session_id: str):
        try:
            deleted = await backend.delete_session(
                agent_name=agent_name,
                principal_id=principal_of(request),
                session_id=session_id,
            )
        except Exception as exc:  # noqa: BLE001
            raise PublicErrorResponse("session_busy", "session is busy") from exc
        if not deleted:
            return JSONResponse(
                status_code=404,
                content=error_body(
                    "session_not_found",
                    "session not found",
                    getattr(request.state, "request_id", ""),
                ),
            )
        return JSONResponse(status_code=204, content=None)

    app.include_router(router)


def register_models(app: Any, config: Any) -> None:
    """GET /v1/models (API-17): the single configured model."""
    from fastapi import APIRouter

    router = APIRouter(prefix="/v1")

    @router.get("/models")
    async def models(request: Request):
        return JSONResponse(
            content={"object": "list", "data": [{"id": config.llm.model, "object": "model"}]},
            headers={"Cache-Control": "no-store"},
        )

    app.include_router(router)
