"""Redis storage backend (REQUIREMENTS.md SES-01 redis row, SES-05).

Key layout shares one hash tag per session for Redis Cluster compatibility:
``agentbase:{principal_digest}`` wrapped in ``{...}`` so session, run,
idempotency, index, and fence keys land in the same slot. Revision mutations
and fence lease ops are atomic Lua scripts; the contract suite runs the same
scripts through an in-memory substitute (``FakeRedis``), and the real-instance
+ fencing/multi-replica proof is recorded as deferred (approved ACC-01
deviation).
"""

from __future__ import annotations

import hashlib
import json
import logging
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

PREFIX = "agentbase"


# The hash tag is the principal digest — every key of a session shares it.
def _hash_tag(principal_id: str) -> str:
    return "{" + hashlib.sha256(principal_id.encode("utf-8")).hexdigest() + "}"


def _k(tag: str, *parts: str) -> str:
    return ":".join((PREFIX, tag, *parts))


def _to_int(value: Any, default: int = 0) -> int:
    """Defensive conversion for self-generated values (timestamps, script
    results) that must never crash the backend."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class RedisClient(Protocol):
    """The redis subset the backend uses (real client or FakeRedis).

    Uses ``Any`` parameter/return types because redis-py's typed client is
    structurally incompatible with the protocol's simpler shapes; FakeRedis
    and the real client both satisfy the runtime contract.
    """

    async def get(self, key: str) -> Any: ...
    async def set(self, key: str, value: str, *, ex: int | None = None) -> None: ...
    async def delete(self, key: str) -> int: ...
    async def eval(self, script: str, keys: list[str], args: list[str]) -> Any: ...


class RedisBackend(StorageBackend):
    kind = "redis"

    def __init__(self, client: RedisClient, settings: StorageSettings | None = None) -> None:
        self._client = client
        self._settings = settings or StorageSettings()
        self._ready = False
        self._approval_index = "agentbase:approval-index"

    async def initialize(self) -> None:
        try:
            await self._client.get("agentbase:ping")
            self._ready = True
        except Exception as exc:  # noqa: BLE001
            raise BackendUnavailableError(f"redis storage unavailable: {exc}") from exc

    async def close(self) -> None:
        self._ready = False

    async def health(self) -> bool:
        """SES-04/NFR-09: re-probe on each call (bounded) so readiness
        converges after the dependency dies or recovers. Every call probes:
        a one-off failure must not stick (recovery semantics)."""
        import asyncio

        try:
            await asyncio.wait_for(self._client.get("agentbase:health"), timeout=2)
            self._ready = True
            return True
        except Exception as exc:  # noqa: BLE001 - dependency outage
            if self._ready:
                logger.warning("redis health probe failed: %s", type(exc).__name__)
            self._ready = False
            return False

    # -- keys --------------------------------------------------------------------------

    def _tag(self, principal_id: str) -> str:
        return _hash_tag(principal_id)

    def _sess(self, agent: str, tag: str, sid: str) -> str:
        return _k(tag, "sess", agent, sid)

    def _run(self, agent: str, tag: str, sid: str, rid: str) -> str:
        return _k(tag, "run", agent, sid, rid)

    def _idem(self, agent: str, tag: str, sid: str, key: str) -> str:
        return _k(tag, "idem", agent, sid, key)

    def _idx(self, agent: str, tag: str) -> str:
        return _k(tag, "idx", agent)

    def _fence(self, agent: str, tag: str, sid: str) -> str:
        return _k(tag, "fence", agent, sid)

    def _approval(self, agent: str, tag: str, approval_id: str) -> str:
        return _k(tag, "approval", agent, approval_id)

    def _fcount(self, agent: str, tag: str, sid: str) -> str:
        return _k(tag, "fcount", agent, sid)

    # -- sessions ------------------------------------------------------------------------

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
        tag = self._tag(principal_id)
        record = SessionRecord(
            agent_name=agent_name,
            principal_id=principal_id,
            session_id=sid,
            events=list(initial_events or []),
            created_at=now,
            updated_at=now,
        )
        result = await self._client.eval(
            CREATE_SESSION,
            [self._sess(agent_name, tag, sid), self._idx(agent_name, tag)],
            [
                record.to_json(),
                str(_to_int(now.timestamp())),
                str(self._settings.max_sessions),
                str(self._settings.session_ttl_seconds),
                sid,
            ],
        )
        if result is None:
            # existing record — return it
            raw = await self._client.get(self._sess(agent_name, tag, sid))
            if raw is not None:
                return SessionRecord.from_json(raw.decode("utf-8"))
        return record

    async def get_session(
        self, *, agent_name: str, principal_id: str, session_id: str
    ) -> SessionRecord | None:
        raw = await self._client.get(self._sess(agent_name, self._tag(principal_id), session_id))
        if raw is None:
            return None
        try:
            return SessionRecord.from_json(raw.decode("utf-8"))
        except ValueError:
            return None

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
        tag = self._tag(principal_id)
        result = await self._client.eval(
            MUTATE_SESSION,
            [self._sess(agent_name, tag, session_id)],
            [
                str(expected_revision),
                json.dumps(events or []),
                json.dumps(usage or {}),
                "1" if history_truncated else "0",
                str(_to_int(now.timestamp())),
                str(self._settings.session_ttl_seconds),
            ],
        )
        if result is None:
            raise SessionNotFound(f"session {session_id!r} not found")
        if isinstance(result, str) and result.startswith("rev:"):
            raise RevisionConflict(result)
        return SessionRecord.from_json(
            result.decode() if isinstance(result, bytes) else str(result)
        )

    async def truncate_session_events(
        self,
        *,
        agent_name: str,
        principal_id: str,
        session_id: str,
        keep_revision: int,
    ) -> None:
        tag = self._tag(principal_id)
        await self._client.eval(
            TRUNCATE_SESSION,
            [self._sess(agent_name, tag, session_id)],
            [str(keep_revision)],
        )

    async def delete_session(self, *, agent_name: str, principal_id: str, session_id: str) -> bool:
        tag = self._tag(principal_id)
        result = await self._client.eval(
            DELETE_SESSION,
            [
                self._sess(agent_name, tag, session_id),
                self._run(agent_name, tag, session_id, "*"),
                self._idem(agent_name, tag, session_id, "*"),
                self._fence(agent_name, tag, session_id),
                self._fcount(agent_name, tag, session_id),
                self._idx(agent_name, tag),
            ],
            [session_id],
        )
        if isinstance(result, str) and result.startswith("busy:"):
            raise SessionBusy(result)
        return bool(result)

    async def list_sessions(self, *, agent_name: str, principal_id: str) -> list[SessionRecord]:
        tag = self._tag(principal_id)
        members = await self._client.eval(LIST_SESSIONS, [self._idx(agent_name, tag)], [])
        out: list[SessionRecord] = []
        if not members:
            return out
        for sid in members:
            raw = await self._client.get(self._sess(agent_name, tag, sid))
            if raw is not None:
                try:
                    out.append(SessionRecord.from_json(raw.decode("utf-8")))
                except ValueError:
                    continue
        return out

    # -- runs --------------------------------------------------------------------------

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
        tag = self._tag(principal_id)
        record = RunRecord(
            agent_name=agent_name,
            principal_id=principal_id,
            session_id=session_id,
            run_id=run_id,
            input=dict(run_input),
            created_at=now,
            updated_at=now,
        )
        result = await self._client.eval(
            CREATE_RUN,
            [
                self._sess(agent_name, tag, session_id),
                self._run(agent_name, tag, session_id, run_id),
            ],
            [record.to_json(), str(self._settings.max_runs_per_session)],
        )
        if isinstance(result, str) and result.startswith("missing:"):
            raise SessionNotFound(f"session {session_id!r} not found")
        if isinstance(result, str) and result.startswith("capacity:"):
            raise CapacityError(result)
        return record

    async def get_run(
        self, *, agent_name: str, principal_id: str, session_id: str, run_id: str
    ) -> RunRecord | None:
        raw = await self._client.get(
            self._run(agent_name, self._tag(principal_id), session_id, run_id)
        )
        if raw is None:
            return None
        try:
            return RunRecord.from_json(raw.decode("utf-8"))
        except ValueError:
            return None

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
        raw = await self._client.get(
            self._run(agent_name, self._tag(principal_id), session_id, run_id)
        )
        if raw is None:
            return None
        try:
            record = RunRecord.from_json(raw.decode("utf-8"))
        except ValueError:
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
        await self._client.set(
            self._run(agent_name, self._tag(principal_id), session_id, run_id),
            record.to_json(),
        )
        return record

    async def list_runs(
        self, *, agent_name: str, principal_id: str, session_id: str
    ) -> list[RunRecord]:
        tag = self._tag(principal_id)
        keys = await self._client.eval(LIST_RUNS, [self._run(agent_name, tag, session_id, "*")], [])
        out: list[RunRecord] = []
        for k in keys:
            raw = await self._client.get(k)
            if raw is not None:
                try:
                    out.append(RunRecord.from_json(raw.decode("utf-8")))
                except ValueError:
                    continue
        return out

    # -- idempotency -----------------------------------------------------------------------

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
        tag = self._tag(principal_id)
        record = IdempotencyRecord(
            agent_name=agent_name,
            principal_id=principal_id,
            session_id=session_id,
            key=key,
            created_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        result = await self._client.eval(
            CREATE_IDEM,
            [self._idem(agent_name, tag, session_id, key)],
            [
                record.to_json(),
                str(self._settings.max_idempotency_records_per_session),
                str(ttl_seconds),
                str(_to_int(now.timestamp())),
            ],
        )
        if isinstance(result, str) and result.startswith("capacity:"):
            raise CapacityError(result)
        return record

    async def get_idempotency(
        self, *, agent_name: str, principal_id: str, session_id: str, key: str
    ) -> IdempotencyRecord | None:
        raw = await self._client.get(
            self._idem(agent_name, self._tag(principal_id), session_id, key)
        )
        if raw is None:
            return None
        try:
            return IdempotencyRecord.from_json(raw.decode("utf-8"))
        except ValueError:
            return None

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
        await self._client.set(
            self._idem(agent_name, self._tag(principal_id), session_id, key),
            record.to_json(),
        )
        return record

    async def expire_idempotency(
        self, *, agent_name: str, principal_id: str, session_id: str, key: str
    ) -> bool:
        n = await self._client.delete(
            self._idem(agent_name, self._tag(principal_id), session_id, key)
        )
        return n > 0

    # -- retention & capacity ---------------------------------------------------------------

    async def sweep(self, *, now: datetime | None = None) -> dict[str, int]:
        # SES-06 redis row: session/idempotency expiration is applied
        # atomically with mutations (TTL'd keys fall out naturally); terminal
        # run retention is enforced here via a scan.
        now = now or utcnow()
        deleted = await self._client.eval(
            SWEEP_RUNS,
            [],
            [str(_to_int(now.timestamp())), str(self._settings.run_ttl_seconds)],
        )
        return {"sessions": 0, "runs": _to_int(deleted), "idempotency": 0}

    # -- fencing (SES-05 lease) ----------------------------------------------------------------

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
        tag = self._tag(principal_id)
        result = await self._client.eval(
            ACQUIRE_FENCE,
            [
                self._fence(agent_name, tag, session_id),
                self._fcount(agent_name, tag, session_id),
                self._sess(agent_name, tag, session_id),
            ],
            [token, str(_to_int(now.timestamp())), str(_to_int(ttl_seconds))],
        )
        if result is None:
            return None
        return Fence(token=token, fencing_number=_to_int(result))

    async def renew_fence(
        self,
        *,
        agent_name: str,
        principal_id: str,
        session_id: str,
        token: str,
        ttl_seconds: float,
    ) -> bool:
        tag = self._tag(principal_id)
        result = await self._client.eval(
            RENEW_FENCE,
            [self._fence(agent_name, tag, session_id)],
            [token, str(_to_int(ttl_seconds))],
        )
        return bool(result)

    async def release_fence(
        self, *, agent_name: str, principal_id: str, session_id: str, token: str
    ) -> bool:
        tag = self._tag(principal_id)
        result = await self._client.eval(
            RELEASE_FENCE,
            [self._fence(agent_name, tag, session_id)],
            [token],
        )
        return bool(result)

    async def current_fence(
        self, *, agent_name: str, principal_id: str, session_id: str
    ) -> Fence | None:
        raw = await self._client.get(self._fence(agent_name, self._tag(principal_id), session_id))
        if raw is None:
            return None
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, AttributeError):
            return None
        return Fence(
            token=data.get("token", ""),
            fencing_number=_to_int(data.get("fencing_number", 0)),
        )

    # ---------------------------------------------------------------------------
    # Atomic Lua scripts (real redis) with python twins (FakeRedis).
    # Scripts are identified by their source; FakeRedis maps source -> python fn.
    # ---------------------------------------------------------------------------

    async def find_run(
        self, *, agent_name: str, principal_id: str, run_id: str
    ) -> RunRecord | None:
        raw = await self._client.eval(
            LIST_RUNS,
            [_k(self._tag(principal_id), "run", agent_name)],
            [],
        )
        for key in raw or []:
            value = await self._client.get(key)
            if not value:
                continue
            try:
                record = RunRecord.from_json(json.loads(value))
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
            if record.run_id == run_id:
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
        key = self._approval(agent_name, self._tag(principal_id), approval_id)
        await self._client.eval(
            CREATE_APPROVAL,
            [key, self._approval_index],
            [record.to_json(), f"{agent_name}:{principal_id}:{approval_id}"],
        )
        return record

    async def get_approval(
        self, *, agent_name: str, principal_id: str, approval_id: str
    ) -> ApprovalRecord | None:
        raw = await self._client.get(
            self._approval(agent_name, self._tag(principal_id), approval_id)
        )
        if not raw:
            return None
        try:
            return ApprovalRecord.from_json(json.loads(raw))
        except (ValueError, TypeError, json.JSONDecodeError):
            return None

    async def list_approvals(
        self, *, agent_name: str, principal_id: str, session_id: str
    ) -> list[ApprovalRecord]:
        entries = await self._client.eval(
            LIST_APPROVALS,
            [self._approval_index],
            [],
        )
        out: list[ApprovalRecord] = []
        for entry in entries or []:
            parts = entry.split(":")
            if len(parts) != 3:
                continue
            a_name, p_id, a_id = parts
            if a_name != agent_name or p_id != principal_id:
                continue
            record = await self.get_approval(
                agent_name=agent_name, principal_id=principal_id, approval_id=a_id
            )
            if record is not None and record.session_id == session_id:
                out.append(record)
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
        """HITL-04 CAS via Lua: only a pending, unexpired record can be
        decided; the first decision wins."""
        now = now or utcnow()
        key = self._approval(agent_name, self._tag(principal_id), approval_id)
        raw = await self._client.eval(
            DECIDE_APPROVAL,
            [key],
            [now.isoformat(), decision, reason or ""],
        )
        if not raw:
            return None
        try:
            return ApprovalRecord.from_json(json.loads(raw))
        except (ValueError, TypeError, json.JSONDecodeError):
            return None

    async def expire_approvals(self, *, now: datetime | None = None) -> list[ApprovalRecord]:
        now = now or utcnow()
        entries = await self._client.eval(
            LIST_APPROVALS,
            [self._approval_index],
            [],
        )
        expired: list[ApprovalRecord] = []
        for entry in entries or []:
            parts = entry.split(":")
            if len(parts) != 3:
                continue
            a_name, p_id, a_id = parts
            record = await self.get_approval(agent_name=a_name, principal_id=p_id, approval_id=a_id)
            if record is not None and record.pending and now > record.expires_at:
                decided = await self.decide_approval(
                    agent_name=a_name,
                    principal_id=p_id,
                    approval_id=a_id,
                    decision="timed_out",
                    now=now,
                )
                if decided is not None:
                    expired.append(decided)
        return expired


# -- approval Lua scripts (HITL-02/04) ------------------------------------------

CREATE_APPROVAL = """
local raw = redis.call('GET', KEYS[1])
if raw then return nil end
redis.call('SET', KEYS[1], ARGV[1])
redis.call('SADD', KEYS[2], ARGV[2])
return ARGV[1]
"""

LIST_APPROVALS = """
return redis.call('SMEMBERS', KEYS[1])
"""

DECIDE_APPROVAL = """
local raw = redis.call('GET', KEYS[1])
if not raw then return nil end
local record = cjson.decode(raw)
if record['status'] ~= 'pending' then return nil end
if ARGV[1] > record['expires_at'] and ARGV[2] ~= 'timed_out' then return nil end
record['status'] = ARGV[2]
record['reason'] = ARGV[3]
record['decided_at'] = ARGV[1]
record['revision'] = record['revision'] + 1
redis.call('SET', KEYS[1], cjson.encode(record))
return cjson.encode(record)
"""

CREATE_SESSION = """
if redis.call('EXISTS', KEYS[1]) == 1 then return nil end
local now = tonumber(ARGV[2])
local cutoff = now - tonumber(ARGV[4])
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', cutoff)
if redis.call('ZCARD', KEYS[2]) >= tonumber(ARGV[3]) then return 'capacity' end
redis.call('SET', KEYS[1], ARGV[1])
redis.call('ZADD', KEYS[2], now, ARGV[5])
if tonumber(ARGV[4]) > 0 then redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[4]) * 1000) end
return 'ok'
"""

MUTATE_SESSION = """
local raw = redis.call('GET', KEYS[1])
if not raw then return nil end
local rec = cjson.decode(raw)
local expected = tonumber(ARGV[1])
if rec.revision ~= expected then return 'rev:' .. tostring(expected) end
rec.revision = rec.revision + 1
for _, ev in ipairs(cjson.decode(ARGV[2])) do table.insert(rec.events, ev) end
for k, v in pairs(cjson.decode(ARGV[3])) do rec.usage[k] = (rec.usage[k] or 0) + v end
if ARGV[4] == '1' then rec.history_truncated = true end
rec.updated_at = ARGV[5]
redis.call('SET', KEYS[1], cjson.encode(rec))
if tonumber(ARGV[6]) > 0 then redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[6]) * 1000) end
return cjson.encode(rec)
"""

DELETE_SESSION = """
if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
for _, k in ipairs(redis.call('KEYS', KEYS[2])) do
  local r = cjson.decode(redis.call('GET', k))
  if r.status ~= 'succeeded' and r.status ~= 'failed' and r.status ~= 'cancelled' then
    return 'busy:' .. r.run_id
  end
