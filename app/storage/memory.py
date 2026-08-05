"""In-process memory storage backend (REQUIREMENTS.md SES-01, SES-04).

In-process maps + an asyncio lock. Data is lost on restart and not shared
across replicas; the boot path logs that warning (SES-01 table). Fencing is
in-process only (one replica), per SES-05.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from .contract import (
    BackendUnavailableError,
    CapacityError,
    InvalidSessionId,
    RevisionConflict,
    SessionBusy,
    SessionNotFound,
    StorageBackend,
    StorageSettings,
)
from .model import (
    ApprovalRecord,
    Fence,
    IdempotencyRecord,
    RunRecord,
    SessionRecord,
    utcnow,
)

logger = logging.getLogger(__name__)

BOOT_WARNING = (
    "memory storage: data is lost on restart and is not shared across "
    "replicas; production manifests MUST use one replica with this backend (SES-01)"
)


class MemoryBackend(StorageBackend):
    kind = "memory"

    def __init__(self, settings: StorageSettings | None = None) -> None:
        self._settings = settings or StorageSettings()
        self._sessions: dict[tuple[str, str, str], SessionRecord] = {}
        self._runs: dict[tuple[str, str, str, str], RunRecord] = {}
        self._idempotency: dict[tuple[str, str, str, str], IdempotencyRecord] = {}
        self._approvals: dict[tuple[str, str, str], ApprovalRecord] = {}
        self._fences: dict[tuple[str, str, str], Fence] = {}
        self._fence_counters: dict[tuple[str, str, str], int] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    async def initialize(self) -> None:
        logger.warning(BOOT_WARNING)

    async def close(self) -> None:
        self._closed = True

    async def health(self) -> bool:
        return not self._closed

    # -- sessions ---------------------------------------------------------------

    async def create_session(
        self,
        *,
        agent_name: str,
        principal_id: str,
        session_id: str | None = None,
        initial_events: list[dict[str, Any]] | None = None,
        now: datetime | None = None,
    ) -> SessionRecord:
        if self._closed:
            raise BackendUnavailableError("memory storage closed")
        from .model import new_session_id, validate_session_id

        sid = session_id if session_id is not None else new_session_id()
        if not validate_session_id(sid):
            raise InvalidSessionId(f"invalid session_id {sid!r} (SES-02)")
        now = now or utcnow()
        key = (agent_name, principal_id, sid)
        async with self._lock:
            existing = self._sessions.get(key)
            if existing is not None:
                return existing  # SES-02: same-principal create race -> one record
            record = SessionRecord(
                agent_name=agent_name,
                principal_id=principal_id,
                session_id=sid,
                events=list(initial_events or []),
                created_at=now,
                updated_at=now,
            )
            self._sessions[key] = record
            return record

    async def get_session(
        self, *, agent_name: str, principal_id: str, session_id: str
    ) -> SessionRecord | None:
        return self._sessions.get((agent_name, principal_id, session_id))

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
        now = now or utcnow()
        key = (agent_name, principal_id, session_id)
        async with self._lock:
            record = self._sessions.get(key)
            if record is None:
                raise SessionNotFound(f"session {session_id!r} not found")
            if record.revision != expected_revision:
                raise RevisionConflict(f"revision {expected_revision} != current {record.revision}")
            record.events = list(record.events) + list(events or [])
            if usage:
                for k, v in usage.items():
                    record.usage[k] = record.usage.get(k, 0) + v
            if history_truncated is not None:
                record.history_truncated = history_truncated
            record.revision += 1
            record.updated_at = now
            return record

    async def truncate_session_events(
        self,
        *,
        agent_name: str,
        principal_id: str,
        session_id: str,
        keep_revision: int,
    ) -> None:
        key = (agent_name, principal_id, session_id)
        async with self._lock:
            record = self._sessions.get(key)
            if record is None or record.revision <= keep_revision:
                return
            # drop events appended after keep_revision (ENG-06 revert)
            per_rev = record.revision - keep_revision
            record.events = record.events[:-per_rev] if per_rev > 0 else record.events
            record.revision = keep_revision

    async def delete_session(self, *, agent_name: str, principal_id: str, session_id: str) -> bool:
        key = (agent_name, principal_id, session_id)
        async with self._lock:
            record = self._sessions.get(key)
            if record is None:
                return False
            runs = [
                r for (a, p, s, _), r in self._runs.items() if (a, p, s) == key and not r.terminal
            ]
            if runs:
                raise SessionBusy(f"session {session_id!r} has a nonterminal run")
            self._sessions.pop(key, None)
            for rkey in [k for k in self._runs if k[:3] == key]:
                self._runs.pop(rkey, None)
            for ikey in [k for k in self._idempotency if k[:3] == key]:
                self._idempotency.pop(ikey, None)
            self._fences.pop(key, None)
            self._fence_counters.pop(key, None)
            return True

    async def list_sessions(self, *, agent_name: str, principal_id: str) -> list[SessionRecord]:
        prefix = (agent_name, principal_id)
        return [r for (a, p, _), r in self._sessions.items() if (a, p) == prefix]

    # -- runs --------------------------------------------------------------------

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
        now = now or utcnow()
        skey = (agent_name, principal_id, session_id)
        rkey = (agent_name, principal_id, session_id, run_id)
        async with self._lock:
            if skey not in self._sessions:
                raise SessionNotFound(f"session {session_id!r} not found")
            if rkey in self._runs:
                return self._runs[rkey]
            self._enforce_run_capacity(skey, now)
            record = RunRecord(
                agent_name=agent_name,
                principal_id=principal_id,
                session_id=session_id,
                run_id=run_id,
                input=dict(run_input),
                created_at=now,
                updated_at=now,
            )
            self._runs[rkey] = record
            return record

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
        """Atomic admission under ONE lock hold: ensure the session and
        create the run record — no window where either exists alone."""
        if self._closed:
            raise BackendUnavailableError("memory storage closed")
        from .model import new_session_id, validate_session_id

        now = now or utcnow()
        sid = session_id if session_id is not None else new_session_id()
        if not validate_session_id(sid):
            raise InvalidSessionId(f"invalid session_id {sid!r} (SES-02)")
        skey = (agent_name, principal_id, sid)
        rkey = (agent_name, principal_id, sid, run_id)
        async with self._lock:
            session = self._sessions.get(skey)
            if session is None:
                session = SessionRecord(
                    agent_name=agent_name,
                    principal_id=principal_id,
                    session_id=sid,
                    created_at=now,
                    updated_at=now,
                )
                self._sessions[skey] = session
            if rkey in self._runs:
                return sid, session.revision
            self._enforce_run_capacity(skey, now)
            record = RunRecord(
                agent_name=agent_name,
                principal_id=principal_id,
                session_id=sid,
                run_id=run_id,
                input=dict(run_input),
                created_at=now,
                updated_at=now,
            )
            self._runs[rkey] = record
            return sid, session.revision

    async def get_run(
        self, *, agent_name: str, principal_id: str, session_id: str, run_id: str
    ) -> RunRecord | None:
        return self._runs.get((agent_name, principal_id, session_id, run_id))

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
        usage: dict[str, int] | None = None,
        now: datetime | None = None,
    ) -> RunRecord | None:
        now = now or utcnow()
        rkey = (agent_name, principal_id, session_id, run_id)
        async with self._lock:
            record = self._runs.get(rkey)
            if record is None:
                return None
            if status is not None:
                record.status = status
            if iteration_count is not None:
                record.iteration_count = iteration_count
            if outcome is not None:
                record.outcome = dict(outcome)
            if usage:
                for k, v in usage.items():
                    record.usage[k] = record.usage.get(k, 0) + v
            record.updated_at = now
            return record

    async def list_runs(
        self, *, agent_name: str, principal_id: str, session_id: str
    ) -> list[RunRecord]:
        prefix = (agent_name, principal_id, session_id)
        return [r for k, r in self._runs.items() if k[:3] == prefix]

    # -- idempotency ---------------------------------------------------------------

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
        now = now or utcnow()
        skey = (agent_name, principal_id, session_id)
        ikey = (agent_name, principal_id, session_id, key)
        async with self._lock:
            existing = self._idempotency.get(ikey)
            if existing is not None:
                return existing
            self._enforce_idempotency_capacity(skey, now)
            record = IdempotencyRecord(
                agent_name=agent_name,
                principal_id=principal_id,
                session_id=session_id,
                key=key,
                created_at=now,
                expires_at=_expiry(now, ttl_seconds),
            )
            self._idempotency[ikey] = record
            return record

    async def get_idempotency(
        self, *, agent_name: str, principal_id: str, session_id: str, key: str
    ) -> IdempotencyRecord | None:
        return self._idempotency.get((agent_name, principal_id, session_id, key))

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
    ) -> IdempotencyRecord | None:
        ikey = (agent_name, principal_id, session_id, key)
        async with self._lock:
            record = self._idempotency.get(ikey)
            if record is None:
                return None
            record.status = status
            record.outcome = dict(outcome)
            return record

    async def expire_idempotency(
        self, *, agent_name: str, principal_id: str, session_id: str, key: str
    ) -> bool:
        ikey = (agent_name, principal_id, session_id, key)
        async with self._lock:
            if ikey not in self._idempotency:
                return False
            self._idempotency.pop(ikey, None)
            return True

    # -- retention & capacity -------------------------------------------------------

    async def sweep(self, *, now: datetime | None = None) -> dict[str, int]:
        now = now or utcnow()
        stats = {"sessions": 0, "runs": 0, "idempotency": 0}
        async with self._lock:
            for key, session_rec in list(self._sessions.items()):
                if (
                    self._session_expired(session_rec, now)
                    and key not in self._fences
                    and not any(k[:3] == key and not r.terminal for k, r in self._runs.items())
                ):
                    self._sessions.pop(key, None)
                    stats["sessions"] += 1
            run_ttl = self._settings.run_ttl_seconds
            for rkey, run_rec in list(self._runs.items()):
                if run_rec.terminal and (now - run_rec.updated_at).total_seconds() > run_ttl:
                    self._runs.pop(rkey, None)
                    stats["runs"] += 1
            for ikey, idem_rec in list(self._idempotency.items()):
                if idem_rec.expires_at is not None and idem_rec.expires_at <= now:
                    self._idempotency.pop(ikey, None)
                    stats["idempotency"] += 1
        return stats

    # -- fencing ----------------------------------------------------------------------

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
        now = now or utcnow()
        key = (agent_name, principal_id, session_id)
        async with self._lock:
            current = self._fences.get(key)
            if current is not None and (current.expires_at is None or current.expires_at > now):
                return None
            number = self._fence_counters.get(key, 0) + 1
            self._fence_counters[key] = number
            fence = Fence(
                token=token,
                fencing_number=number,
                expires_at=now + timedelta(seconds=ttl_seconds),
            )
            self._fences[key] = fence
            return fence

    async def renew_fence(
        self,
        *,
        agent_name: str,
        principal_id: str,
        session_id: str,
        token: str,
        ttl_seconds: float,
    ) -> bool:
        key = (agent_name, principal_id, session_id)
        async with self._lock:
            current = self._fences.get(key)
            if current is None or current.token != token:
                return False
            current.expires_at = utcnow() + timedelta(seconds=ttl_seconds)
            return True

    async def release_fence(
        self, *, agent_name: str, principal_id: str, session_id: str, token: str
    ) -> bool:
        key = (agent_name, principal_id, session_id)
        async with self._lock:
            current = self._fences.get(key)
            if current is None or current.token != token:
                return False
            self._fences.pop(key, None)
            return True

    async def current_fence(
        self, *, agent_name: str, principal_id: str, session_id: str
    ) -> Fence | None:
        return self._fences.get((agent_name, principal_id, session_id))

    # -- helpers ------------------------------------------------------------------------

    def _session_expired(self, record: SessionRecord, now: datetime) -> bool:
        ttl = self._settings.session_ttl_seconds
        if ttl == 0:
            return False  # SES-06: 0 disables age expiry, never maxSessions
        return (now - record.updated_at).total_seconds() > ttl

    def _enforce_run_capacity(self, skey: tuple[str, str, str], now: datetime) -> None:
        runs = [r for k, r in self._runs.items() if k[:3] == skey]
        cap = self._settings.max_runs_per_session
        terminal = sorted([r for r in runs if r.terminal], key=lambda r: r.updated_at)
        while len(runs) >= cap and terminal:
            self._runs.pop((*skey, terminal[0].run_id), None)
            terminal.pop(0)
            runs = [r for k, r in self._runs.items() if k[:3] == skey]
        if len(runs) >= cap:
            raise CapacityError("maxRunsPerSession reached; cannot free capacity")

    def _enforce_idempotency_capacity(self, skey: tuple[str, str, str], now: datetime) -> None:
        records = [r for k, r in self._idempotency.items() if k[:3] == skey]
        if len(records) >= self._settings.max_idempotency_records_per_session:
            raise CapacityError("maxIdempotencyRecordsPerSession reached")

    async def find_run(
        self, *, agent_name: str, principal_id: str, run_id: str
    ) -> RunRecord | None:
        for (a, p, _s, r), record in self._runs.items():
            if a == agent_name and p == principal_id and r == run_id:
                return record
        return None

    # -- approvals (HITL-02/04) ----------------------------------------------

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
    ) -> ApprovalRecord:
        from .model import ApprovalRecord as _AR

        now = now or utcnow()
        record = _AR(
            agent_name=agent_name,
            principal_id=principal_id,
            session_id=session_id,
            run_id=run_id,
            approval_id=approval_id,
            config_generation=config_generation,
            server_name=server_name,
            raw_tool_name=raw_tool_name,
            final_tool_name=final_tool_name,
            args_hash=args_hash,
            args_preview=args_preview,
            checkpoint=checkpoint,
            timeout_seconds=timeout_seconds,
            created_at=now,
            expires_at=now + __import__("datetime").timedelta(seconds=timeout_seconds),
        )
        self._approvals[(agent_name, principal_id, approval_id)] = record
        return record

    async def get_approval(
        self, *, agent_name: str, principal_id: str, approval_id: str
    ) -> ApprovalRecord | None:
        return self._approvals.get((agent_name, principal_id, approval_id))

    async def list_approvals(
        self, *, agent_name: str, principal_id: str, session_id: str
    ) -> list[ApprovalRecord]:
        return [
            r
            for r in self._approvals.values()
            if r.agent_name == agent_name
            and r.principal_id == principal_id
            and r.session_id == session_id
        ]

    async def list_all_approvals(self, *, agent_name: str) -> list[ApprovalRecord]:
        return sorted(
            (r for (a, _p, _i), r in self._approvals.items() if a == agent_name),
            key=lambda r: r.created_at,
        )

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
        """HITL-04 CAS: the first decision wins; expired pendings are
        owned by the sweep."""
        now = now or utcnow()
        record = self._approvals.get((agent_name, principal_id, approval_id))
        if record is None or not record.pending:
            return None
        if now > record.expires_at and decision != "timed_out":
            return None
        record.status = decision
        record.reason = reason
        record.decided_at = now
        record.revision += 1
        return record

    async def expire_approvals(self, *, now: datetime | None = None) -> list[ApprovalRecord]:
        now = now or utcnow()
        expired: list[ApprovalRecord] = []
        for record in list(self._approvals.values()):
            if record.pending and now > record.expires_at:
                record.status = "timed_out"
                record.decided_at = now
                record.revision += 1
                expired.append(record)
        return expired


def _expiry(now: datetime, ttl_seconds: int) -> datetime:
    return now + timedelta(seconds=ttl_seconds)
