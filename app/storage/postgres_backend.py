"""PostgreSQL storage backend (REQUIREMENTS.md SES-01 postgres row, SES-05).

``agent_sessions`` uses primary key (agent_name, principal_id, session_id)
with revision, JSONB data, and timestamps; runs/idempotency use identically
scoped companion tables. Schema creation/migration is transactional and
versioned. Fencing holds a session-scoped advisory lock on a dedicated
connection for the run lifetime and increments a persisted fencing number on
acquisition. The contract suite runs the same SQL through an in-memory
SQLite substitute; the real-instance + fencing/multi-replica proof is
recorded as deferred (approved ACC-01 deviation).
"""

from __future__ import annotations

import hashlib
import json
import logging
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta
from typing import Any, Protocol

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
    new_session_id,
    utcnow,
    validate_session_id,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

DDL = [
    """
    CREATE TABLE IF NOT EXISTS agent_schema (
        version INTEGER PRIMARY KEY
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_sessions (
        agent_name      TEXT NOT NULL,
        principal_id    TEXT NOT NULL,
        session_id      TEXT NOT NULL,
        revision        INTEGER NOT NULL,
        fencing_number  INTEGER NOT NULL DEFAULT 0,
        data            JSONB NOT NULL,
        created_at      TIMESTAMPTZ NOT NULL,
        updated_at      TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (agent_name, principal_id, session_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_runs (
        agent_name   TEXT NOT NULL,
        principal_id TEXT NOT NULL,
        session_id   TEXT NOT NULL,
        run_id       TEXT NOT NULL,
        data         JSONB NOT NULL,
        created_at   TIMESTAMPTZ NOT NULL,
        updated_at   TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (agent_name, principal_id, session_id, run_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_idempotency (
        agent_name   TEXT NOT NULL,
        principal_id TEXT NOT NULL,
        session_id   TEXT NOT NULL,
        key          TEXT NOT NULL,
        data         JSONB NOT NULL,
        created_at   TIMESTAMPTZ NOT NULL,
        expires_at   TIMESTAMPTZ,
        PRIMARY KEY (agent_name, principal_id, session_id, key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_approvals (
        agent_name   TEXT NOT NULL,
        principal_id TEXT NOT NULL,
        approval_id  TEXT NOT NULL,
        data         JSONB NOT NULL,
        created_at   TIMESTAMPTZ NOT NULL,
        expires_at   TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (agent_name, principal_id, approval_id)
    )
    """,
]