end
redis.call('DEL', KEYS[1])
for _, k in ipairs(redis.call('KEYS', KEYS[2])) do redis.call('DEL', k) end
for _, k in ipairs(redis.call('KEYS', KEYS[3])) do redis.call('DEL', k) end
redis.call('DEL', KEYS[4], KEYS[5])
redis.call('ZREM', KEYS[6], ARGV[1])
return 1
"""

LIST_SESSIONS = """
return redis.call('ZRANGE', KEYS[1], 0, -1)
"""

CREATE_RUN = """
if redis.call('EXISTS', KEYS[1]) == 0 then return 'missing:' .. KEYS[1] end
if redis.call('EXISTS', KEYS[2]) == 1 then return 'ok' end
local cap = tonumber(ARGV[2])
local pattern = string.gsub(KEYS[2], 'run:[^:]*$', 'run:*')
local existing = redis.call('KEYS', pattern)
local terminal = {}
for _, k in ipairs(existing) do
  local r = cjson.decode(redis.call('GET', k))
  if r.status == 'succeeded' or r.status == 'failed' or r.status == 'cancelled' then
    table.insert(terminal, {k, r.updated_at})
  end
end
table.sort(terminal, function(a, b) return a[2] < b[2] end)
while #existing >= cap and #terminal > 0 do
  redis.call('DEL', terminal[1][1])
  table.remove(terminal, 1)
  existing = redis.call('KEYS', pattern)
