"""ACP REST surface (REQUIREMENTS.md API-16 + frozen annex §13.1).

Registered only when ``server.protocols.acp: true`` (otherwise the paths are
ordinary 404s per API-00). Implements the frozen annex: ``GET /acp/agents``
manifest and ``POST /acp/runs`` (non-streaming + SSE streaming) with the P1
event vocabulary plus the P2 ``agent_transfer`` event, auth/session/
idempotency/error behavior per A-5, and the annex golden shapes (A-2/A-4).
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ...engine.runner import RunRequest
from ..errors import PublicErrorResponse
from .chat import (
    _canonical_idempotency_key,
    _collect_non_streaming,
    _finish_idempotency,
    _normalize_usage,
    _read_body,
    _stream,
)

_ACP_FIELDS = {"session_id", "message", "stream", "idempotency_key"}


def _manifest(components: dict[str, Any]) -> dict[str, Any]:
    """Annex A-2: root + sub-agents with their FINAL (post-rename) tools."""
    component = components["agent"]
    root = component.agent

    def _agent_entry(agent: Any) -> dict[str, Any]:
        return {
            "name": agent.name,
            "description": getattr(agent, "description", "") or "",
            "tools": sorted(getattr(t, "name", "") for t in getattr(agent, "tools", []) or []),
        }

    entry = _agent_entry(root)
    entry["object"] = "agent.manifest"
    entry["sub_agents"] = [_agent_entry(sub) for sub in (getattr(root, "sub_agents", None) or [])]
    return entry


def register(app: Any, config: Any, components: dict[str, Any]) -> None:
    agent_name = config.name
    router = APIRouter(prefix="/acp")

    @router.get("/agents")
    async def agents_manifest(request: Request):
        # API-00: X-Request-Id is attached by middleware; body is static.
        return JSONResponse(_manifest(components))

    @router.post("/runs")
    async def create_run(request: Request):
        shutdown = components.get("shutdown")
        if shutdown is not None and shutdown.is_draining():
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "message": "Server is shutting down",
                        "type": "service_unavailable",
                        "code": "service_unavailable",
                    },
                    "request_id": getattr(request.state, "request_id", ""),
                },
            )
        body = await _read_body(request, config)
        request_id = getattr(request.state, "request_id", "")

        # Annex A-3: field subset.
        unknown = set(body) - _ACP_FIELDS
        if unknown:
            raise PublicErrorResponse(
                "invalid_request", f"unsupported fields: {', '.join(sorted(unknown))}", 400
            )

        message = body.get("message")
        if not isinstance(message, dict) or message.get("role") != "user":
            raise PublicErrorResponse(
                "invalid_request", "message must be {'role': 'user', 'content': ...}", 400
            )
        content = message.get("content")
        if not isinstance(content, str) or not content:
            raise PublicErrorResponse("invalid_request", "message.content is required", 400)

        streaming = bool(body.get("stream", False))
        principal = getattr(request.state, "principal", "anonymous")
        # HITL-01: while approval is enabled every run request MUST be
        # stateful; reject a stateless request BEFORE any model work.
        if config.approval.enabled and not body.get("session_id"):
            raise PublicErrorResponse(
                "approval_session_required",
                "approval mode requires a session_id",
                400,
            )

        # Annex A-5: idempotency per API-06a.
        idem_key = _canonical_idempotency_key(body.get("idempotency_key"))
        if idem_key:
            replay = await components["backend"].get_idempotency(
                agent_name=agent_name,
                principal_id=principal,
                session_id=body.get("session_id") or "",
                key=idem_key,
            )
            if replay is not None and replay.status == "completed":
                return JSONResponse(
                    status_code=200,
                    content=_non_streaming_from_replay(replay, request_id),
                    headers={"Cache-Control": "no-store"},
                )

        run_request = RunRequest(
            principal_id=principal,
            user_message=content,
            session_id=body.get("session_id"),
            request_id=request_id,
            idempotency_key=idem_key,
            agent_name=agent_name,
            streaming=streaming,
        )

        if idem_key:
            await components["backend"].create_idempotency(
                agent_name=agent_name,
                principal_id=principal,
                session_id=body.get("session_id") or "",
                key=idem_key,
                ttl_seconds=config.storage.idempotencyTtlSeconds,
            )

        # Admission: same run cap + CNT-07 registry as chat (A-5).
        slots = components.get("run_slots")
        if slots is not None and not await slots.try_acquire():
            raise PublicErrorResponse("overloaded", "Too many concurrent runs", 503) from None
        run_registry = components.get("run_registry")
        current_task = asyncio.current_task()
        if run_registry is not None and current_task is not None:
            run_registry.add(current_task)

        runner = components["runner"]
        if streaming:
            return StreamingResponse(
                _stream(
                    runner,
                    run_request,
                    request,
                    request_id,
                    config,
                    idem_key,
                    components,
                    principal,
                    agent_name,
                    slots,
                ),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
            )

        try:
            text, done, paused = await _collect_non_streaming(runner, run_request)
        finally:
            if slots is not None:
                slots.release()
            if run_registry is not None and current_task is not None:
                run_registry.discard(current_task)
        if paused is not None:
            # HITL-03: non-streaming detach — 202 with the approval
            # reference instead of a 500 (the run is paused, not errored).
            return JSONResponse(
                status_code=202,
                content={
                    "object": "run.pending_approval",
                    "run_id": paused.run_id or run_request.request_id,
                    "session_id": run_request.session_id or "",
                    "approval_id": paused.approval_id,
                    "tool": paused.tool_name,
                    "expires_at": paused.expires_at,
                    "request_id": request_id,
                },
                headers={"Cache-Control": "no-store"},
            )
        if done is None:
            raise PublicErrorResponse("internal_error", "run produced no terminal event")
        if done.x_agent_status in ("iteration_limit", "output_limit"):
            finish_reason = "length"
        elif done.finish_reason == "error":
            finish_reason = "error"
        else:
            finish_reason = "stop"
        result = _acp_completion_body(
            text=text,
            request_id=request_id,
            session_id=run_request.session_id,
            finish_reason=finish_reason,
            x_agent_status=done.x_agent_status,
            usage=done.usage,
        )
        if idem_key:
            await _finish_idempotency(
                components, principal, agent_name, body.get("session_id") or "", idem_key, result
            )
        return JSONResponse(status_code=200, content=result, headers={"Cache-Control": "no-store"})

    app.include_router(router)


def _acp_completion_body(
    *,
    text: str,
    request_id: str,
    session_id: str | None,
    finish_reason: str,
    x_agent_status: str | None,
    usage: dict[str, Any],
) -> dict[str, Any]:
    """Annex A-4 non-streaming run response shape.

    R-14: the usage block is the SAME normalized shape as the chat surface
    (prompt/completion/total_tokens + costUsd when costs.enabled computed
    one) instead of a hand-rolled copy that dropped the cost field.
    """
    return {
        "object": "run.completion",
        "run_id": f"run-{request_id}",
        "session_id": session_id or "",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": finish_reason,
                "x_agent_status": x_agent_status,
            }
        ],
        "usage": _normalize_usage(usage),
        "request_id": request_id,
    }


def _non_streaming_from_replay(replay: Any, request_id: str) -> dict[str, Any]:
    """Annex A-5: a completed idempotency replay returns the stored run
    result (the stored outcome is the full run.completion body)."""
    outcome = replay.outcome or {}
    choices = (outcome.get("choices") or [{}])[0]
    message = choices.get("message") or {}
    return {
        "object": "run.completion",
        "run_id": f"run-{request_id}",
        "session_id": replay.session_id,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": choices.get("finish_reason", "stop"),
                "x_agent_status": choices.get("x_agent_status"),
            }
        ],
        "usage": outcome.get("usage", {}),
        "request_id": request_id,
        "replayed": True,
    }