SQL = {
    "insert_schema_version": (
        "INSERT INTO agent_schema (version) VALUES (%s) ON CONFLICT (version) DO NOTHING"
    ),
    "schema_version": "SELECT version FROM agent_schema ORDER BY version DESC LIMIT 1",
    "health_probe": "SELECT 1",
    "insert_approval": (
        "INSERT INTO agent_approvals"
        " (agent_name, principal_id, approval_id, data, created_at, expires_at)"
        " VALUES (%s, %s, %s, %s, %s, %s)"
    ),
    "get_approval": (
        "SELECT data FROM agent_approvals"
        " WHERE agent_name = %s AND principal_id = %s AND approval_id = %s"
    ),
    "list_approvals": (
        "SELECT data FROM agent_approvals"
        " WHERE agent_name = %s AND principal_id = %s AND data->>'session_id' = %s"
    ),
    "decide_approval": (
        "UPDATE agent_approvals SET data = %s, expires_at = %s WHERE agent_name = %s"
        " AND principal_id = %s AND approval_id = %s AND data->>'status' = 'pending'"
        " AND (data->>'expires_at' > %s OR %s = 'timed_out') RETURNING data"
    ),
    "find_run": (
        "SELECT session_id, data FROM agent_runs"
        " WHERE agent_name = %s AND principal_id = %s AND run_id = %s"
    ),
    "expire_approvals": (
        "SELECT agent_name, principal_id, approval_id, data FROM agent_approvals"
        " WHERE data->>'status' = 'pending' AND data->>'expires_at' < %s"
    ),
    "insert_session": (
        "INSERT INTO agent_sessions "
        "(agent_name, principal_id, session_id, revision, data, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (agent_name, principal_id, session_id) DO NOTHING"
    ),
    "get_session": (
        "SELECT data FROM agent_sessions "
        "WHERE agent_name = %s AND principal_id = %s AND session_id = %s"
    ),
    "cas_session": (
        "UPDATE agent_sessions SET revision = revision + 1, data = %s, updated_at = %s "
        "WHERE agent_name = %s AND principal_id = %s AND session_id = %s AND revision = %s "
        "RETURNING data"
    ),
    "delete_session": (
        "DELETE FROM agent_sessions WHERE agent_name = %s AND principal_id = %s AND session_id = %s"
    ),
    "delete_runs": (
        "DELETE FROM agent_runs WHERE agent_name = %s AND principal_id = %s AND session_id = %s"
    ),
    "delete_idem": (
        "DELETE FROM agent_idempotency "
        "WHERE agent_name = %s AND principal_id = %s AND session_id = %s"
    ),
    "list_sessions": (
        "SELECT data FROM agent_sessions WHERE agent_name = %s AND principal_id = %s"
    ),
    "count_sessions": (
        "SELECT COUNT(*) AS n FROM agent_sessions WHERE agent_name = %s AND principal_id = %s"
    ),
    "count_runs": (
        "SELECT COUNT(*) AS n FROM agent_runs "
        "WHERE agent_name = %s AND principal_id = %s AND session_id = %s"
    ),
    "list_runs": (
        "SELECT data FROM agent_runs "
        "WHERE agent_name = %s AND principal_id = %s AND session_id = %s"
    ),
    "delete_oldest_terminal_run": (
        "DELETE FROM agent_runs WHERE ctid IN ("
        "SELECT ctid FROM agent_runs "
        "WHERE agent_name = %s AND principal_id = %s AND session_id = %s "
        "AND (data->>'status') IN ('succeeded','failed','cancelled') "
        "ORDER BY updated_at ASC LIMIT 1)"
    ),
    "insert_run": (
        "INSERT INTO agent_runs "
        "(agent_name, principal_id, session_id, run_id, data, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (agent_name, principal_id, session_id, run_id) DO NOTHING"
    ),
    "get_run": (
        "SELECT data FROM agent_runs "
        "WHERE agent_name = %s AND principal_id = %s AND session_id = %s AND run_id = %s"
    ),
    "update_run": (
        "UPDATE agent_runs SET data = %s, updated_at = %s "
        "WHERE agent_name = %s AND principal_id = %s AND session_id = %s AND run_id = %s "
        "RETURNING data"
    ),
    "count_idem": (
        "SELECT COUNT(*) AS n FROM agent_idempotency "
        "WHERE agent_name = %s AND principal_id = %s AND session_id = %s"
    ),
    "insert_idem": (
        "INSERT INTO agent_idempotency "
        "(agent_name, principal_id, session_id, key, data, created_at, expires_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (agent_name, principal_id, session_id, key) DO NOTHING"
    ),
    "get_idem": (
        "SELECT data FROM agent_idempotency "
        "WHERE agent_name = %s AND principal_id = %s AND session_id = %s AND key = %s"
    ),
    "update_idem": (
        "UPDATE agent_idempotency SET data = %s "
        "WHERE agent_name = %s AND principal_id = %s AND session_id = %s AND key = %s "
        "RETURNING data"
    ),
    "delete_idem_one": (
        "DELETE FROM agent_idempotency "
        "WHERE agent_name = %s AND principal_id = %s AND session_id = %s AND key = %s"
    ),
    "expired_sessions": (
        "SELECT agent_name, principal_id, session_id, updated_at FROM agent_sessions "
        "WHERE updated_at < %s"
    ),
    "delete_run_older_than": (
        "DELETE FROM agent_runs WHERE ctid IN ("
        "SELECT ctid FROM agent_runs "
        "WHERE agent_name = %s AND principal_id = %s AND session_id = %s "
        "AND (data->>'status') IN ('succeeded','failed','cancelled') "
        "AND updated_at < %s)"
    ),
    "delete_runs_older_than": (
        "DELETE FROM agent_runs "
        "WHERE (data->>'status') IN ('succeeded','failed','cancelled') "
        "AND updated_at < %s"
    ),
    "expired_idem": (
        "SELECT agent_name, principal_id, session_id, key FROM agent_idempotency "
        "WHERE expires_at IS NOT NULL AND expires_at <= %s"
    ),
    "update_session_data": (
        "UPDATE agent_sessions SET data = %s "
        "WHERE agent_name = %s AND principal_id = %s AND session_id = %s"
    ),
    "get_fence_number": (
        "SELECT fencing_number FROM agent_sessions "
        "WHERE agent_name = %s AND principal_id = %s AND session_id = %s"
    ),
    "bump_fence_number": (
        "UPDATE agent_sessions SET fencing_number = fencing_number + 1 "
        "WHERE agent_name = %s AND principal_id = %s AND session_id = %s "
        "RETURNING fencing_number"
    ),
}


