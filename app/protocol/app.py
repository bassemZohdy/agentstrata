"""FastAPI app factory (REQUIREMENTS.md API-00, API-01..04, §9).

Creates the runtime's HTTP surface from the validated config and the engine
components. Routes: /healthz, /readyz, /health, /config, /v1/chat/completions
(OpenAI-compatible, streaming + non-streaming), /v1/sessions, /v1/models.
Auth and CORS are wired per config; request IDs and error mapping follow
API-00.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .. import __version__
from ..config.capabilities import capability_status
from .auth import AuthProvider
from .routes import chat, health, sessions

logger = logging.getLogger(__name__)


def create_app(config: Any, components: dict[str, Any], mode: str = "standalone") -> FastAPI:
    """Build the FastAPI app (API-00 surface-wide contract)."""
    app = FastAPI(
        title="AgentStrata",
        version=__version__,
        docs_url=None,  # API-18: documented OpenAPI, no interactive docs by default
        redoc_url=None,
        openapi_url="/openapi.json",
    )

    # Per-request request id (API-00: every response after scope creation
    # includes X-Request-Id).
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response

    # Error mapping (ENG-10 / API-15).
    from .errors import PublicErrorResponse, public_error_handler

    app.add_exception_handler(PublicErrorResponse, public_error_handler)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        request_id = getattr(request.state, "request_id", "")
        logger.exception("unhandled error on %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "message": "internal_error",
                    "type": "internal_error",
                    "code": "internal_error",
                },
                "request_id": request_id,
            },
        )

    # Auth provider (SEC-01/03).
    auth = AuthProvider.from_config(config)

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        if request.url.path in ("/healthz", "/readyz"):
            return await call_next(request)
        principal, error = await auth.authenticate(request)
        if error is not None:
            request_id = getattr(request.state, "request_id", "")
            return JSONResponse(status_code=error.status, content=error.body(request_id))
        request.state.principal = principal
        return await call_next(request)

    # CORS (SEC-06).
    from fastapi.middleware.cors import CORSMiddleware

    origins = list(config.server.corsOrigins)
    if "*" in origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=config.server.corsAllowCredentials,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["*"],
        )
    elif origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=config.server.corsAllowCredentials,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["*"],
        )

    # Routes.
    health.register(app, config, components, mode)
    chat.register(app, config, components)
    sessions.register(app, config, components)
    sessions.register_models(app, config)

    return app


def get_capabilities() -> dict[str, Any]:
    return capability_status()
