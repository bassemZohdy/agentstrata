"""Engine internals: AgentEvent union, run state machine, public errors.

The internal ``AgentEvent`` union (ENG-02) is the only thing the engine
emits; adapters in §9 convert it to public protocols. The run state machine
(ENG-05) uses compare-and-swap terminal transitions so exactly one outcome
wins under timeout/disconnect/shutdown races. Public errors (ENG-10) are
stable summaries keyed to API-15 codes with no internal detail.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# ---------------------------------------------------------------------------
# ENG-02 internal AgentEvent union
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    result: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class AgentTransfer:
    """MA-04: one ADK transfer between agents (event/debug streams only;
    text mode stays text-only)."""

    from_agent: str
    to_agent: str


@dataclass(frozen=True)
class RagDegraded:
    """RAG-04: the store/embedding was unavailable; the run answers without
    context. Rendered only in events/debug stream modes."""


@dataclass(frozen=True)
class ApprovalRequired:
    """HITL-02: the run paused before a matched tool executed; the approval
    record is durable and the checkpoint holds the exact resume arguments."""

    approval_id: str
    tool_name: str
    preview: str
    expires_at: str
    run_id: str | None = None


@dataclass(frozen=True)
class Iteration:
    index: int


@dataclass(frozen=True)
class Done:
    finish_reason: str = "stop"
    x_agent_status: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class RunError:
    code: str
    message: str


AgentEvent = (
    TextDelta
    | ToolCall
    | ToolResult
    | AgentTransfer
    | Iteration
    | ApprovalRequired
    | RagDegraded
    | Done
    | RunError
)


# ---------------------------------------------------------------------------
# ENG-05 run state machine
# ---------------------------------------------------------------------------


class RunState(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    CANCELLING = "cancelling"
    AWAITING_APPROVAL = "awaiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL = frozenset({RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED})


class RunStateMachine:
    """P1 transitions: created -> running -> succeeded|failed|cancelled, with
    optional running -> cancelling -> cancelled. Exactly one terminal state
    wins (ENG-05); the CAS is enforced by the persistence layer."""

    def __init__(self, initial: RunState = RunState.CREATED) -> None:
        self._state = initial

    @property
    def state(self) -> RunState:
        return self._state

    @property
    def terminal(self) -> bool:
        return self._state in TERMINAL

    def start(self) -> bool:
        if self._state != RunState.CREATED:
            return False
        self._state = RunState.RUNNING
        return True

    def pause_for_approval(self) -> bool:
        """HITL-02: running -> awaiting_approval (non-terminal pause)."""
        if self._state == RunState.RUNNING:
            self._state = RunState.AWAITING_APPROVAL
            return True
        return False

    def begin_cancel(self) -> bool:
        if self._state not in (RunState.RUNNING, RunState.CREATED):
            return False
        self._state = RunState.CANCELLING
        return True

    def _to_terminal(self, target: RunState) -> bool:
        if target not in TERMINAL or self.terminal:
            return False
        if target == RunState.CANCELLED and self._state not in (
            RunState.CANCELLING,
            RunState.RUNNING,
            RunState.CREATED,
        ):
            return False
        if target in (RunState.SUCCEEDED, RunState.FAILED) and self._state not in (
            RunState.RUNNING,
            RunState.CREATED,
            RunState.CANCELLING,
        ):
            return False
        self._state = target
        return True

    def succeed(self) -> bool:
        return self._to_terminal(RunState.SUCCEEDED)

    def fail(self, reason: str = "") -> bool:
        return self._to_terminal(RunState.FAILED)

    def cancel(self) -> bool:
        return self._to_terminal(RunState.CANCELLED)

    def reconcile_after_restart(self) -> RunState:
        """ENG-05: a persistent nonterminal run is never resumed after the
        lease is lost; reconcile to failed (run_interrupted)."""
        if not self.terminal:
            self._state = RunState.FAILED
        return self._state


# ---------------------------------------------------------------------------
# ENG-10 public error codes (stable summaries; API-15 table)
# ---------------------------------------------------------------------------

PUBLIC_ERROR_CODES = {
    "invalid_session_id",
    "session_busy",
    "storage_unavailable",
    "storage_capacity",
    "auth_unavailable",
    "rate_limited",
    "overloaded",
    "provider_auth",
    "provider_unavailable",
    "provider_error",
    "context_length_exceeded",
    "agent_timeout",
    "budget_exceeded",
    "iteration_limit",
    "tool_outcome_unknown",
    "invalid_request",
    "internal_error",
}


class PublicError(Exception):
    """An error whose message is safe to expose publicly (ENG-10)."""

    def __init__(self, code: str, message: str) -> None:
        if code not in PUBLIC_ERROR_CODES:
            raise ValueError(f"unknown public error code {code!r}")
        super().__init__(message)
        self.code = code
        self.message = message


def sanitize_error(exc: BaseException) -> PublicError:
    """Map an internal exception to a stable public summary (ENG-10)."""
    if isinstance(exc, PublicError):
        return exc
    if isinstance(exc, asyncio.CancelledError):
        return PublicError("agent_timeout", "The request was cancelled by timeout or disconnect.")
    if isinstance(exc, TimeoutError):
        return PublicError("agent_timeout", "The request exceeded its deadline.")
    if isinstance(exc, ValueError) and "Transfer target agent" in str(exc):
        # MA-04: a transfer to an unknown/unavailable agent fails the run
        # with provider_error; no silent fallback (ADK raises this from the
        # coordinator's scheduler for unknown transfer targets).
        return PublicError("provider_error", "The requested transfer target agent is unavailable.")
    return PublicError("internal_error", "An internal error occurred; see correlated logs.")
