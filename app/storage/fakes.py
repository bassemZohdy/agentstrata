"""In-memory substitutes for external storage backends (§18 ACC-01).

Per the approved ACC-01 deviation (2026-08-02), the shared contract suite
exercises the redis/postgres backends through these substitutes; the
real-instance + fencing/multi-replica proofs are recorded as deferred.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any

from . import redis_backend as rb


def _i(value: Any) -> int:
    """Defensive int conversion for self-generated script args/timestamps."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _f(value: Any) -> float:
    """Defensive float conversion (never throws)."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _j(value: str) -> Any:
    """Defensive JSON decode of self-generated payloads."""
    try:
        return json.loads(value)
    except ValueError:
        return {}


def _clock() -> Callable[[], datetime]:
    """Returns a monotone test clock (fixed base so expiry math is stable)."""
    base = datetime.now(UTC)

    def now() -> datetime:
        return base

    return now


class FakeRedis:
    """Implements the ``RedisClient`` protocol over in-memory dicts, with a
    python twin for every Lua script (keyed by script source) so the backend
    logic runs identically under the substitute."""

    def __init__(self, now_fn: Callable[[], datetime] | None = None) -> None:
        self._store: dict[str, bytes] = {}
        self._expires: dict[str, datetime] = {}
        self._sorted_sets: dict[str, dict[str, float]] = {}
        self._sets: dict[str, set] = {}
        self.now = now_fn or _clock()

    # -- raw ops -------------------------------------------------------------------

    async def get(self, key: str) -> bytes | None:
        if key in self._expires and self._expires[key] <= self.now():
            self._store.pop(key, None)
            self._expires.pop(key, None)
            return None
        return self._store.get(key)

    async def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        self._store[key] = value.encode("utf-8")
        if ex is not None:
            self._expires[key] = self.now() + timedelta(seconds=ex)

    async def delete(self, key: str) -> int:
        if key in self._store:
            self._store.pop(key, None)
            self._expires.pop(key, None)
            return 1
        return 0

    async def eval(self, script: str, keys: list[str], args: list[str]) -> Any:
        twin = _TWINS.get(script)
        if twin is None:
            raise ValueError("unknown script")
        return twin(self, keys, args)

    # -- fake helpers ----------------------------------------------------------------

    def _zadd(self, zkey: str, score: float, member: str) -> None:
        self._sorted_sets.setdefault(zkey, {})[member] = score

    def _zremrangebyscore(self, zkey: str, min_: float, max_: float) -> int:
        zset = self._sorted_sets.get(zkey)
        if not zset:
            return 0
        doomed = [m for m, s in zset.items() if min_ <= s <= max_]
        for m in doomed:
            zset.pop(m, None)
        return len(doomed)

    def _zcard(self, zkey: str) -> int:
        return len(self._sorted_sets.get(zkey, {}))

    def _zrange(self, zkey: str) -> list[str]:
        zset = self._sorted_sets.get(zkey, {})
        return [m for m, _ in sorted(zset.items(), key=lambda kv: kv[1])]

    def _zrangebyscore(self, zkey: str, min_: float, max_: float) -> list[str]:
        zset = self._sorted_sets.get(zkey, {})
        return [m for m, s in sorted(zset.items(), key=lambda kv: kv[1]) if min_ <= s <= max_]

    def _zrem(self, zkey: str, member: str) -> int:
        zset = self._sorted_sets.get(zkey)
        if zset and member in zset:
            zset.pop(member, None)
            return 1
        return 0

    def _keys(self, pattern: str) -> list[str]:
        out = []
        for key in list(self._store.keys()):
            if fnmatch.fnmatch(key, pattern) and not self._is_expired(key):
                out.append(key)
        return out

    def _is_expired(self, key: str) -> bool:
        exp = self._expires.get(key)
        return exp is not None and exp <= self.now()

    def _get_decoded(self, key: str) -> Any | None:
        raw = self._store.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, AttributeError):
            return None

    def _set_json(
        self, key: str, obj: Any, ex: int | None = None, expires_at: datetime | None = None
    ) -> None:
        self._store[key] = json.dumps(obj).encode("utf-8")
        if ex is not None:
            self._expires[key] = self.now() + timedelta(seconds=ex)
        elif expires_at is not None:
            self._expires[key] = expires_at

    def _put(self, key: str, value: bytes | None) -> None:
        if value is None:
            self._store.pop(key, None)
            self._expires.pop(key, None)
        else:
            self._store[key] = value

    def _now_ts(self) -> int:
        return _i(self.now().timestamp())


# ---------------------------------------------------------------------------
# Python twins for the Lua scripts (behaviorally identical to the Lua).
# ---------------------------------------------------------------------------


def _t_create_session(fake: FakeRedis, keys: list[str], args: list[str]) -> Any:
    sess_key, idx_key = keys
    payload, ts, max_sessions, ttl = args[0], _i(args[1]), _i(args[2]), _i(args[3])
    if fake._store.get(sess_key) is not None:
        return None
    cutoff = ts - ttl
    fake._zremrangebyscore(idx_key, _f("-inf"), _f(cutoff))
    if fake._zcard(idx_key) >= max_sessions:
        return "capacity"
    if ttl > 0:
        fake._set_json(sess_key, _j(payload), expires_at=datetime.fromtimestamp(ts + ttl, tz=UTC))
    else:
        fake._set_json(sess_key, _j(payload))
    fake._zadd(idx_key, ts, args[4])
    return "ok"


def _t_mutate_session(fake: FakeRedis, keys: list[str], args: list[str]) -> Any:
    (sess_key,) = keys
    expected = _i(args[0])
    events = _j(args[1])
    usage = _j(args[2])
    truncated = args[3] == "1"
    updated_at = args[4]
    rec = fake._get_decoded(sess_key)
    if rec is None:
        return None
    if rec["revision"] != expected:
        return f"rev:{expected}"
    rec["revision"] += 1
    rec["events"] = list(rec.get("events", [])) + events
    for k, v in usage.items():
        rec.setdefault("usage", {})[k] = rec["usage"].get(k, 0) + v
    if truncated:
        rec["history_truncated"] = True
    rec["updated_at"] = updated_at
    fake._put(sess_key, json.dumps(rec).encode("utf-8"))
    ttl = _i(args[5])
    if ttl > 0:
        fake._expires[sess_key] = datetime.fromtimestamp(_i(updated_at) + ttl, tz=UTC)
    return json.dumps(rec)


def _t_delete_session(fake: FakeRedis, keys: list[str], args: list[str]) -> Any:
    sess_key, runidx, idemidx, fence_key, fcount_key, idx_key = keys
    sid = args[0]
    if fake._store.get(sess_key) is None:
        return 0
    run_members = fake._zrange(runidx)
    idem_members = fake._zrange(idemidx)
    for k in run_members:
        r = fake._get_decoded(k)
        if r and r.get("status") not in ("succeeded", "failed", "cancelled"):
            return f"busy:{r.get('run_id')}"
    for k in [sess_key, *run_members, *idem_members, fence_key, fcount_key]:
        fake._put(k, None)
    for k in run_members:
        fake._zrem(runidx, k)
    for k in idem_members:
        fake._zrem(idemidx, k)
    fake._zrem(idx_key, sid)
    return 1


def _t_list_sessions(fake: FakeRedis, keys: list[str], args: list[str]) -> Any:
    (idx_key,) = keys
    return fake._zrange(idx_key)


def _t_create_run(fake: FakeRedis, keys: list[str], args: list[str]) -> Any:
    sess_key, run_key, runidx = keys
    payload, cap, updated_ts = args[0], _i(args[1]), _i(args[2])
    if fake._store.get(sess_key) is None:
        return f"missing:{sess_key}"
    if fake._store.get(run_key) is not None:
        return "ok"

    def session_runs():
        members = fake._zrange(runidx)
        live = [k for k in members if fake._store.get(k) is not None]
        for k in set(members) - set(live):
            fake._zrem(runidx, k)
        return live

    existing = session_runs()
    terminal = sorted(
        [
            (k, _i((r or {}).get("updated_at_ts") or 0))
            for k in existing
            if (r := fake._get_decoded(k)) is not None
            and r.get("status") in ("succeeded", "failed", "cancelled")
        ],
        key=lambda kv: kv[1],
    )
    while len(existing) >= cap and terminal:
        fake._put(terminal[0][0], None)
        fake._zrem(runidx, terminal[0][0])
        terminal.pop(0)
        existing = session_runs()
    if len(existing) >= cap:
        return "capacity:maxRunsPerSession"
    fake._set_json(run_key, _j(payload))
    fake._zadd(runidx, float(updated_ts), run_key)
    return "ok"


def _t_create_idem(fake: FakeRedis, keys: list[str], args: list[str]) -> Any:
    idem_key, idemidx = keys
    payload, cap, ttl = args[0], _i(args[1]), _i(args[2])
    ts = _i(args[3]) if len(args) > 3 else 0
    expires_ts = _i(args[4]) if len(args) > 4 else ts + ttl
    if fake._store.get(idem_key) is not None:
        return "ok"
    count = 0
    for k in fake._zrange(idemidx):
        if fake._store.get(k) is not None:
            count += 1
        else:
            fake._zrem(idemidx, k)
    if count >= cap:
        return "capacity:maxIdempotencyRecordsPerSession"
    if ttl > 0 and ts > 0:
        fake._set_json(idem_key, _j(payload), expires_at=datetime.fromtimestamp(ts + ttl, tz=UTC))
    else:
        fake._set_json(idem_key, _j(payload))
    fake._zadd(idemidx, float(expires_ts), idem_key)
    return "ok"


def _t_list_runs(fake: FakeRedis, keys: list[str], args: list[str]) -> Any:
    (runidx,) = keys
    return fake._zrange(runidx)


def _t_admit_run(fake: FakeRedis, keys: list[str], args: list[str]) -> Any:
    """ADMIT_RUN twin: ensure the session (creating it when absent) and
    create the run record in one call — no orphan window."""
    sess_key, idx_key, run_key, runidx = keys
    payload, run_payload = args[0], args[1]
    max_sessions, ttl = _i(args[2]), _i(args[3])
    now_ts, run_cap = _i(args[4]), _i(args[5])
    updated_ts, sid = _i(args[6]), args[7]
    sess = fake._get_decoded(sess_key)
    revision = 1
    if sess is None:
        fake._zremrangebyscore(idx_key, float("-inf"), float(now_ts - ttl))
        if fake._zcard(idx_key) >= max_sessions:
            return "capacity:maxSessions"
        if ttl > 0:
            fake._set_json(
                sess_key,
                _j(payload),
                expires_at=datetime.fromtimestamp(now_ts + ttl, tz=UTC),
            )
        else:
            fake._set_json(sess_key, _j(payload))
        fake._zadd(idx_key, float(now_ts), sid)
    else:
        revision = _i(sess.get("revision") or 1)
    if fake._store.get(run_key) is not None:
        return f"ok:{revision}"
    prefix_cap = run_cap

    def session_runs():
        members = fake._zrange(runidx)
        live = [k for k in members if fake._store.get(k) is not None]
        for k in set(members) - set(live):
            fake._zrem(runidx, k)
        return live

    existing = session_runs()
    terminal = sorted(
        [
            (k, _i((r or {}).get("updated_at_ts") or 0))
            for k in existing
            if (r := fake._get_decoded(k)) is not None
            and r.get("status") in ("succeeded", "failed", "cancelled")
        ],
        key=lambda kv: kv[1],
    )
    while len(existing) >= prefix_cap and terminal:
        fake._put(terminal[0][0], None)
        fake._zrem(runidx, terminal[0][0])
        terminal.pop(0)
        existing = session_runs()
    if len(existing) >= prefix_cap:
        return "capacity:maxRunsPerSession"
    fake._set_json(run_key, _j(run_payload))
    fake._zadd(runidx, float(updated_ts), run_key)
    return f"ok:{revision}"


def _t_expire_idem(fake: FakeRedis, keys: list[str], args: list[str]) -> Any:
    idem_key, idemidx = keys
    if fake._store.get(idem_key) is None:
        return 0
    fake._put(idem_key, None)
    fake._zrem(idemidx, idem_key)
    return 1


def _t_acquire_fence(fake: FakeRedis, keys: list[str], args: list[str]) -> Any:
    fence_key, fcount_key, sess_key = keys
    token, now_ts, ttl = args[0], _i(args[1]), _i(args[2])
    current = fake._get_decoded(fence_key)
    if current is not None and (
        current.get("expires_at", 0) == 0 or current["expires_at"] > now_ts
    ):
        return None
    count = _i((fake._store.get(fcount_key) or b"0").decode()) + 1
    fake._put(fcount_key, str(count).encode("utf-8"))
    fake._set_json(
        fence_key,
        {"token": token, "fencing_number": count, "expires_at": now_ts + ttl},
    )
    # SES-06: a leased session must not expire under its TTL
    if sess_key in fake._expires:
        fake._expires[sess_key] = datetime.fromtimestamp(now_ts + ttl, tz=UTC)
    return str(count)


def _t_renew_fence(fake: FakeRedis, keys: list[str], args: list[str]) -> Any:
    (fence_key,) = keys
    token, ttl = args[0], _i(args[1])
    f = fake._get_decoded(fence_key)
    if f is None or f.get("token") != token:
        return 0
    f["expires_at"] = _i(f.get("expires_at", 0)) + ttl
    fake._set_json(fence_key, f)
    return 1


def _t_release_fence(fake: FakeRedis, keys: list[str], args: list[str]) -> Any:
    (fence_key,) = keys
    token = args[0]
    f = fake._get_decoded(fence_key)
    if f is None or f.get("token") != token:
        return 0
    fake._put(fence_key, None)
    return 1


def _t_truncate_session(fake: FakeRedis, keys: list[str], args: list[str]) -> Any:
    (sess_key,) = keys
    keep = _i(args[0])
    rec = fake._get_decoded(sess_key)
    if rec is None or _i(rec.get("revision")) <= keep:
        return 0
    drop = _i(rec.get("revision")) - keep
    rec["events"] = rec.get("events", [])[: -drop or None]
    rec["revision"] = keep
    fake._set_json(sess_key, rec)
    return drop


def _t_sweep_runs(fake: FakeRedis, keys: list[str], args: list[str]) -> Any:
    now_ts, ttl = _i(args[0]), _i(args[1])
    cutoff = now_ts - ttl
    deleted = 0
    for runidx in list(fake._sorted_sets):
        if not runidx.startswith("agentbase:") or ":runidx:" not in runidx:
            continue
        for k in fake._zrangebyscore(runidx, float("-inf"), float(cutoff)):
            r = fake._get_decoded(k)
            if r:
                terminal = r.get("status") in ("succeeded", "failed", "cancelled")
                updated = _i(r.get("updated_at_ts") or 0)
                if terminal and updated <= cutoff:
                    fake._put(k, None)
                    fake._zrem(runidx, k)
                    deleted += 1
                elif not terminal and updated > 0:
                    fake._zadd(runidx, float(updated), k)
            else:
                fake._zrem(runidx, k)
    return deleted


def _t_create_approval(fake: FakeRedis, keys: list[str], args: list[str]) -> Any:
    """CREATE_APPROVAL twin: SET the record + SADD the index entry."""
    key, index = keys
    raw, entry = args
    if fake._store.get(key) is not None:
        return None
    fake._store[key] = raw.encode("utf-8")
    fake._sets.setdefault(index, set()).add(entry)
    return raw


def _t_list_approvals(fake: FakeRedis, keys: list[str], args: list[str]) -> Any:
    index = keys[0]
    return sorted(fake._sets.get(index, set()))


def _t_decide_approval(fake: FakeRedis, keys: list[str], args: list[str]) -> Any:
    """DECIDE_APPROVAL twin: pending + unexpired only; first decision wins."""
    import json as _json

    key = keys[0]
    now, decision, reason = args
    raw = fake._store.get(key)
    if raw is None:
        return None
    record = _json.loads(raw)
    if record.get("status") != "pending":
        return None
    if now > record.get("expires_at", "") and decision != "timed_out":
        return None
    record["status"] = decision
    record["reason"] = reason
    record["decided_at"] = now
    record["revision"] = record.get("revision", 1) + 1
    encoded = _json.dumps(record)
    fake._store[key] = encoded.encode("utf-8")
    return encoded


_TWINS: dict[str, Callable[[FakeRedis, list[str], list[str]], Any]] = {
    rb.CREATE_SESSION: _t_create_session,
    rb.MUTATE_SESSION: _t_mutate_session,
    rb.DELETE_SESSION: _t_delete_session,
    rb.LIST_SESSIONS: _t_list_sessions,
    rb.CREATE_RUN: _t_create_run,
    rb.ADMIT_RUN: _t_admit_run,
    rb.CREATE_IDEM: _t_create_idem,
    rb.LIST_RUNS: _t_list_runs,
    rb.EXPIRE_IDEM: _t_expire_idem,
    rb.ACQUIRE_FENCE: _t_acquire_fence,
    rb.RENEW_FENCE: _t_renew_fence,
    rb.RELEASE_FENCE: _t_release_fence,
    rb.SWEEP_RUNS: _t_sweep_runs,
    rb.CREATE_APPROVAL: _t_create_approval,
    rb.LIST_APPROVALS: _t_list_approvals,
    rb.DECIDE_APPROVAL: _t_decide_approval,
    rb.TRUNCATE_SESSION: _t_truncate_session,
}


class FakePostgres:
    """Minimal in-memory substitute for the postgres backend's SQL surface.

    Stores rows keyed by (table, primary-key-tuple); the postgres backend
    routes through this when no real database is configured. Advisory-lock
    fencing is simulated with an in-process held-lock map (the real
    advisory-lock + fencing-number proof is deferred per ACC-01).
    """

    def __init__(self) -> None:
        self.rows: dict[str, dict[tuple, dict]] = {}
        self.advisory_locks: dict[tuple[str, str, str], str] = {}

    def execute(self, table: str, pk: tuple, data: dict) -> None:
        self.rows.setdefault(table, {})[pk] = dict(data)

    def fetch(self, table: str, pk: tuple) -> dict | None:
        return self.rows.get(table, {}).get(pk)

    def delete(self, table: str, pk: tuple) -> bool:
        rows = self.rows.get(table, {})
        if pk in rows:
            rows.pop(pk, None)
            return True
        return False

    def fetch_all(self, table: str, prefix: tuple) -> list[dict]:
        return [
            dict(d) for (pk, d) in self.rows.get(table, {}).items() if pk[: len(prefix)] == prefix
        ]


class SqliteDb:
    """In-memory SQLite substitute for the postgres backend (DbClient).

    Executes the backend's real SQL with three translations: ``%s`` params
    to ``?``, ``JSONB`` to ``TEXT``, and the postgres JSON operator
    ``data->>'status'`` to ``json_extract``. Advisory-lock fencing is
    simulated with an in-process held-lock map (the real advisory-lock +
    fencing-number proof is deferred per ACC-01).
    """

    def __init__(self) -> None:
        import sqlite3
        from datetime import datetime as _dt

        # Python 3.12 deprecates sqlite3's default datetime adapter.
        sqlite3.register_adapter(_dt, lambda dt: dt.isoformat())
        self._conn = sqlite3.connect(":memory:", isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = asyncio.Lock()
        self._held_locks: set[int] = set()

    @staticmethod
    def _translate(sql: str) -> str:
        sql = sql.replace("JSONB", "TEXT")
        sql = sql.replace("(data->>'status')", "json_extract(data, '$.status')")
        sql = sql.replace("data->>'status'", "json_extract(data, '$.status')")
        sql = sql.replace("ctid", "rowid")  # sqlite rowid analogue of postgres ctid
        return sql.replace("%s", "?")

    @staticmethod
    def _column_migration(sql: str, conn: Any) -> str | None:
        """SQLite has no ``ALTER TABLE ... ADD COLUMN IF NOT EXISTS``; the
        backend's idempotent column migration becomes a no-op when the
        column already exists (the CREATE TABLE already carries it)."""
        import re

        marker = "ADD COLUMN IF NOT EXISTS"
        if marker not in sql:
            return sql
        match = re.match(r"\s*ALTER TABLE\s+(\S+)\s+" + re.escape(marker) + r"\s+(\S+)", sql)
        if match is None:
            return sql
        table, column = match.group(1), match.group(2)
        columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
        if column in columns:
            return None  # no-op
        return sql.replace(marker, "ADD COLUMN")

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        async with self._lock:
            translated = self._column_migration(self._translate(sql), self._conn)
            if translated is None:
                return
            self._conn.execute(translated, list(params))

    async def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        async with self._lock:
            cur = self._conn.execute(self._translate(sql), list(params))
            rows = cur.fetchall()
            return [dict(r) for r in rows]

    def transaction(self) -> AbstractAsyncContextManager[None]:
        return _SqliteTxn(self)

    async def try_advisory_lock(self, key: int) -> bool:
        async with self._lock:
            if key in self._held_locks:
                return False
            self._held_locks.add(key)
            return True

    async def release_advisory_lock(self, key: int) -> None:
        async with self._lock:
            self._held_locks.discard(key)


class _SqliteTxn:
    def __init__(self, db: SqliteDb) -> None:
        self._db = db

    async def __aenter__(self) -> None:
        await self._db.execute("BEGIN")

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if exc_type is None:
            await self._db.execute("COMMIT")
        else:
            await self._db.execute("ROLLBACK")