end
if #existing >= cap then return 'capacity:maxRunsPerSession' end
redis.call('SET', KEYS[2], ARGV[1])
return 'ok'
"""

CREATE_IDEM = """
if redis.call('EXISTS', KEYS[1]) == 1 then return 'ok' end
local pattern = string.gsub(KEYS[1], 'idem:[^:]*$', 'idem:*')
local count = #redis.call('KEYS', pattern)
if count >= tonumber(ARGV[2]) then return 'capacity:maxIdempotencyRecordsPerSession' end
redis.call('SET', KEYS[1], ARGV[1])
if tonumber(ARGV[3]) > 0 then redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[3]) * 1000) end
return 'ok'
"""

LIST_RUNS = """
return redis.call('KEYS', KEYS[1])
"""

ACQUIRE_FENCE = """
local raw = redis.call('GET', KEYS[1])
if raw then
  local f = cjson.decode(raw)
  if f.expires_at == 0 or f.expires_at > tonumber(ARGV[2]) then return nil end
end
local count = tonumber(redis.call('GET', KEYS[2]) or '0') + 1
redis.call('SET', KEYS[2], tostring(count))
local fence = {token = ARGV[1], fencing_number = count,
 expires_at = tonumber(ARGV[2]) + tonumber(ARGV[3])}
