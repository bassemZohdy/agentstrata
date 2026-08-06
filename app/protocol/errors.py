"""Public error mapping (REQUIREMENTS.md API-15, ENG-10).

Maps engine/storage/validation errors to HTTP status codes and the
OpenAI-compatible error body. Messages are stable summaries — internal
detail is never exposed.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

STATUS_BY_CODE: dict[str, int] = {
    "invalid_session_id": 400,
    "invalid_request": 400,
    "context_length_exceeded": 400,
    "provider_auth": 401,
    "auth_error": 401,
    "auth_unavailable": 503,
    "session_busy": 409,
    "idempotency_in_progress": 409,
    "storage_unavailable": 503,
    "storage_capacity": 503,
    "rate_limited": 429,
    "overloaded": 503,
    "provider_unavailable": 503,
    "provider_error": 502,
    "agent_timeout": 504,
    "budget_exceeded": 400,
    "iteration_limit": 200,
    "tool_outcome_unknown": 500,
    "internal_error": 500,
    "session_not_found": 404,
}


class PublicErrorResponse(Exception):
    """Raised by route handlers; converted to a JSON error response."""

    def __init__(self, code: str, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status or STATUS_BY_CODE.get(code, 500)


def error_body(code: str, message: str, request_id: str) -> dict[str, Any]:
    return {
        "error": {
            "message": message,
            "type": code,
            "code": code,
        },
        "request_id": request_id,
    }


async def public_error_handler(request: Request, exc: Exception):
    # Registered for PublicErrorResponse; FastAPI's ExceptionHandler contract
    # types the parameter as Exception.
    assert isinstance(exc, PublicErrorResponse)
    request_id = getattr(request.state, "request_id", "")
    return JSONResponse(
        status_code=exc.status,
        content=error_body(exc.code, exc.message, request_id),
    )


def error_mapping() -> dict[type[Exception], Any]:
    return {PublicErrorResponse: public_error_handler}