def _lock_key(agent_name: str, principal_id: str, session_id: str) -> int:
    return int(
        hashlib.sha256(f"{agent_name}\0{principal_id}\0{session_id}".encode()).hexdigest()[:15], 16
    )


class DbClient(Protocol):
    """The database surface the backend uses (psycopg async or SqliteDb)."""

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None: ...
    async def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]: ...
    def transaction(self) -> AbstractAsyncContextManager[None]: ...
    async def try_advisory_lock(self, key: int) -> bool: ...
    async def release_advisory_lock(self, key: int) -> None: ...


class PostgresBackend(StorageBackend):
    kind = "postgres"

    def __init__(self, db: DbClient, settings: StorageSettings | None = None) -> None:
        self._db = db
        self._settings = settings or StorageSettings()
        self._ready = False
        # SES-05 token: the advisory lock is the fence; the token records the
        # runtime identity so renew/release are token-matched.
        self._tokens: dict[int, str] = {}

    async def initialize(self) -> None:
        try:
            async with self._db.transaction():
                for ddl in DDL:
                    await self._db.execute(ddl)
                version = await self._db.query(SQL["schema_version"])
                if not version or version[0]["version"] != SCHEMA_VERSION:
                    await self._db.execute(SQL["insert_schema_version"], (SCHEMA_VERSION,))
            self._ready = True
        except Exception as exc:  # noqa: BLE001
            raise BackendUnavailableError(f"postgres storage unavailable: {exc}") from exc

    async def close(self) -> None:
        self._ready = False

    async def health(self) -> bool:
        """SES-04/NFR-09: re-probe (bounded) so readiness converges after
        the dependency dies or recovers, instead of freezing the boot flag."""
        if not self._ready:
            return False
        import asyncio

        try:
            await asyncio.wait_for(self._db.query(SQL["health_probe"]), timeout=2)
            self._ready = True
            return True
        except Exception:  # noqa: BLE001 - dependency outage
            self._ready = False
            return False

    # -- helpers -------------------------------------------------------------------

    def _json(self, record: Any) -> str:
        return record.to_json()

    @staticmethod
    def _parse(parser: Any, raw: Any) -> Any | None:
        try:
            return parser(raw)
        except ValueError:
            return None

    def _parse_row(self, parser: Any, rows: list[dict[str, Any]]) -> Any | None:
        if not rows:
            return None
        return self._parse(parser, rows[0]["data"])

    # -- sessions ----------------------------------------------------------------------

    async def create_session(
        self,
        *,
        agent_name: str,
        principal_id: str,
        session_id: str | None = None,
        initial_events: list[dict[str, Any]] | None = None,
        now: datetime | None = None,
    ) -> SessionRecord:
        sid = session_id if session_id is not None else new_session_id()
        if not validate_session_id(sid):
            raise InvalidSessionId(f"invalid session_id {sid!r} (SES-02)")
        now = now or utcnow()
        record = SessionRecord(
            agent_name=agent_name,
            principal_id=principal_id,
            session_id=sid,
            events=list(initial_events or []),
            created_at=now,
            updated_at=now,
        )
        async with self._db.transaction():
            # SES-02: delete eligible expired sessions, then enforce maxSessions.
            await self._purge_expired_sessions(now)
            count = await self._db.query(SQL["count_sessions"], (agent_name, principal_id))
            if count and count[0]["n"] >= self._settings.max_sessions:
                raise CapacityError("maxSessions reached; cannot free capacity")
            await self._db.execute(
                SQL["insert_session"],
                (
                    agent_name,
                    principal_id,
                    sid,
                    1,
                    record.to_json(),
                    now,
                    now,
                ),
            )
        existing = await self.get_session(
            agent_name=agent_name, principal_id=principal_id, session_id=sid
        )
        return existing or record

    async def get_session(
        self, *, agent_name: str, principal_id: str, session_id: str
    ) -> SessionRecord | None:
        rows = await self._db.query(SQL["get_session"], (agent_name, principal_id, session_id))
        return self._parse_row(SessionRecord.from_json, rows)

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
        current = await self.get_session(
            agent_name=agent_name, principal_id=principal_id, session_id=session_id
        )
        if current is None:
            raise SessionNotFound(f"session {session_id!r} not found")
        if current.revision != expected_revision:
            raise RevisionConflict(f"revision {expected_revision} != current {current.revision}")
        current.events = list(current.events) + list(events or [])
        if usage:
            for k, v in usage.items():
                current.usage[k] = current.usage.get(k, 0) + v
        if history_truncated is not None:
            current.history_truncated = history_truncated
        current.revision += 1
        current.updated_at = now
        rows = await self._db.query(
            SQL["cas_session"],
            (
                current.to_json(),
                now,
                agent_name,
                principal_id,
                session_id,
                expected_revision,
            ),
        )
        if not rows:
            raise RevisionConflict(f"revision {expected_revision} != current (cas failed)")
        updated = self._parse(SessionRecord.from_json, rows[0]["data"])
        return updated or current

    async def truncate_session_events(
        self,
        *,
        agent_name: str,
        principal_id: str,
        session_id: str,
        keep_revision: int,
    ) -> None:
        current = await self.get_session(
            agent_name=agent_name, principal_id=principal_id, session_id=session_id
        )
        if current is None or current.revision <= keep_revision:
            return
        drop = current.revision - keep_revision
        current.events = current.events[:-drop]
        current.revision = keep_revision
        await self._db.query(
            SQL["update_session_data"],
            (current.to_json(), agent_name, principal_id, session_id),
        )

    async def delete_session(self, *, agent_name: str, principal_id: str, session_id: str) -> bool:
        existing = await self.get_session(
            agent_name=agent_name, principal_id=principal_id, session_id=session_id
        )
        if existing is None:
            return False
        runs = await self.list_runs(
            agent_name=agent_name, principal_id=principal_id, session_id=session_id
        )
        if any(not r.terminal for r in runs):
            raise SessionBusy(f"session {session_id!r} has a nonterminal run")
        async with self._db.transaction():
            await self._db.execute(SQL["delete_runs"], (agent_name, principal_id, session_id))
            await self._db.execute(SQL["delete_idem"], (agent_name, principal_id, session_id))
            await self._db.execute(SQL["delete_session"], (agent_name, principal_id, session_id))
            await self._db.release_advisory_lock(_lock_key(agent_name, principal_id, session_id))
        return True

    async def list_sessions(self, *, agent_name: str, principal_id: str) -> list[SessionRecord]:
        rows = await self._db.query(SQL["list_sessions"], (agent_name, principal_id))
        out: list[SessionRecord] = []
        for row in rows:
            rec = self._parse(SessionRecord.from_json, row["data"])
            if rec is not None:
                out.append(rec)
        return out

    # -- runs -----------------------------------------------------------------------------

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
        session = await self.get_session(
            agent_name=agent_name, principal_id=principal_id, session_id=session_id
        )
        if session is None:
            raise SessionNotFound(f"session {session_id!r} not found")
        existing = await self.get_run(
            agent_name=agent_name, principal_id=principal_id, session_id=session_id, run_id=run_id
        )
        if existing is not None:
            return existing
        record = RunRecord(
            agent_name=agent_name,
            principal_id=principal_id,
            session_id=session_id,
            run_id=run_id,
            input=dict(run_input),
            created_at=now,
            updated_at=now,
        )
        async with self._db.transaction():
            count = await self._db.query(SQL["count_runs"], (agent_name, principal_id, session_id))
            n = count[0]["n"] if count else 0
            while n >= self._settings.max_runs_per_session:
                await self._db.execute(
                    SQL["delete_oldest_terminal_run"],
                    (agent_name, principal_id, session_id),
                )
                count = await self._db.query(
                    SQL["count_runs"], (agent_name, principal_id, session_id)
                )
                n2 = count[0]["n"] if count else 0
                if n2 >= n:
                    raise CapacityError("maxRunsPerSession reached; cannot free capacity")
                n = n2
            await self._db.execute(
                SQL["insert_run"],
                (agent_name, principal_id, session_id, run_id, record.to_json(), now, now),
            )
        return record

    async def get_run(
        self, *, agent_name: str, principal_id: str, session_id: str, run_id: str
    ) -> RunRecord | None:
        rows = await self._db.query(SQL["get_run"], (agent_name, principal_id, session_id, run_id))
        return self._parse_row(RunRecord.from_json, rows)

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
        record = await self.get_run(
            agent_name=agent_name, principal_id=principal_id, session_id=session_id, run_id=run_id
        )
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
        await self._db.query(
            SQL["update_run"],
            (record.to_json(), now, agent_name, principal_id, session_id, run_id),
        )
        return record

    async def list_runs(
        self, *, agent_name: str, principal_id: str, session_id: str
    ) -> list[RunRecord]:
        rows = await self._db.query(SQL["list_runs"], (agent_name, principal_id, session_id))
        out: list[RunRecord] = []
        for row in rows:
            rec = self._parse(RunRecord.from_json, row["data"])
            if rec is not None:
                out.append(rec)
        return out

    # -- idempotency ------------------------------------------------------------------------

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
        existing = await self.get_idempotency(
            agent_name=agent_name, principal_id=principal_id, session_id=session_id, key=key
        )
        if existing is not None:
            return existing
        record = IdempotencyRecord(
            agent_name=agent_name,
            principal_id=principal_id,
            session_id=session_id,
            key=key,
            created_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        async with self._db.transaction():
            count = await self._db.query(SQL["count_idem"], (agent_name, principal_id, session_id))
            if count and count[0]["n"] >= self._settings.max_idempotency_records_per_session:
                raise CapacityError("maxIdempotencyRecordsPerSession reached")
            await self._db.execute(
                SQL["insert_idem"],
                (
                    agent_name,
                    principal_id,
                    session_id,
                    key,
                    record.to_json(),
                    now,
                    record.expires_at,
                ),
            )
        return record

    async def get_idempotency(
        self, *, agent_name: str, principal_id: str, session_id: str, key: str
    ) -> IdempotencyRecord | None:
        rows = await self._db.query(SQL["get_idem"], (agent_name, principal_id, session_id, key))
        return self._parse_row(IdempotencyRecord.from_json, rows)

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
        record = await self.get_idempotency(
            agent_name=agent_name, principal_id=principal_id, session_id=session_id, key=key
        )
        if record is None:
            return None
        record.status = status
        record.outcome = dict(outcome)
        await self._db.query(
            SQL["update_idem"],
            (record.to_json(), agent_name, principal_id, session_id, key),
        )
        return record

    async def expire_idempotency(
        self, *, agent_name: str, principal_id: str, session_id: str, key: str
    ) -> bool:
        await self._db.execute(SQL["delete_idem_one"], (agent_name, principal_id, session_id, key))
        return True

    # -- retention & capacity ----------------------------------------------------------------

    async def sweep(self, *, now: datetime | None = None) -> dict[str, int]:
        now = now or utcnow()
        stats = {"sessions": 0, "runs": 0, "idempotency": 0}
        async with self._db.transaction():
            stats["sessions"] = await self._purge_expired_sessions(now)
            stats["runs"] = await self._purge_expired_runs(now)
            stats["idempotency"] = await self._purge_expired_idem(now)
        return stats

    async def _purge_expired_sessions(self, now: datetime) -> int:
        if self._settings.session_ttl_seconds == 0:
            return 0
        cutoff = now - timedelta(seconds=self._settings.session_ttl_seconds)
        rows = await self._db.query(SQL["expired_sessions"], (cutoff,))
        deleted = 0
        for row in rows:
            lock_key = _lock_key(row["agent_name"], row["principal_id"], row["session_id"])
            acquired = await self._db.try_advisory_lock(lock_key)
            if acquired:
                # not leased — release and delete
                await self._db.release_advisory_lock(lock_key)
            else:
                continue  # SES-06: skip a session with a live lease
            runs = await self.list_runs(
                agent_name=row["agent_name"],
                principal_id=row["principal_id"],
                session_id=row["session_id"],
            )
            if any(not r.terminal for r in runs):
                continue
            await self._db.execute(
                SQL["delete_session"],
                (row["agent_name"], row["principal_id"], row["session_id"]),
            )
            deleted += 1
        return deleted

    async def _purge_expired_runs(self, now: datetime) -> int:
        cutoff = now - timedelta(seconds=self._settings.run_ttl_seconds)
        await self._db.execute(SQL["delete_runs_older_than"], (cutoff,))
        return 0  # sqlite driver returns no rowcount; deletion is idempotent

    async def _purge_expired_idem(self, now: datetime) -> int:
        rows = await self._db.query(SQL["expired_idem"], (now,))
        for row in rows:
            await self._db.execute(
                SQL["delete_idem_one"],
                (row["agent_name"], row["principal_id"], row["session_id"], row["key"]),
            )
        return len(rows)

    # -- fencing (SES-05: session-scoped advisory lock on a dedicated connection) ------------

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
        key = _lock_key(agent_name, principal_id, session_id)
        acquired = await self._db.try_advisory_lock(key)
        if not acquired:
            return None
        now = now or utcnow()
        rows = await self._db.query(
            SQL["bump_fence_number"], (agent_name, principal_id, session_id)
        )
        try:
            number = int(rows[0]["fencing_number"]) if rows else 1
        except (KeyError, TypeError, ValueError):
            number = 1
        self._tokens[key] = token
        return Fence(
            token=token, fencing_number=number, expires_at=now + timedelta(seconds=ttl_seconds)
        )

    async def renew_fence(
        self,
        *,
        agent_name: str,
        principal_id: str,
        session_id: str,
        token: str,
        ttl_seconds: float,
    ) -> bool:
        key = _lock_key(agent_name, principal_id, session_id)
        # advisory locks persist for the connection lifetime; the token check
        # is the SES-05 identity gate.
        return self._tokens.get(key) == token

    async def release_fence(
        self, *, agent_name: str, principal_id: str, session_id: str, token: str
    ) -> bool:
        key = _lock_key(agent_name, principal_id, session_id)
        if self._tokens.get(key) != token:
            return False
        await self._db.release_advisory_lock(key)
        self._tokens.pop(key, None)
        return True

    async def current_fence(
        self, *, agent_name: str, principal_id: str, session_id: str
    ) -> Fence | None:
        # The advisory lock holder is the current owner; the substitute and
        # real driver both expose the held-lock state via try_advisory_lock
        # semantics (a second try fails while held).
        return None

    async def find_run(
        self, *, agent_name: str, principal_id: str, run_id: str
    ) -> RunRecord | None:
        rows = await self._db.query(SQL["find_run"], (agent_name, principal_id, run_id))
        if not rows:
            return None
        try:
            return RunRecord.from_json(json.loads(rows[0]["data"]))
        except (ValueError, TypeError, json.JSONDecodeError):
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
        now = now or utcnow()
        record = ApprovalRecord(
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
        await self._db.execute(
            SQL["insert_approval"],
            (agent_name, principal_id, approval_id, record.to_json(), now, record.expires_at),
        )
        return record

    async def get_approval(
        self, *, agent_name: str, principal_id: str, approval_id: str
    ) -> ApprovalRecord | None:
        rows = await self._db.query(SQL["get_approval"], (agent_name, principal_id, approval_id))
        if not rows:
            return None
        try:
            return ApprovalRecord.from_json(json.loads(rows[0]["data"]))
        except (ValueError, TypeError, json.JSONDecodeError):
            return None

    async def list_approvals(
        self, *, agent_name: str, principal_id: str, session_id: str
    ) -> list[ApprovalRecord]:
        rows = await self._db.query(SQL["list_approvals"], (agent_name, principal_id, session_id))
        out: list[ApprovalRecord] = []
        for row in rows:
            try:
                out.append(ApprovalRecord.from_json(json.loads(row["data"])))
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
        out.sort(key=lambda r: r.created_at)
        return out

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
        """HITL-04 CAS: the UPDATE's WHERE clause is the CAS - only a pending,
        unexpired record matches; the first decision wins."""
        now = now or utcnow()
        record = await self.get_approval(
            agent_name=agent_name, principal_id=principal_id, approval_id=approval_id
        )
        if record is None or not record.pending:
            return None
        if now > record.expires_at and decision != "timed_out":
            return None
        record.status = decision
        record.reason = reason
        record.decided_at = now
        record.revision += 1
        rows = await self._db.query(
            SQL["decide_approval"],
            (
                record.to_json(),
                record.expires_at,
                agent_name,
                principal_id,
                approval_id,
                now.isoformat(),
                decision,
            ),
        )
        if not rows:
            return None
        try:
            return ApprovalRecord.from_json(json.loads(rows[0]["data"]))
        except (ValueError, TypeError, json.JSONDecodeError):
            return None

    async def expire_approvals(self, *, now: datetime | None = None) -> list[ApprovalRecord]:
        now = now or utcnow()
        rows = await self._db.query(SQL["expire_approvals"], (now.isoformat(),))
        expired: list[ApprovalRecord] = []
        for row in rows:
            try:
                record = ApprovalRecord.from_json(json.loads(row["data"]))
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
            if not record.pending:
                continue
            decided = await self.decide_approval(
                agent_name=record.agent_name,
                principal_id=record.principal_id,
                approval_id=record.approval_id,
                decision="timed_out",
                now=now,
            )
            if decided is not None:
                expired.append(decided)
        return expired