redis.call('SET', KEYS[1], cjson.encode(fence))
redis.call('PEXPIRE', KEYS[3], tonumber(ARGV[3]) * 1000)
return tostring(count)
"""

RENEW_FENCE = """
local raw = redis.call('GET', KEYS[1])
if not raw then return 0 end
local f = cjson.decode(raw)
if f.token ~= ARGV[1] then return 0 end
f.expires_at = tonumber(f.expires_at) + tonumber(ARGV[2])
redis.call('SET', KEYS[1], cjson.encode(f))
return 1
"""

RELEASE_FENCE = """
local raw = redis.call('GET', KEYS[1])
if not raw then return 0 end
local f = cjson.decode(raw)
if f.token ~= ARGV[1] then return 0 end
redis.call('DEL', KEYS[1])
return 1
"""


SWEEP_RUNS = """
local now = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
local deleted = 0
for _, k in ipairs(redis.call('KEYS', 'agentbase:*:run:*')) do
  local raw = redis.call('GET', k)
  if raw then
    local r = cjson.decode(raw)
    local terminal = r.status == 'succeeded' or r.status == 'failed' or r.status == 'cancelled'
    if terminal and (now - tonumber(r.updated_at or 0)) > ttl then
      redis.call('DEL', k)
      deleted = deleted + 1
    end
  end
end
return deleted
"""


TRUNCATE_SESSION = """
local raw = redis.call('GET', KEYS[1])
if not raw then return 0 end
local rec = cjson.decode(raw)
local keep = tonumber(ARGV[1])
if rec.revision <= keep then return 0 end
local drop = rec.revision - keep
for i = 1, drop do table.remove(rec.events) end
rec.revision = keep
redis.call('SET', KEYS[1], cjson.encode(rec))
return drop
"""
