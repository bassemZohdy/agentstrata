"""OpenAI-compatible chat (REQUIREMENTS.md API-05 – API-08a, API-12 – API-17).

POST /v1/chat/completions: request validation (API-05), stateful vs stateless
rules (API-06), Idempotency-Key canonicalization/replay (API-06a),
non-streaming and SSE streaming shapes (API-07/08), overrides gating
(API-12), usage reporting, error mapping (API-15).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

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
from ..errors import PublicErrorResponse

_ALLOWED_FIELDS = {
    "model",
    "messages",
    "stream",
    "temperature",
    "max_tokens",
    "max_completion_tokens",
    "session_id",
    "idempotency_key",
}

# Producer->consumer sentinel: the run finished naturally (or was cancelled by
# the producer) and no more events are coming.
_STREAM_DONE = object()


def register(app: Any, config: Any, components: dict[str, Any]) -> None:
    agent_name = config.name

    router = APIRouter(prefix="/v1")

    @router.post("/chat/completions")
    async def chat_completions(request: Request):
        # CNT-07: reject new runs once draining begins (existing runs keep
        # their deadline up to shutdownGraceSeconds).
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

        # API-05: field subset validation.
        unknown = set(body) - _ALLOWED_FIELDS
        if unknown:
            raise PublicErrorResponse(
                "invalid_request",
                f"unsupported fields: {', '.join(sorted(unknown))}",
                400,
            )

        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            raise PublicErrorResponse("invalid_request", "messages is required", 400)
        user_message = _extract_user_message(messages)
        if user_message is None:
            raise PublicErrorResponse(
                "invalid_request", "exactly one user message is required (stateful)", 400
            )

        # Resolve the runner per request: a component-rebuild reload swaps
        # components["runner"] in place (same dict), so new requests must pick
        # up the new generation (NFR-08: later requests use the new config).
        runner = components["runner"]

        streaming = bool(body.get("stream", False))
        principal = getattr(request.state, "principal", "anonymous")
        # HITL-01: while approval is enabled every chat request MUST be
        # stateful; reject a stateless request BEFORE any model work.
        if config.approval.enabled and not body.get("session_id"):
            raise PublicErrorResponse(
                "approval_session_required",
                "approval mode requires a session_id",
                400,
            )

        # API-06a: idempotency — canonicalize the key.
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

        # API-12: overrides gating.
        temperature = body.get("temperature")
        max_tokens = body.get("max_tokens", body.get("max_completion_tokens"))
        temperature_override = None
        max_tokens_override = None
        if temperature is not None:
            if not config.engine.overrides.allowTemperature:
                raise PublicErrorResponse("invalid_request", "temperature overrides disabled", 400)
            try:
                temperature_override = float(temperature)
            except (TypeError, ValueError) as exc:
                raise PublicErrorResponse("invalid_request", "invalid temperature", 400) from exc
        if max_tokens is not None:
            if not config.engine.overrides.allowMaxTokens:
                raise PublicErrorResponse("invalid_request", "max_tokens overrides disabled", 400)
            try:
                max_tokens_override = int(max_tokens)
            except (TypeError, ValueError) as exc:
                raise PublicErrorResponse("invalid_request", "invalid max_tokens", 400) from exc

        run_request = RunRequest(
            principal_id=principal,
            user_message=user_message,
            session_id=body.get("session_id"),
            request_id=request_id,
            temperature_override=temperature_override,
            max_tokens_override=max_tokens_override,
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

        # NFR-03: in-flight run cap (server.maxConcurrentRequests) - reject
        # with 503 `overloaded` (API-15) BEFORE any model work starts.
        slots = components.get("run_slots")
        if slots is not None and not await slots.try_acquire():
            metrics_bundle = components.get("metrics")
            if metrics_bundle is not None:
                metrics_bundle.denials.add(1, {"reason": "concurrency"})
            raise PublicErrorResponse("overloaded", "Too many concurrent runs", 503) from None
        # CNT-07: track the driving task so grace-expiry shutdown can cancel
        # it (persisting a terminal state) before storage closes.
        run_registry = components.get("run_registry")
        current_task = asyncio.current_task()
        if run_registry is not None and current_task is not None:
            run_registry.add(current_task)

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
                headers={
                    "Cache-Control": "no-store",
                    "X-Accel-Buffering": "no",
                },
            )

        try:
            text, done, paused = await _collect_non_streaming(runner, run_request)
        finally:
            if slots is not None:
                slots.release()
            if run_registry is not None and current_task is not None:
                run_registry.discard(current_task)
        if paused is not None:
            # HITL-03: non-streaming detach — 202 with the approval reference;
            # this is the sole exception to API-08a disconnect cancellation.
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
        result = _non_streaming_body(
            text=text,
            model=config.llm.model,
            finish_reason=finish_reason,
            x_agent_status=done.x_agent_status,
            usage=done.usage,
            request_id=request_id,
        )
        if idem_key:
            await _finish_idempotency(
                components, principal, agent_name, body.get("session_id") or "", idem_key, result
            )
        return JSONResponse(status_code=200, content=result, headers={"Cache-Control": "no-store"})

    app.include_router(router)


async def _read_body(request: Request, config: Any) -> dict[str, Any]:
    """API-20: body limits are enforced by the HTTP parser; here we bound
    the decoded size per server.maxRequestBytes."""
    raw = await request.body()
    if len(raw) > config.server.maxRequestBytes:
        raise PublicErrorResponse("invalid_request", "request body too large", 413)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise PublicErrorResponse("invalid_request", "invalid JSON body", 400) from exc
    if not isinstance(data, dict):
        raise PublicErrorResponse("invalid_request", "body must be a JSON object", 400)
    return data


def _extract_user_message(messages: list[Any]) -> str | None:
    """API-06 stateful: exactly one user message; server history is
    authoritative. Returns the user text or None when invalid."""
    user_texts = []
    for message in messages:
        if not isinstance(message, dict):
            return None
        role = message.get("role")
        if role == "user":
            content = message.get("content")
            if isinstance(content, str) and content:
                user_texts.append(content)
            else:
                return None
        elif role not in ("user", "assistant", "system"):
            return None
    if len(user_texts) != 1:
        return None
    return user_texts[0]


def _canonical_idempotency_key(key: Any) -> str | None:
    if not key:
        return None
    text = str(key).strip()
    if not text:
        return None
    # API-06a: canonical form is the SHA-256 of the trimmed key.
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _producer_finished(producer: Any, queue: asyncio.Queue) -> bool:
    """True when the run producer is done and the queue drained (stream end)."""
    return producer.done() and queue.empty()


async def _collect_non_streaming(
    runner, run_request: RunRequest
) -> tuple[str, Done | None, Any | None]:
    text_parts: list[str] = []
    done: Done | None = None
    paused: Any | None = None
    async for event in runner.execute(run_request):
        if isinstance(event, TextDelta):
            text_parts.append(event.text)
        elif isinstance(event, ApprovalRequired):
            paused = event
        elif isinstance(event, Done):
            done = event
    return "".join(text_parts), done, paused


def _cost_usage_fields(usage: dict[str, Any]) -> dict[str, Any]:
    """COST-01: usage.costUsd appears only when costs.enabled computed it."""
    if "cost_usd" not in usage:
        return {}
    return {"costUsd": usage["cost_usd"]}


def _non_streaming_body(
    *,
    text: str,
    model: str,
    finish_reason: str,
    x_agent_status: str | None,
    usage: dict[str, int],
    request_id: str,
) -> dict[str, Any]:
    return {
        "id": f"chatcmpl-{request_id}",
        "object": "chat.completion",
        "created": _now(),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": finish_reason,
                "x_agent_status": x_agent_status,
            }
        ],
        "usage": {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            **_cost_usage_fields(usage),
        },
        "request_id": request_id,
    }


def _non_streaming_from_replay(replay, request_id: str) -> dict[str, Any]:
    outcome = replay.outcome or {}
    return {
        "id": f"chatcmpl-{request_id}",
        "object": "chat.completion",
        "created": _now(),
        "model": outcome.get("model", ""),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": outcome.get("text", "")},
                "finish_reason": outcome.get("finish_reason", "stop"),
            }
        ],
        "usage": outcome.get("usage", {}),
        "request_id": request_id,
        "replayed": True,
    }


async def _stream(
    runner,
    run_request: RunRequest,
    request: Request,
    request_id: str,
    config: Any,
    idem_key: str | None,
    components: Any,
    principal: str,
    agent_name: str,
    slots: Any = None,
) -> AsyncIterator[str]:
    """API-08 + API-08a: SSE delta -> text/extension chunks -> finish -> [DONE].

    A producer task drives ``runner.execute`` into a bounded output queue
    (``server.streamQueueEvents``); this generator is the consumer. The consumer
    polls client-disconnect at a <=1 s cadence; the producer's ``put`` times out
    after ``server.slowConsumerSeconds`` of a full queue. Either trigger
    requests run cancellation within 1 s (the producer task is cancelled, which
    drives the run's CancelledError path; the runner commits a terminal state
    and any lingering nonterminal run is reconciled by the storage sweep).
    After headers are sent, a mid-stream cancellation emits one ``x_agent_event``
    error chunk then ``[DONE]``; HTTP status stays 200 and no nonstandard
    finish reason is used (API-08a). Partial assistant text is never persisted
    (ENG-06 — the runner only commits the turn on success).
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=config.server.streamQueueEvents)
    slow_seconds = config.server.slowConsumerSeconds
    slow_consumer = asyncio.Event()
    gen = runner.execute(run_request)
    # API-13: text = text deltas only; events = + tool_call/tool_result + agent
    # transfer; debug = + iteration events (MA-04: text mode stays text-only).
    stream_mode = config.engine.streaming.value

    async def produce() -> None:
        try:
            async for event in gen:
                try:
                    await asyncio.wait_for(queue.put(event), timeout=slow_seconds)
                except TimeoutError:
                    # Output queue has been full for slowConsumerSeconds: the
                    # client is not keeping up. Record OBS-05 and stop driving
                    # the run; the consumer cancels + tears it down.
                    metrics_bundle = components.get("metrics")
                    if metrics_bundle is not None:
                        metrics_bundle.queue_cancellations.add(1)
                    slow_consumer.set()
                    break
            queue.put_nowait(_STREAM_DONE)
        except BaseException:
            # On cancel/teardown, still unblock the consumer so it can finish.
            with suppress(asyncio.QueueFull):
                queue.put_nowait(_STREAM_DONE)
            raise

    producer = asyncio.create_task(produce())
    assistant_text: list[str] = []
    finish_reason = "stop"
    x_status: str | None = None
    usage: dict[str, int] = {}
    mid_stream_cancel: str | None = None
    try:
        while True:
            # Disconnect poll: the queue.get timeout bounds this to <=1 s.
            if await request.is_disconnected():
                mid_stream_cancel = "client_disconnected"
                break
            try:
                item = await asyncio.wait_for(queue.get(), timeout=1.0)
            except TimeoutError:
                if _producer_finished(producer, queue):
                    if slow_consumer.is_set():
                        mid_stream_cancel = "slow_consumer"
                    break
                continue
            if item is _STREAM_DONE:
                if slow_consumer.is_set():
                    mid_stream_cancel = "slow_consumer"
                break
            event = item
            if isinstance(event, TextDelta):
                assistant_text.append(event.text)
                yield _sse_data(
                    {
                        "id": request_id,
                        "object": "chat.completion.chunk",
                        "created": _now(),
                        "model": config.llm.model,
                        "choices": [
                            {"index": 0, "delta": {"content": event.text}, "finish_reason": None}
                        ],
                    }
                )
            elif isinstance(event, Iteration):
                if stream_mode != "debug":
                    continue
                yield _sse_data(
                    {
                        "id": request_id,
                        "object": "chat.completion.chunk",
                        "created": _now(),
                        "model": config.llm.model,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": None}],
                    }
                )
            elif isinstance(event, ApprovalRequired):
                # HITL-03: SSE emits approval_required then [DONE]; the run
                # DETACHES (the sole API-08a exception) — it is not cancelled.
                yield _sse_data(
                    {
                        "id": request_id,
                        "object": "chat.completion.chunk",
                        "created": _now(),
                        "model": config.llm.model,
                        "choices": [],
                        "approval_required": {
                            "approval_id": event.approval_id,
                            "tool": event.tool_name,
                            "expires_at": event.expires_at,
                            "run_id": event.run_id,
                        },
                    }
                )
                yield "data: [DONE]\n\n"

                return
            elif isinstance(event, RagDegraded):
                # RAG-04: rag_degraded appears only in events/debug mode;
                # text mode and non-streaming stay silent.
                if stream_mode == "text":
                    continue
                yield _sse_data(
                    {
                        "id": request_id,
                        "object": "chat.completion.chunk",
                        "created": _now(),
                        "model": config.llm.model,
                        "choices": [],
                        "rag_degraded": True,
                    }
                )
            elif isinstance(event, AgentTransfer):
                # MA-04: event/debug streams only; text mode stays text-only.
                if stream_mode == "text":
                    continue
                yield _sse_data(
                    {
                        "id": request_id,
                        "object": "chat.completion.chunk",
                        "created": _now(),
                        "model": config.llm.model,
                        "choices": [],
                        "x_agent_event": {
                            "type": "agent_transfer",
                            "from": event.from_agent,
                            "to": event.to_agent,
                        },
                    }
                )
            elif isinstance(event, ToolCall):
                if stream_mode == "text":
                    continue
                yield _sse_data(
                    {
                        "id": request_id,
                        "object": "chat.completion.chunk",
                        "created": _now(),
                        "model": config.llm.model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": event.call_id,
                                            "type": "function",
                                            "function": {
                                                "name": event.name,
                                                "arguments": json.dumps(event.args),
                                            },
                                        }
                                    ]
                                },
                                "finish_reason": None,
                            }
                        ],
                    }
                )
            elif isinstance(event, ToolResult):
                pass  # tool results are not exposed as assistant deltas
            elif isinstance(event, RunError):
                finish_reason = "error"
                x_status = event.code
                yield _sse_data(
                    {"error": {"message": event.message, "type": event.code, "code": event.code}}
                )
            elif isinstance(event, Done):
                finish_reason = event.finish_reason
                x_status = event.x_agent_status
                usage = event.usage
            if slow_consumer.is_set():
                mid_stream_cancel = "slow_consumer"
                break
    finally:
        # Request run cancellation within 1 s: cancel the producer (which drives
        # the run) and close the engine generator. Best-effort teardown — the
        # runner commits a terminal state on CancelledError; any lingering
        # nonterminal run is reconciled by the storage sweep (run_interrupted).
        # NOTE: no yields here — yielding from a finally during generator
        # close (client disconnect) raises RuntimeError. Final chunks are
        # emitted after this block, only on the normal completion path.
        if not producer.done():
            producer.cancel()
        with suppress(BaseException):
            await producer
        with suppress(BaseException):
            await gen.aclose()
        if slots is not None:
            slots.release()
        run_registry = components.get("run_registry")
        current_task = asyncio.current_task()
        if run_registry is not None and current_task is not None:
            run_registry.discard(current_task)

    # API-08a: after headers are sent, a mid-stream cancellation emits one
    # x_agent_event error chunk then [DONE]; status stays 200 and no
    # nonstandard finish reason is used.
    if mid_stream_cancel:
        finish_reason = "stop"
        x_status = None
        usage = {}
        yield _sse_data(
            {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": _now(),
                "model": config.llm.model,
                "choices": [],
                "x_agent_event": {
                    "type": "error",
                    "code": "agent_timeout",
                    "message": "The run was cancelled by timeout or disconnect.",
                },
            }
        )
    final_choice = {"index": 0, "delta": {}, "finish_reason": finish_reason}
    if x_status:
        final_choice["x_agent_status"] = x_status
    yield _sse_data(
        {
            "id": request_id,
            "object": "chat.completion.chunk",
            "created": _now(),
            "model": config.llm.model,
            "choices": [final_choice],
        }
    )
    # A disconnected stream may not receive its usage chunk (API-08a).
    if usage and not mid_stream_cancel:
        yield _sse_data(
            {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": _now(),
                "model": config.llm.model,
                "choices": [],
                "usage": usage,
            }
        )
    yield "data: [DONE]\n\n"
    if idem_key:
        result = _non_streaming_body(
            text="".join(assistant_text),
            model=config.llm.model,
            finish_reason=finish_reason,
            x_agent_status=x_status,
            usage=usage,
            request_id=request_id,
        )
        with suppress(BaseException):
            await _finish_idempotency(
                components,
                principal,
                agent_name,
                run_request.session_id or "",
                idem_key,
                result,
            )


def _now() -> int:
    """Epoch seconds (never throws)."""
    return time.time_ns() // 1_000_000_000


def _sse_data(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


async def _finish_idempotency(
    components: Any,
    principal: str,
    agent_name: str,
    session_id: str,
    idem_key: str,
    outcome: dict[str, Any],
) -> None:
    await components["backend"].finish_idempotency(
        agent_name=agent_name,
        principal_id=principal,
        session_id=session_id,
        key=idem_key,
        status="completed",
        outcome=outcome,
    )
