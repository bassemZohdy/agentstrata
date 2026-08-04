"""FastAPI app factory (REQUIREMENTS.md API-00, API-01..04, §9).

Creates the runtime's HTTP surface from the validated config and the engine
components. Routes: /healthz, /readyz, /health, /config, /v1/chat/completions
(OpenAI-compatible, streaming + non-streaming), /v1/sessions, /v1/models.
Auth and CORS are wired per config; request IDs and error mapping follow
API-00.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .. import __version__
from ..config.capabilities import capability_status
from .auth import AuthProvider
from .ratelimit import FixedWindowLimiter
from .routes import approvals, chat, health, sessions

logger = logging.getLogger(__name__)


class RunSlotGate:
    """Replica-local in-flight run cap (NFR-03 / server.maxConcurrentRequests).

    ``try_acquire`` is atomic (lock-protected) and never blocks; the chat
    route rejects with 503 ``overloaded`` when the cap is reached, before any
    model work starts. ``release`` is called from the route's teardown paths.
    """

    def __init__(self, limit: int) -> None:

        self._limit = limit
        self._in_flight = 0
        self._lock = asyncio.Lock()

    async def try_acquire(self) -> bool:

        async with self._lock:
            if self._in_flight >= self._limit:
                return False
            self._in_flight += 1
            return True

    def release(self) -> None:
        self._in_flight = max(0, self._in_flight - 1)


@asynccontextmanager
async def _lifespan(app: FastAPI, components: dict[str, Any], port: int) -> AsyncIterator[None]:
    """Startup: the tier-8 watch loop and the MCP reconcilers need a live
    event loop; main.py only constructs them (M8 gate regressions: both
    were never started in production). Also writes the CNT-10 bound-port
    marker (uvicorn binds before the lifespan startup, so the file appears
    only after a successful bind)."""
    import os
    from pathlib import Path

    marker = Path(os.environ.get("AGENT_HEALTH_MARKER", "/tmp/agentbase.ready"))
    with suppress(OSError):
        marker.write_text(str(port), encoding="utf-8")
    watcher = components.get("watcher")
    if watcher is not None and hasattr(watcher, "run"):
        components["watcher_task"] = asyncio.create_task(watcher.run())
    mcp = components.get("mcp")
    if mcp is not None and hasattr(mcp, "start") and not getattr(mcp, "_started", False):
        await mcp.start()
    # HITL-05: the approval reconciler runs at startup (finishes pending
    # records left by a previous process) and then on a fixed interval to
    # enforce approval timeouts against the onTimeout policy.
    runner = components.get("runner")
    if runner is not None and hasattr(runner, "reconcile_pending"):
        try:
            await runner.reconcile_pending()
        except Exception:  # noqa: BLE001 - reconciliation must not block boot
            import logging

            logging.getLogger("app.lifecycle").exception("approval reconcile (startup)")
        interval = max(float(getattr(runner, "_reconcile_interval", 5.0)), 1.0)

        async def _reconcile_loop() -> None:
            while True:
                await asyncio.sleep(interval)
                try:
                    await runner.reconcile_pending()
                except Exception:  # noqa: BLE001
                    logging.getLogger("app.lifecycle").exception("approval reconcile (loop)")

        components["reconcile_task"] = asyncio.create_task(_reconcile_loop())
    yield


def create_app(config: Any, components: dict[str, Any], mode: str = "standalone") -> FastAPI:
    """Build the FastAPI app (API-00 surface-wide contract)."""
    app = FastAPI(
        title="Agentbase",
        version=__version__,
        docs_url=None,  # API-18: documented OpenAPI, no interactive docs by default
        redoc_url=None,
        openapi_url="/openapi.json",
        lifespan=lambda _app: _lifespan(_app, components, config.server.port),
    )

    # NFR-03 / API-15: replica-local in-flight run cap. The chat route
    # acquires one slot per admitted run (before any model work) and answers
    # 503 `overloaded` at the cap. A counter+lock (not a Semaphore) so the
    # cap can be checked atomically without blocking or timeout races.
    components["run_slots"] = RunSlotGate(config.server.maxConcurrentRequests)
    # CNT-07: in-flight run tasks, cancelled at grace expiry so the runner
    # persists terminal states BEFORE storage closes.
    components["run_registry"] = set()

    # API-20: replica-local fixed UTC-minute rate limiter (disabled by default).
    limiter = FixedWindowLimiter.build_if_enabled(config.server.rateLimit)
    if limiter is not None:

        @app.middleware("http")
        async def rate_limit_middleware(request: Request, call_next):
            # API-20: health probes are never rate-limited.
            if request.url.path in ("/healthz", "/readyz"):
                return await call_next(request)
            principal = getattr(request.state, "principal", None)
            allowed, retry_after = limiter.allow(
                FixedWindowLimiter.key_for_request(request, principal)
            )
            if not allowed:
                from .errors import PublicErrorResponse, error_body

                err = PublicErrorResponse(
                    "rate_limited", "Rate limit exceeded for this window", 429
                )
                request_id = getattr(request.state, "request_id", "")
                response = JSONResponse(
                    status_code=429,
                    content=error_body(err.code, err.message, request_id),
                )
                response.headers["Retry-After"] = str(max(retry_after, 1))
                return response
            return await call_next(request)

    # SEC-11: response hardening + SEC-09 trusted-proxy + SEC-10 audit.
    from ..security.audit import HARDENING_HEADERS, audit, parse_forwarded_for

    trusted_cidrs = list(config.server.trustedProxyCidrs)

    @app.middleware("http")
    async def hardening_middleware(request: Request, call_next):
        response = await call_next(request)
        for key, value in HARDENING_HEADERS.items():
            response.headers.setdefault(key, value)
        return response

    # Per-request request id (API-00: every response after scope creation
    # includes X-Request-Id).
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = request_id
        if trusted_cidrs:
            direct = request.client.host if request.client else ""
            client = parse_forwarded_for(
                request.headers.get("x-forwarded-for", ""), trusted_cidrs, direct
            )
            request.state.client_ip = client or direct
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
            audit(
                "auth_failure",
                code=error.code,
                path=request.url.path,
                request_id=request_id,
            )
            return JSONResponse(status_code=error.status, content=error.body(request_id))
        request.state.principal = principal
        return await call_next(request)

    # CORS (SEC-06).
    from fastapi.middleware.cors import CORSMiddleware

    # CORS (SEC-06): exact-origin matching; '*' only with credentials
    # disabled (CFG-14 rejects '*' + credentials at config validation).
    origins = list(config.server.corsOrigins)
    if origins:
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
    approvals.register(app, config, components)
    sessions.register(app, config, components)
    sessions.register_models(app, config)
    # API-16: the ACP surface registers only when enabled; otherwise the
    # paths are ordinary 404s (API-00). CAP-01 still gates enabling it until
    # the P2 acceptance suite passes.
    if getattr(config.server.protocols, "acp", False):
        from .routes.acp import register as register_acp

        register_acp(app, config, components)

    return app


def get_capabilities() -> dict[str, Any]:
    return capability_status()
