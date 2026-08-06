"""Storage backend contract (REQUIREMENTS.md SES-01 – SES-08).

Every backend implements :class:`StorageBackend`. Shared errors carry the
public API codes (``session_busy``, ``storage_capacity``,
``storage_unavailable``); the shared contract test suite in
``tests/test_storage`` runs identically against every backend (memory + file
for real; redis/postgres via in-memory substitutes per the approved ACC-01
deviation).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from .model import (
    ApprovalRecord,
    Fence,
    IdempotencyRecord,
    RunRecord,
    SessionRecord,
)

SWEEP_INTERVAL_SECONDS = 600  # SES-06: every 10 minutes


@dataclass
class StorageSettings:
    """SES-06/07 bounds derived from the storage config section."""

    session_ttl_seconds: int = 86400  # 0 disables age expiry (never maxSessions)
    run_ttl_seconds: int = 604800
    idempotency_ttl_seconds: int = 86400
    max_sessions: int = 10000
    max_runs_per_session: int = 1000
    max_idempotency_records_per_session: int = 1000

    @classmethod
    def from_config(cls, config: Any) -> StorageSettings:
        storage = config.storage
        return cls(
            session_ttl_seconds=storage.sessionTtlSeconds,
            run_ttl_seconds=storage.runTtlSeconds,
            idempotency_ttl_seconds=storage.idempotencyTtlSeconds,
            max_sessions=storage.maxSessions,
            max_runs_per_session=storage.maxRunsPerSession,
            max_idempotency_records_per_session=storage.maxIdempotencyRecordsPerSession,
        )


class StorageError(Exception):
    """Base class for storage contract errors."""


class CapacityError(StorageError):
    """A capacity bound was hit (SES-02/07): 503 storage_capacity."""


class RevisionConflict(StorageError):
    """Concurrent mutation on a stale revision (SES-05): 409."""


class SessionBusy(StorageError):
    """Session has a nonterminal run or held fence (SES-05/08): 409."""


class SessionNotFound(StorageError):
    """Unknown/expired/foreign session: identical 404 (SES-06)."""


class InvalidSessionId(StorageError):
    """session_id fails SES-02: 400 invalid_session_id."""


class BackendUnavailableError(StorageError):
    """Backend cannot initialize/ready (SES-04): readiness 503."""


class StorageBackend(ABC):
    """Common contract implemented by all four backends."""

    kind: str = "base"

    # -- lifecycle -----------------------------------------------------------

    @abstractmethod
    async def initialize(self) -> None:
        """Probe/ready the backend (SES-04); raises BackendUnavailableError."""

    @abstractmethod
    async def close(self) -> None:
        """Flush admitted mutations then close (SES-08); failures are logged
        by the caller and treated as exit 1."""

    @abstractmethod
    async def health(self) -> bool:
        """True when the backend can serve stateful requests."""

    # -- sessions (SES-01/02/03) ---------------------------------------------

    @abstractmethod
    async def create_session(
        self,
        *,
        agent_name: str,
        principal_id: str,
        session_id: str | None = None,
        initial_events: list[dict[str, Any]] | None = None,
        now: datetime | None = None,
    ) -> SessionRecord:
        """Atomically create a revision-1 record; enforce maxSessions
        (delete eligible expired sessions first, never evict live/unexpired);
        a same-principal create race produces exactly one record."""

    @abstractmethod
    async def get_session(
        self, *, agent_name: str, principal_id: str, session_id: str
    ) -> SessionRecord | None:
        """Scoped lookup; a record outside the principal namespace is
        indistinguishable from absence (SES-03)."""

    @abstractmethod
    async def mutate_session(
        self,
        *,
        agent_name: str,
        principal_id: str,
        session_id: str,
        expected_revision: int,
        events: list[dict[str, Any]] | None = None,
        usage: dict[str, int] | None = None,
        history_truncated: bool | None = None,
        now: datetime | None = None,
    ) -> SessionRecord:
        """Compare-and-swap mutation on revision (SES-05): a stale
        ``expected_revision`` raises RevisionConflict. Returns the new record
        with revision+1 and updated_at refreshed (SES-06)."""

    @abstractmethod
    async def truncate_session_events(
        self,
        *,
        agent_name: str,
        principal_id: str,
        session_id: str,
        keep_revision: int,
    ) -> None:
        """ENG-06: on failure/cancellation, remove events appended after
        ``keep_revision`` (the pre-run revision) so the failed user message
        and partial assistant text are never committed."""

    @abstractmethod
    async def delete_session(self, *, agent_name: str, principal_id: str, session_id: str) -> bool:
        """Cascade-delete session + runs + idempotency (SES-08); raises
        SessionBusy when a nonterminal run exists; False when absent."""

    @abstractmethod
    async def list_sessions(self, *, agent_name: str, principal_id: str) -> list[SessionRecord]:
        """Per-principal enumeration (used by tests/admin)."""

    # -- runs ----------------------------------------------------------------

    @abstractmethod
    async def create_run(
        self,
        *,
        agent_name: str,
        principal_id: str,
        session_id: str,
        run_id: str,
        run_input: dict[str, Any],
        now: datetime | None = None,
    ) -> RunRecord:
        """Admit a run; enforce maxRunsPerSession by deleting the oldest
        terminal records first (SES-07), else CapacityError."""

    @abstractmethod
    async def admit_run(
        self,
        *,
        agent_name: str,
        principal_id: str,
        session_id: str | None,
        run_id: str,
        run_input: dict[str, Any],
        now: datetime | None = None,
    ) -> tuple[str, int]:
        """Atomic admission (ENG-03 step 7): ensure the session exists
        (minting an id when ``session_id`` is None, enforcing maxSessions)
        and create the run record (enforcing maxRunsPerSession) as ONE
        atomic step — a crash between the two cannot orphan a session.
        Returns ``(session_id, admit_revision)``; the revision is the
        session's revision at admission (used to truncate history on
        failure). Idempotent on ``run_id``: an existing run record returns
        the same session + current revision."""

    @abstractmethod
    async def get_run(
        self, *, agent_name: str, principal_id: str, session_id: str, run_id: str
    ) -> RunRecord | None: ...

    @abstractmethod
    async def update_run(
        self,
        *,
        agent_name: str,
        principal_id: str,
        session_id: str,
        run_id: str,
        status: str | None = None,
        iteration_count: int | None = None,
        outcome: dict[str, Any] | None = None,
        usage: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> RunRecord | None: ...

    @abstractmethod
    async def list_runs(
        self, *, agent_name: str, principal_id: str, session_id: str
    ) -> list[RunRecord]: ...

    # -- idempotency ---------------------------------------------------------

    @abstractmethod
    async def create_idempotency(
        self,
        *,
        agent_name: str,
        principal_id: str,
        session_id: str,
        key: str,
        ttl_seconds: int,
        now: datetime | None = None,
    ) -> IdempotencyRecord:
        """Admit a new idempotency key; at maxIdempotencyRecordsPerSession a
        new key fails CapacityError rather than evicting an unexpired record
        (SES-07)."""

    @abstractmethod
    async def get_idempotency(
        self, *, agent_name: str, principal_id: str, session_id: str, key: str
    ) -> IdempotencyRecord | None: ...

    @abstractmethod
    async def finish_idempotency(
        self,
        *,
        agent_name: str,
        principal_id: str,
        session_id: str,
        key: str,
        status: str,
        outcome: dict[str, Any],
        now: datetime | None = None,
    ) -> IdempotencyRecord | None: ...

    @abstractmethod
    async def expire_idempotency(
        self, *, agent_name: str, principal_id: str, session_id: str, key: str
    ) -> bool: ...

    # -- retention & capacity (SES-06/07) ------------------------------------

    @abstractmethod
    async def sweep(self, *, now: datetime | None = None) -> dict[str, int]:
        """Delete expired sessions (skip live-run/leased), terminal runs older
        than runTtlSeconds, and expired idempotency records. Returns counts."""

    # -- fencing (SES-05) ----------------------------------------------------

    @abstractmethod
    async def acquire_fence(
        self,
        *,
        agent_name: str,
        principal_id: str,
        session_id: str,
        token: str,
        ttl_seconds: float,
        now: datetime | None = None,
    ) -> Fence | None:
        """Atomically acquire the session ownership fence with a fresh
        monotonic fencing number; None when already held (session_busy)."""

    @abstractmethod
    async def renew_fence(
        self,
        *,
        agent_name: str,
        principal_id: str,
        session_id: str,
        token: str,
        ttl_seconds: float,
    ) -> bool:
        """Renew only by token match (SES-05)."""

    @abstractmethod
    async def release_fence(
        self, *, agent_name: str, principal_id: str, session_id: str, token: str
    ) -> bool:
        """Release only by token match."""

    @abstractmethod
    async def current_fence(
        self, *, agent_name: str, principal_id: str, session_id: str
    ) -> Fence | None:
        """Current fence (None when absent or expired)."""

    # -- helpers ---------------------------------------------------------------

    def session_ttl_seconds(self, config: Any) -> int:
        return getattr(config.storage, "sessionTtlSeconds", 86400)

    def run_ttl_seconds(self, config: Any) -> int:
        return getattr(config.storage, "runTtlSeconds", 604800)

    def idempotency_ttl_seconds(self, config: Any) -> int:
        return getattr(config.storage, "idempotencyTtlSeconds", 86400)

    def max_sessions(self, config: Any) -> int:
        return getattr(config.storage, "maxSessions", 10000)

    def max_runs_per_session(self, config: Any) -> int:
        return getattr(config.storage, "maxRunsPerSession", 1000)

    def max_idempotency_records(self, config: Any) -> int:
        return getattr(config.storage, "maxIdempotencyRecordsPerSession", 1000)

    @abstractmethod
    async def find_run(
        self, *, agent_name: str, principal_id: str, run_id: str
    ) -> RunRecord | None:
        """Owner-scoped run lookup by run id (HITL-03 GET /v1/runs/{id})."""

    # -- approvals (HITL-02/04, P3) ------------------------------------------

    @abstractmethod
    async def create_approval(
        self,
        *,
        agent_name: str,
        principal_id: str,
        session_id: str,
        run_id: str,
        approval_id: str,
        config_generation: int,
        server_name: str,
        raw_tool_name: str,
        final_tool_name: str,
        args_hash: str,
        args_preview: str,
        checkpoint: dict[str, Any],
        timeout_seconds: int,
        now: datetime | None = None,
    ) -> ApprovalRecord: ...

    @abstractmethod
    async def get_approval(
        self, *, agent_name: str, principal_id: str, approval_id: str
    ) -> ApprovalRecord | None: ...

    @abstractmethod
    async def list_approvals(
        self, *, agent_name: str, principal_id: str, session_id: str
    ) -> list[ApprovalRecord]: ...

    @abstractmethod
    @abstractmethod
    async def list_all_approvals(self, *, agent_name: str) -> list[ApprovalRecord]:
        """Global owner-scoped scan across sessions (HITL-05 reconciler)."""

    @abstractmethod
    async def decide_approval(
        self,
        *,
        agent_name: str,
        principal_id: str,
        approval_id: str,
        decision: str,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> ApprovalRecord | None:
        """HITL-04 CAS: the FIRST decision wins. Returns the record with the
        applied decision, or None when the race was lost (already decided,
        timed out, cancelled or stale) or the record is unknown."""

    @abstractmethod
    async def expire_approvals(self, *, now: datetime | None = None) -> list[ApprovalRecord]:
        """Sweep pending approvals past their expiry: mark timed_out and
        return them (the engine resumes the runs with the deny/allow policy)."""


class AsyncLock(Protocol):
    async def __aenter__(self) -> Any: ...
    async def __aexit__(self, *exc: Any) -> None: ...
