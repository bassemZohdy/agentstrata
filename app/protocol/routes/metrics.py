"""Prometheus text-exposition endpoint (REQUIREMENTS.md OBS-05).

Registered only when ``observability.prometheus.enabled`` is true. The
registry lives on the Observability facade (shared across component
rebuilds), so the endpoint keeps serving the same instrument set across
live reloads. Health probes are never rate-limited; this route follows the
same exemption so scrapers cannot be throttled by the replica-local limiter.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response


def register(app: Any, config: Any, components: dict[str, Any]) -> None:
    path = config.observability.prometheus.path
    router = APIRouter()

    @router.get(path)
    async def metrics(request: Request):
        # OBS-05: the same registry serves every scraper; the render is
        # cheap (string join) and needs no per-request allocation growth.
        observability = components.get("observability")
        registry = getattr(observability, "registry", None)
        if registry is None:
            return Response(
                "# prometheus metrics disabled\n",
                media_type="text/plain; version=0.0.4",
            )
        return Response(
            registry.render(),
            media_type="text/plain; version=0.0.4",
        )

    app.include_router(router)
