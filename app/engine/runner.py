"""AgentRunner façade (REQUIREMENTS.md ENG-02, ENG-05, ENG-06).

Wraps ADK ``Runner.run_async`` and normalizes ADK ``Event``s into the
internal ``AgentEvent`` union (text_delta/tool_call/tool_result/iteration/
done/error). Enforces the run controls (deadline, iteration/output/token
limits, tool-call dedup), drives the run state machine, and persists the run
record + session history transactionally (admit without history append;
commit pruning+turn+usage only on success).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from asyncio import CancelledError
from collections.abc import AsyncGenerator
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from google.adk.events import Event
from google.adk.runners import Runner as AdkRunner
from google.genai import types as genai_types

from ..storage.contract import (
    SessionNotFound,
    StorageBackend,
)
from ..storage.model import utcnow
from .agent import AppliedConfig
from .events import (
    AgentEvent,
    AgentTransfer,
    Done,
    Iteration,
    PublicError,
    RunError,
    RunState,
    RunStateMachine,
    TextDelta,
    ToolCall,
    ToolResult,
    sanitize_error,
)
from .limits import RunLimiter
from .tools import ToolLedger

logger = logging.getLogger(__name__)


@dataclass
class RunRequest:
    """Normalized request the façade accepts (ENG-02)."""

    principal_id: str
    user_message: str
    session_id: str | None = None
    request_id: str | None = None
    temperature_override: float | None = None
    max_tokens_override: int | None = None
    idempotency_key: str | None = None
    agent_name: str = "agent"
    streaming: bool = False


@dataclass
class RunResult:
    run_id: str
    state: RunState
    finish_reason: str
    x_agent_status: str | None
    text: str
    usage: dict[str, int]
    usage_estimated: bool
    session_id: str
    error_code: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


class AgentRunner:
    """ENG-02 façade; one instance per Applied Config generation."""

    def __init__(
        self,
        applied: AppliedConfig,
        adk_runner: AdkRunner,
        backend: StorageBackend,
        *,
        app_name: str | None = None,
        token_budget_per_session_remaining: int | None = None,
    ) -> None:
        self._applied = applied
        self._adk_runner = adk_runner
        self._backend = backend
        # The storage namespace must match the ADK Runner's app_name so
        # sessions resolve identically on both sides (SES-09).
        self._app_name = app_name or applied.name
        self._session_budget_remaining = token_budget_per_session_remaining

    # -- public entry -----------------------------------------------------------

    async def execute(self, request: RunRequest) -> AsyncGenerator[AgentEvent, None]:
        """Run one request; yields internal AgentEvents (ENG-02)."""
        run_id = str(uuid.uuid4())
        state = RunStateMachine()
        limiter = RunLimiter(
            deadline_monotonic=time.monotonic() + self._applied.timeout_seconds,
            timeout_seconds=self._applied.timeout_seconds,
            max_iterations=self._applied.max_iterations,
            max_output_bytes=self._applied.max_output_bytes,
            token_budget=self._budget_for_request(request),
        )
        ledger = ToolLedger()
        text_parts: list[str] = []
        transfers: list[dict[str, str]] = []

        sid = request.session_id or ""
        admit_revision = 1
        try:
            sid, admit_revision = await self._admit(request, run_id, state, ledger)
            state.start()
            yield Iteration(index=0)

            errored = False
            run_deadline = limiter.deadline_remaining()
            async with asyncio.timeout(max(run_deadline, 0.001)):
                async for adk_event in self._adk_runner.run_async(
                    user_id=request.principal_id,
                    session_id=sid,
                    new_message=self._new_message(request),
                    run_config=self._run_config(request),
                ):
                    if state.terminal:
                        break
                    limiter.check_deadline()
                    async for agent_event in self._convert(
                        adk_event, limiter, ledger, text_parts, transfers
                    ):
                        if isinstance(agent_event, RunError):
                            errored = True
                        yield agent_event
                    if (
                        limiter.iteration_limit_hit
                        or limiter.output_limit_hit
                        or not limiter.can_start_another_call
                    ):
                        break

            error_code: str | None = None
            if limiter.budget_exceeded:
                errored = True
                error_code = "budget_exceeded"

            if errored:
                state.fail()
                await self._backend.truncate_session_events(
                    agent_name=self._app_name,
                    principal_id=request.principal_id,
                    session_id=sid,
                    keep_revision=admit_revision,
                )
                code = error_code or "provider_error"
                await self._commit_failure(request, sid, run_id, state.state, code, transfers)
                yield RunError(code=code, message=_PUBLIC_MESSAGES.get(code, code))
                yield Done(finish_reason="error", x_agent_status=code)
            else:
                finish, status, usage = self._finalize(state, limiter, text_parts, request)
                if state.succeed():
                    await self._commit_success(request, sid, run_id, text_parts, usage, transfers)
                else:
                    await self._commit_failure(
                        request, sid, run_id, state.state, transfers=transfers
                    )
                yield Done(
                    finish_reason=finish,
                    x_agent_status=status,
                    usage={
                        "input_tokens": limiter.account.input_tokens,
                        "output_tokens": limiter.account.output_tokens,
                    },
                )
        except CancelledError:
            self._mark_cancelled(state)
            await self._commit_failure(request, sid, run_id, state.state, transfers=transfers)
            raise
        except GeneratorExit:
            # Generator closed mid-run (API-08a stream teardown): persist a
            # terminal state WITHOUT yielding — yielding during GeneratorExit
            # is illegal and would raise RuntimeError out of aclose().
            if not state.terminal:
                state.fail()
            with suppress(BaseException):
                await self._commit_failure(request, sid, run_id, state.state, transfers=transfers)
            raise
        except Exception as exc:  # noqa: BLE001 — transport/model errors
            logger.exception("run %s failed: %s", run_id, type(exc).__name__)
            # A wrapped timeout (ADK cleanup may re-raise its own error) is
            # still a deadline violation — report agent_timeout (ENG-07).
            if limiter.deadline_remaining() <= 0:
                public = PublicError("agent_timeout", "The request exceeded its deadline.")
            else:
                public = sanitize_error(exc)
            if not state.terminal:
                state.fail()
            with suppress(BaseException):
                await self._commit_failure(
                    request, sid, run_id, state.state, public.code, transfers
                )
            yield RunError(code=public.code, message=public.message)
            yield Done(finish_reason="error", x_agent_status=public.code)

    # -- admission (ENG-03 order; auth/rate-limit stubbed until M5) -----------------

    def _mark_cancelled(self, state: RunStateMachine) -> None:
        """CAS running→cancelling→cancelled; no-op when already terminal."""
        if state.begin_cancel() or not state.terminal:
            state.cancel()

    async def _admit(
        self,
        request: RunRequest,
        run_id: str,
        state: RunStateMachine,
        ledger: ToolLedger,
    ) -> tuple[str, int]:
        # ENG-03 steps 1-4 (request id, auth, capability, rate-limit) are
        # enforced by the M5 adapter; the ordering contract is preserved here.
        # Step 5: resolve/atomically create the session + lease.
        if request.session_id is None:
            record = await self._backend.create_session(
                agent_name=self._app_name,
                principal_id=request.principal_id,
            )
            request.session_id = record.session_id
        else:
            existing = await self._backend.get_session(
                agent_name=self._app_name,
                principal_id=request.principal_id,
                session_id=request.session_id,
            )
            if existing is None:
                existing = await self._backend.create_session(
                    agent_name=self._app_name,
                    principal_id=request.principal_id,
                    session_id=request.session_id,
                )
        # distributed lease (SES-05) — best effort until M6 wiring
        # Step 6: budget eligibility.
        if self._session_budget_remaining is not None and self._session_budget_remaining <= 0:
            raise PublicError("budget_exceeded", "The session token budget was exceeded.")
        # Step 7: run record (ENG-06: admit without appending history).
        assert request.session_id is not None
        admit_record = await self._backend.get_session(
            agent_name=self._app_name,
            principal_id=request.principal_id,
            session_id=request.session_id,
        )
        admit_revision = admit_record.revision if admit_record is not None else 1
        await self._backend.create_run(
            agent_name=self._app_name,
            principal_id=request.principal_id,
            session_id=request.session_id,
            run_id=run_id,
            run_input={"user_message": request.user_message, "request_id": request.request_id},
            now=utcnow(),
        )
        # Step 8: iteration/token controls are enforced in the loop.
        return request.session_id, admit_revision

    def _budget_for_request(self, request: RunRequest) -> int:
        budget = self._applied.token_budget_per_request
        if self._session_budget_remaining is not None:
            budget = (
                min(budget, self._session_budget_remaining)
                if budget > 0
                else self._session_budget_remaining
            )
        return budget

    def _new_message(self, request: RunRequest) -> genai_types.Content:
        return genai_types.Content(role="user", parts=[genai_types.Part(text=request.user_message)])

    def _run_config(self, request: RunRequest) -> Any:
        from google.adk.agents.run_config import RunConfig, StreamingMode

        kwargs: dict[str, Any] = {}
        if request.streaming:
            # API-13/ENG: real model deltas for streaming requests. Without
            # this, ADK calls the model non-streaming and the SSE surface
            # emits one big delta at the end (no first-token streaming).
            kwargs["streaming_mode"] = StreamingMode.SSE
        if request.temperature_override is not None and self._applied.overrides_allow_temperature:
            kwargs["temperature"] = min(
                request.temperature_override, self._applied.overrides_temperature_max
            )
        if request.max_tokens_override is not None and self._applied.overrides_allow_max_tokens:
            kwargs["max_output_tokens"] = min(
                request.max_tokens_override, self._applied.overrides_max_tokens_max
            )
        return RunConfig(**kwargs) if kwargs else None

    # -- event conversion + controls -------------------------------------------------

    async def _convert(
        self,
        adk_event: Event,
        limiter: RunLimiter,
        ledger: ToolLedger,
        text_parts: list[str],
        transfers: list[dict[str, str]],
    ) -> AsyncGenerator[AgentEvent, None]:
        if adk_event.usage_metadata:
            limiter.observe_usage(_usage_dict(adk_event.usage_metadata))
        # MA-04: an ADK transfer action becomes one AgentTransfer event,
        # recorded in the run audit (deduped per (from, to)).
        if adk_event.actions and adk_event.actions.transfer_to_agent:
            entry = {
                "from": adk_event.author or "",
                "to": adk_event.actions.transfer_to_agent,
            }
            if entry not in transfers:
                transfers.append(entry)
                yield AgentTransfer(from_agent=entry["from"], to_agent=entry["to"])
        if adk_event.error_code or adk_event.error_message:
            yield RunError(
                code=adk_event.error_code or "provider_error",
                message="The model call failed.",
            )
            return
        if adk_event.content:
            for part in adk_event.content.parts or []:
                if part.text:
                    allowed = limiter.reserve_output(part.text)
                    if allowed:
                        text_parts.append(allowed)
                        yield TextDelta(text=allowed)
                    if limiter.output_limit_hit:
                        return
                if part.function_call:
                    call = part.function_call
                    record = await ledger.begin(call.id or "", call.name or "")
                    if record.state == "completed":
                        yield ToolResult(call_id=call.id or "", result=record.result)
                        continue
                    if record.state in ("failed", "outcome_unknown"):
                        yield ToolResult(call_id=call.id or "", error=record.error or record.state)
                        continue
                    limiter.end_iteration()
                    yield ToolCall(
                        call_id=call.id or "",
                        name=call.name or "",
                        args=call.args or {},
                    )
                if part.function_response:
                    resp = part.function_response
                    ledger.complete(
                        resp.id or "",
                        resp.response if isinstance(resp.response, str) else None,
                    )
                    yield ToolResult(
                        call_id=resp.id or "",
                        result=resp.response if isinstance(resp.response, str) else None,
                    )
            if adk_event.actions and adk_event.actions.end_of_agent:
                return
        if adk_event.actions and adk_event.actions.requested_tool_confirmations:
            yield RunError(
                code="approval_required",
                message="Approval is a Phase 3 capability.",
            )

    def _finalize(
        self,
        state: RunStateMachine,
        limiter: RunLimiter,
        text_parts: list[str],
        request: RunRequest,
    ) -> tuple[str, str | None, dict[str, int]]:
        # ENG-07: iteration exhaustion / output limit -> length + status.
        # NOTE: does NOT transition the state machine — execute() owns the
        # single CAS terminal transition.
        if limiter.iteration_limit_hit:
            return "length", "iteration_limit", _usage(limiter)
        if limiter.output_limit_hit:
            return "length", "output_limit", _usage(limiter)
        return "stop", None, _usage(limiter)

    async def _commit_success(
        self,
        request: RunRequest,
        session_id: str,
        run_id: str,
        text_parts: list[str],
        usage: dict[str, int],
        transfers: list[dict[str, str]],
    ) -> None:
        """ENG-06: one revision-checked transaction commits pruning + the
        complete turn + usage, and marks the run succeeded."""
        expected = await _current_revision(self._backend, request, self._app_name, session_id)
        await self._backend.mutate_session(
            agent_name=self._app_name,
            principal_id=request.principal_id,
            session_id=session_id,
            expected_revision=expected,
            events=[
                {"role": "user", "parts": [{"text": request.user_message}]},
                {"role": "model", "parts": [{"text": "".join(text_parts)}]},
            ],
            usage=usage,
            now=utcnow(),
        )
        await self._backend.update_run(
            agent_name=self._app_name,
            principal_id=request.principal_id,
            session_id=session_id,
            run_id=run_id,
            status="succeeded",
            outcome={"text": "".join(text_parts), "transfers": transfers},
            usage=usage,
            now=utcnow(),
        )

    async def _commit_failure(
        self,
        request: RunRequest,
        session_id: str,
        run_id: str,
        run_state: RunState,
        error_code: str | None = None,
        transfers: list[dict[str, str]] | None = None,
    ) -> None:
        """ENG-06: persist terminal state + actual usage, append neither the
        user message nor partial assistant text."""
        from contextlib import suppress

        with suppress(SessionNotFound):
            await self._backend.update_run(
                agent_name=self._app_name,
                principal_id=request.principal_id,
                session_id=session_id,
                run_id=run_id,
                status=run_state.value,
                outcome={
                    "error_code": error_code or run_state.value,
                    "transfers": transfers or [],
                },
                now=utcnow(),
            )


def _usage(limiter: RunLimiter) -> dict[str, int]:
    return {
        "input_tokens": limiter.account.input_tokens,
        "output_tokens": limiter.account.output_tokens,
    }


_PUBLIC_MESSAGES = {
    "provider_error": "The model call failed.",
    "budget_exceeded": "The token budget for this request was exceeded.",
    "agent_timeout": "The request exceeded its deadline.",
}


def _token_count(value: Any) -> int:
    """Provider usage metadata is untrusted (TRUST-01): coerce defensively."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _usage_dict(metadata: Any) -> dict[str, int]:
    """ENG-08: extract provider-reported usage from an ADK event."""
    if metadata is None:
        return {}
    if isinstance(metadata, dict):
        return {
            "input_tokens": _token_count(
                metadata.get("promptTokenCount", metadata.get("prompt_token_count", 0))
            ),
            "output_tokens": _token_count(
                metadata.get("candidatesTokenCount", metadata.get("candidates_token_count", 0))
            ),
        }
    return {
        "input_tokens": _token_count(getattr(metadata, "prompt_token_count", 0)),
        "output_tokens": _token_count(getattr(metadata, "candidates_token_count", 0)),
    }


async def _current_revision(
    backend: StorageBackend,
    request: RunRequest,
    app_name: str,
    session_id: str,
) -> int:
    record = await backend.get_session(
        agent_name=app_name,
        principal_id=request.principal_id,
        session_id=session_id,
    )
    return record.revision if record is not None else 1
