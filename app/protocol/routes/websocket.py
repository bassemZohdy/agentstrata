"""WebSocket API (REQUIREMENTS.md WS-01).

Registered only when ``server.protocols.websocket`` is true, at
``/v1/ws``. The same auth as the REST surface (token via ``?token=`` or
the standard Authorization/X-API-Key headers); one active run per
connection; engine events are pushed verbatim (same vocabulary as the SSE
streams) and the client can cancel the run or decide a pending approval
mid-stream — the bidirectional cases SSE cannot express.

Inbound messages (JSON, bounded by ``server.maxMessageBytes``):
  {"type":"run.start","message":...,"sessionId"?, "idempotencyKey"?}
  {"type":"run.cancel","runId"?}         (cancels the connection's active run)
  {"type":"approval.decide","approvalId":...,"decision":"approve|deny",
   "reason"?}
  {"type":"ping","ts"?}

Outbound messages: run.started / run.delta / run.iteration /
run.tool_call / run.tool_result / run.transfer / run.rag_degraded /
approval.required / run.error / run.done / run.cancelled /
approval.decided / error / pong.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.requests import Request

from ...engine.events import (
    AgentTransfer,
    ApprovalRequired,
    Done,
    Iteration,
    RagDegraded,
    RunError,
    TextDelta,
    ToolCall,
    ToolResult,
)
from ...engine.runner import RunRequest
from ...security.audit import audit

logger = logging.getLogger(__name__)

# Producer->consumer sentinel: the run's events are exhausted.
_RUN_DONE = object()

# WS close codes: 1008 policy violation, 1009 too big.
_CLOSE_POLICY = 1008
_CLOSE_TOO_BIG = 1009


def register(app: Any, config: Any, components: dict[str, Any], auth: Any) -> None:
    agent_name = config.name
    router = APIRouter(prefix="/v1")

    @router.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket):
        # WS-01: same auth as the REST surface; browsers cannot set headers,
        # so ``?token=`` is injected as an Authorization bearer before the
        # shared authenticator runs.
        headers = list(websocket.headers.raw)
        token = websocket.query_params.get("token")
        if token and not any(k.lower() == b"authorization" for k, _ in headers):
            headers.append((b"authorization", f"Bearer {token}".encode()))
        scope = {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "ws",
            "path": websocket.url.path,
            "raw_path": websocket.url.path.encode("utf-8"),
            "query_string": websocket.url.query.encode("utf-8"),
            "headers": headers,
            "client": websocket.client,
            "server": None,
            "app": websocket.app,
        }
        principal, failure = await auth.authenticate(Request(scope))
        await websocket.accept()
        if failure is not None:
            audit("auth_failure", code=failure.code, path="/v1/ws")
            await websocket.close(code=_CLOSE_POLICY)
            return
        await _session(websocket, config, components, agent_name, principal)

    app.include_router(router)


async def _session(
    websocket: WebSocket,
    config: Any,
    components: dict[str, Any],
    agent_name: str,
    principal: str,
) -> None:
    """One authenticated connection: an inbound queue + the main loop."""
    inbound: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=8)
    recv_task = asyncio.create_task(_receive_loop(websocket, inbound, config))
    active: dict[str, Any] | None = None
    slots: Any = None
    try:
        while True:
            if active is None:
                get_task = asyncio.create_task(inbound.get())
                done, _pending = await asyncio.wait(
                    {get_task, recv_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if recv_task in done:
                    get_task.cancel()
                    break  # client disconnected
                msg = get_task.result()
                kind = msg.get("type")
                if kind == "ping":
                    await _send(websocket, {"type": "pong", "ts": msg.get("ts")})
                elif kind == "run.start":
                    # Assign via a fresh name so the ``active`` narrowing
                    # survives: a failed start leaves the connection idle.
                    new_slots, new_active = await _start_run(
                        websocket, config, components, agent_name, principal, msg
                    )
                    if new_active is not None:
                        slots = new_slots
                        active = new_active
                elif kind == "approval.decide":
                    await _decide(websocket, components, principal, msg)
                elif kind == "run.cancel":
                    await _send(websocket, {"type": "error", "code": "no_active_run"})
                else:
                    await _send(
                        websocket,
                        {"type": "error", "code": "invalid_message", "message": "unknown type"},
                    )
                continue
            # One active run: race outbound events against inbound messages.
            # ``run`` is the narrowed local: the idle branch always continues,
            # and mypy cannot propagate that narrowing through the loop's own
            # ``active = None`` reassignments.
            run = active
            assert run is not None
            outbound: asyncio.Queue[Any] = run["outbound"]
            get_task = asyncio.create_task(outbound.get())
            recv_wait = asyncio.create_task(inbound.get())
            done, pending = await asyncio.wait(
                {get_task, recv_wait, recv_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if recv_task in done:
                # client went away: cancel the run so the runner commits a
                # terminal state, then tear down. Only the short-lived
                # waiters get cancelled — never the receive loop.
                get_task.cancel()
                recv_wait.cancel()
                _cancel_active(run)
                active = None
                break
            for task in pending:
                if task is not recv_task:
                    task.cancel()
            for task in done:
                item = task.result()
                if task is get_task:
                    if item is _RUN_DONE:
                        producer = run["producer"]
                        if run.get("cancelled"):
                            await _send(
                                websocket,
                                {
                                    "type": "run.cancelled",
                                    "runId": run.get("run_id", ""),
                                    "reason": run.get("cancel_reason", "cancelled"),
                                },
                            )
                        elif producer.done() and producer.exception() is not None:
                            await _send(
                                websocket,
                                {
                                    "type": "run.error",
                                    "runId": run.get("run_id", ""),
                                    "code": "internal_error",
                                    "message": "the run failed unexpectedly",
                                },
                            )
                        if slots is not None:
                            slots.release()
                        active = None
                    elif isinstance(item, dict):
                        await _send(websocket, item)
                elif isinstance(item, dict):
                    kind = item.get("type")
                    if kind == "run.cancel":
                        _cancel_active(run)
                    elif kind == "approval.decide":
                        await _decide(websocket, components, principal, item)
                    elif kind == "ping":
                        await _send(websocket, {"type": "pong", "ts": item.get("ts")})
                    elif kind == "run.start":
                        await _send(
                            websocket,
                            {"type": "error", "code": "run_in_progress"},
                        )
                    else:
                        await _send(
                            websocket,
                            {
                                "type": "error",
                                "code": "invalid_message",
                                "message": "unknown type",
                            },
                        )
    except WebSocketDisconnect:
        pass
    finally:
        if active is not None:
            _cancel_active(active)
            if slots is not None:
                slots.release()
        recv_task.cancel()
        with suppress(BaseException):
            await recv_task


async def _receive_loop(
    websocket: WebSocket, inbound: asyncio.Queue[dict[str, Any]], config: Any
) -> None:
    """Bounded inbound parsing; oversize messages close the connection."""
    try:
        while True:
            raw = await websocket.receive_text()
            if len(raw) > config.server.maxMessageBytes:
                await websocket.close(code=_CLOSE_TOO_BIG)
                return
            try:
                msg = json.loads(raw)
            except ValueError:
                await _send(websocket, {"type": "error", "code": "invalid_message"})
                continue
            if not isinstance(msg, dict):
                await _send(websocket, {"type": "error", "code": "invalid_message"})
                continue
            await inbound.put(msg)
    except WebSocketDisconnect:
        return


async def _start_run(
    websocket: WebSocket,
    config: Any,
    components: dict[str, Any],
    agent_name: str,
    principal: str,
    msg: dict[str, Any],
) -> tuple[Any, dict[str, Any] | None]:
    """WS-01: start one run; returns (slots, active-run record or None)."""
    message = msg.get("message")
    if not isinstance(message, str) or not message.strip():
        await _send(
            websocket,
            {"type": "error", "code": "invalid_message", "message": "message required"},
        )
        return None, None
    slots = components.get("run_slots")
    if slots is not None and not await slots.try_acquire():
        metrics_bundle = components.get("metrics")
        if metrics_bundle is not None:
            metrics_bundle.denials.add(1, {"reason": "concurrency"})
        await _send(websocket, {"type": "error", "code": "overloaded"})
        return None, None
    try:
        run_request = RunRequest(
            principal_id=principal,
            user_message=message,
            session_id=msg.get("sessionId") or None,
            idempotency_key=msg.get("idempotencyKey") or None,
            agent_name=agent_name,
        )
        runner = components["runner"]
        gen = runner.execute(run_request)
        outbound: asyncio.Queue[Any] = asyncio.Queue(maxsize=config.server.streamQueueEvents)
        slow_seconds = config.server.slowConsumerSeconds
        active: dict[str, Any] = {
            "gen": gen,
            "outbound": outbound,
            "run_id": "",
            "cancelled": False,
            "cancel_reason": None,
            "producer": None,
        }

        async def produce() -> None:
            try:
                async for event in gen:
                    if not active["run_id"]:
                        active["run_id"] = getattr(event, "run_id", "") or ""
                    payload = _to_ws_event(event, active["run_id"])
                    try:
                        await asyncio.wait_for(outbound.put(payload), timeout=slow_seconds)
                    except TimeoutError:
                        metrics_bundle = components.get("metrics")
                        if metrics_bundle is not None:
                            metrics_bundle.queue_cancellations.add(1)
                        active["cancelled"] = True
                        active["cancel_reason"] = "slow_consumer"
                        break
                outbound.put_nowait(_RUN_DONE)
            except BaseException:
                with suppress(asyncio.QueueFull):
                    outbound.put_nowait(_RUN_DONE)
                raise

        active["producer"] = asyncio.create_task(produce())
        await _send(websocket, {"type": "run.started"})
        return slots, active
    except Exception as exc:  # noqa: BLE001 — transport/model errors
        logger.exception("ws run.start failed: %s", type(exc).__name__)
        if slots is not None:
            slots.release()
        await _send(websocket, {"type": "error", "code": "internal_error"})
        return None, None


def _cancel_active(active: dict[str, Any]) -> None:
    producer = active.get("producer")
    if producer is not None and not active.get("cancelled"):
        active["cancelled"] = True
        active["cancel_reason"] = "client_requested"
        producer.cancel()


async def _decide(
    websocket: WebSocket,
    components: dict[str, Any],
    principal: str,
    msg: dict[str, Any],
) -> None:
    """WS-01: approval.decide routes to the SAME engine resume as REST."""
    approval_id = msg.get("approvalId")
    decision = msg.get("decision")
    if not isinstance(approval_id, str) or decision not in ("approve", "deny"):
        await _send(
            websocket,
            {
                "type": "error",
                "code": "invalid_message",
                "message": "decision must be approve|deny",
            },
        )
        return
    internal = "approved" if decision == "approve" else "denied"
    outcome = await components["runner"].resume_approval(
        approval_id=approval_id,
        principal_id=principal,
        decision=internal,
        reason=msg.get("reason"),
    )
    if outcome is None:
        await _send(websocket, {"type": "error", "code": "approval_not_found"})
        return
    run_id = outcome.get("run_id", "")
    for event in outcome.get("events", []):
        await _send(websocket, _to_ws_event(event, run_id))
    await _send(
        websocket,
        {
            "type": "approval.decided",
            "approvalId": approval_id,
            "status": outcome.get("status"),
        },
    )


def _to_ws_event(event: Any, run_id: str) -> dict[str, Any]:
    """Engine event -> WS message (same vocabulary as the SSE streams)."""
    if isinstance(event, Iteration):
        return {"type": "run.iteration", "runId": run_id, "index": event.index}
    if isinstance(event, TextDelta):
        return {"type": "run.delta", "runId": run_id, "text": event.text}
    if isinstance(event, ToolCall):
        return {
            "type": "run.tool_call",
            "runId": run_id,
            "callId": event.call_id,
            "name": event.name,
            "args": event.args,
        }
    if isinstance(event, ToolResult):
        return {
            "type": "run.tool_result",
            "runId": run_id,
            "callId": event.call_id,
            "result": event.result,
            "error": event.error,
        }
    if isinstance(event, AgentTransfer):
        return {
            "type": "run.transfer",
            "runId": run_id,
            "fromAgent": event.from_agent,
            "toAgent": event.to_agent,
        }
    if isinstance(event, RagDegraded):
        return {"type": "run.rag_degraded", "runId": run_id}
    if isinstance(event, ApprovalRequired):
        return {
            "type": "approval.required",
            "approvalId": event.approval_id,
            "runId": run_id,
            "toolName": event.tool_name,
            "preview": event.preview,
            "expiresAt": event.expires_at,
        }
    if isinstance(event, RunError):
        return {"type": "run.error", "runId": run_id, "code": event.code, "message": event.message}
    if isinstance(event, Done):
        return {
            "type": "run.done",
            "runId": run_id,
            "finishReason": event.finish_reason,
            "status": event.x_agent_status,
            "usage": event.usage,
        }
    return {"type": "run.event", "runId": run_id}


async def _send(websocket: WebSocket, payload: dict[str, Any]) -> None:
    with suppress(WebSocketDisconnect):
        await websocket.send_json(payload)
