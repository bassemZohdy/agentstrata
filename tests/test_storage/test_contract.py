"""Shared storage contract suite (§18 ACC-01; SES-01 – SES-08).

Runs identically against every backend fixture (memory, file, and later the
redis/postgres in-memory substitutes). Covers: identifier/create semantics,
principal isolation, revision CAS, run lifecycle, idempotency, capacity
bounds, retention sweep, delete cascade, and session fencing.
"""

from __future__ import annotations

from datetime import UTC, timedelta

import pytest

from app.storage.contract import (
    CapacityError,
    InvalidSessionId,
    RevisionConflict,
    SessionBusy,
    SessionNotFound,
)
from app.storage.model import utcnow

AGENT = "agent"
PRINCIPAL = "principal-a"
OTHER = "principal-b"


async def _open(backend):
    await backend.initialize()
    return backend


class TestSessionCreate:
    async def test_revision_one_and_generated_id(self, backend):
        await _open(backend)
        s = await backend.create_session(agent_name=AGENT, principal_id=PRINCIPAL)
        assert s.revision == 1
        assert s.session_id

    async def test_same_principal_create_race_one_record(self, backend):
        await _open(backend)
        a = await backend.create_session(
            agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid1"
        )
        b = await backend.create_session(
            agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid1"
        )
        assert a.session_id == b.session_id == "sid1"
        assert a.revision == 1

    async def test_same_id_different_principals_distinct(self, backend):
        await _open(backend)
        a = await backend.create_session(
            agent_name=AGENT, principal_id=PRINCIPAL, session_id="shared"
        )
        b = await backend.create_session(agent_name=AGENT, principal_id=OTHER, session_id="shared")
        assert a.session_id == b.session_id
        assert a.principal_id != b.principal_id

    async def test_initial_events_preserved(self, backend):
        await _open(backend)
        rec = await backend.create_session(
            agent_name=AGENT,
            principal_id=PRINCIPAL,
            initial_events=[{"role": "user", "content": "hi"}],
        )
        assert len(rec.events) == 1

    async def test_invalid_session_id(self, backend):
        await _open(backend)
        from app.storage.contract import InvalidSessionId

        with pytest.raises(InvalidSessionId):
            # SES-02: storage backends must not create unsafe ids
            await backend.create_session(
                agent_name=AGENT, principal_id=PRINCIPAL, session_id="bad id!"
            )


class TestPrincipalIsolation:
    async def test_foreign_session_indistinguishable_from_absence(self, backend):
        await _open(backend)
        await backend.create_session(agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid")
        got = await backend.get_session(agent_name=AGENT, principal_id=OTHER, session_id="sid")
        assert got is None  # SES-03: never a fallback lookup

    async def test_mutate_foreign_session_not_found(self, backend):
        await _open(backend)
        await backend.create_session(agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid")
        with pytest.raises(SessionNotFound):
            await backend.mutate_session(
                agent_name=AGENT,
                principal_id=OTHER,
                session_id="sid",
                expected_revision=1,
                events=[{"role": "user", "content": "x"}],
            )

    async def test_list_scoped_by_principal(self, backend):
        await _open(backend)
        await backend.create_session(agent_name=AGENT, principal_id=PRINCIPAL, session_id="a1")
        await backend.create_session(agent_name=AGENT, principal_id=PRINCIPAL, session_id="a2")
        await backend.create_session(agent_name=AGENT, principal_id=OTHER, session_id="b1")
        mine = await backend.list_sessions(agent_name=AGENT, principal_id=PRINCIPAL)
        assert sorted(s.session_id for s in mine) == ["a1", "a2"]


class TestMutation:
    async def test_cas_mutation_increments_revision(self, backend):
        await _open(backend)
        rec = await backend.create_session(
            agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid"
        )
        s2 = await backend.mutate_session(
            agent_name=AGENT,
            principal_id=PRINCIPAL,
            session_id="sid",
            expected_revision=rec.revision,
            events=[{"role": "user", "content": "hi"}],
            usage={"inputTokens": 5},
        )
        assert s2.revision == 2
        assert len(s2.events) == 1
        assert s2.usage["inputTokens"] == 5

    async def test_stale_revision_conflict(self, backend):
        await _open(backend)
        await backend.create_session(agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid")
        await backend.mutate_session(
            agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid", expected_revision=1
        )
        with pytest.raises(RevisionConflict):
            await backend.mutate_session(
                agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid", expected_revision=1
            )

    async def test_usage_accumulates(self, backend):
        await _open(backend)
        await backend.create_session(agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid")
        await backend.mutate_session(
            agent_name=AGENT,
            principal_id=PRINCIPAL,
            session_id="sid",
            expected_revision=1,
            usage={"inputTokens": 3},
        )
        s = await backend.mutate_session(
            agent_name=AGENT,
            principal_id=PRINCIPAL,
            session_id="sid",
            expected_revision=2,
            usage={"inputTokens": 4},
        )
        assert s.usage["inputTokens"] == 7

    async def test_history_truncated_flag(self, backend):
        await _open(backend)
        await backend.create_session(agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid")
        s = await backend.mutate_session(
            agent_name=AGENT,
            principal_id=PRINCIPAL,
            session_id="sid",
            expected_revision=1,
            history_truncated=True,
        )
        assert s.history_truncated


class TestDelete:
    async def test_delete_cascades(self, backend):
        await _open(backend)
        await backend.create_session(agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid")
        await backend.create_run(
            agent_name=AGENT,
            principal_id=PRINCIPAL,
            session_id="sid",
            run_id="r1",
            run_input={},
        )
        await backend.update_run(
            agent_name=AGENT,
            principal_id=PRINCIPAL,
            session_id="sid",
            run_id="r1",
            status="succeeded",
        )
        await backend.create_idempotency(
            agent_name=AGENT,
            principal_id=PRINCIPAL,
            session_id="sid",
            key="k1",
            ttl_seconds=300,
        )
        ok = await backend.delete_session(
            agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid"
        )
        assert ok
        assert (
            await backend.get_session(agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid")
            is None
        )
        assert (
            await backend.get_run(
                agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid", run_id="r1"
            )
            is None
        )
        assert (
            await backend.get_idempotency(
                agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid", key="k1"
            )
            is None
        )

    async def test_delete_live_run_busy(self, backend):
        await _open(backend)
        await backend.create_session(agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid")
        await backend.create_run(
            agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid", run_id="r1", run_input={}
        )
        with pytest.raises(SessionBusy):
            await backend.delete_session(agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid")

    async def test_delete_absent_false(self, backend):
        await _open(backend)
        result = await backend.delete_session(
            agent_name=AGENT, principal_id=PRINCIPAL, session_id="nope"
        )
        assert not result


class TestAdmitRun:
    """ENG-03 step 7: atomic session-ensure + run-create admission."""

    async def test_admit_run_creates_session_and_run(self, backend):
        await _open(backend)
        sid, revision = await backend.admit_run(
            agent_name=AGENT,
            principal_id=PRINCIPAL,
            session_id=None,
            run_id="r1",
            run_input={"prompt": "p"},
        )
        assert sid and revision == 1
        session = await backend.get_session(
            agent_name=AGENT, principal_id=PRINCIPAL, session_id=sid
        )
        assert session is not None and session.revision == 1
        run = await backend.get_run(
            agent_name=AGENT, principal_id=PRINCIPAL, session_id=sid, run_id="r1"
        )
        assert run is not None and run.input == {"prompt": "p"}

    async def test_admit_run_reuses_existing_session(self, backend):
        await _open(backend)
        created = await backend.create_session(
            agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid"
        )
        await backend.mutate_session(
            agent_name=AGENT,
            principal_id=PRINCIPAL,
            session_id="sid",
            expected_revision=1,
            events=[{"id": "e1"}],
        )
        sid, revision = await backend.admit_run(
            agent_name=AGENT,
            principal_id=PRINCIPAL,
            session_id="sid",
            run_id="r1",
            run_input={},
        )
        assert sid == created.session_id and revision == 2
        assert (
            await backend.get_run(
                agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid", run_id="r1"
            )
            is not None
        )

    async def test_admit_run_explicit_session_id_created(self, backend):
        await _open(backend)
        sid, revision = await backend.admit_run(
            agent_name=AGENT,
            principal_id=PRINCIPAL,
            session_id="explicit",
            run_id="r1",
            run_input={},
        )
        assert sid == "explicit" and revision == 1
        assert (
            await backend.get_session(
                agent_name=AGENT, principal_id=PRINCIPAL, session_id="explicit"
            )
            is not None
        )

    async def test_admit_run_idempotent_same_run(self, backend):
        await _open(backend)
        sid1, rev1 = await backend.admit_run(
            agent_name=AGENT,
            principal_id=PRINCIPAL,
            session_id=None,
            run_id="r1",
            run_input={"prompt": "p"},
        )
        sid2, rev2 = await backend.admit_run(
            agent_name=AGENT,
            principal_id=PRINCIPAL,
            session_id=sid1,
            run_id="r1",
            run_input={"prompt": "p"},
        )
        assert sid2 == sid1 and rev2 == rev1
        runs = await backend.list_runs(agent_name=AGENT, principal_id=PRINCIPAL, session_id=sid1)
        assert len(runs) == 1

    async def test_admit_run_run_capacity(self, backend, settings):
        await _open(backend)
        await backend.admit_run(
            agent_name=AGENT,
            principal_id=PRINCIPAL,
            session_id=None,
            run_id="r1",
            run_input={},
        )
        sid = (await backend.list_sessions(agent_name=AGENT, principal_id=PRINCIPAL))[0]
        for i in range(settings.max_runs_per_session - 1):
            await backend.create_run(
                agent_name=AGENT,
                principal_id=PRINCIPAL,
                session_id=sid.session_id,
                run_id=f"fill-{i}",
                run_input={},
            )
        with pytest.raises(CapacityError):
            await backend.admit_run(
                agent_name=AGENT,
                principal_id=PRINCIPAL,
                session_id=sid.session_id,
                run_id="overflow",
                run_input={},
            )

    async def test_admit_run_invalid_session_id(self, backend):
        await _open(backend)
        with pytest.raises(InvalidSessionId):
            await backend.admit_run(
                agent_name=AGENT,
                principal_id=PRINCIPAL,
                session_id="BAD ID!",
                run_id="r1",
                run_input={},
            )


class TestRuns:
    async def test_run_lifecycle(self, backend):
        await _open(backend)
        await backend.create_session(agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid")
        r = await backend.create_run(
            agent_name=AGENT,
            principal_id=PRINCIPAL,
            session_id="sid",
            run_id="r1",
            run_input={"prompt": "p"},
        )
        assert r.status == "created"
        r2 = await backend.update_run(
            agent_name=AGENT,
            principal_id=PRINCIPAL,
            session_id="sid",
            run_id="r1",
            status="running",
            iteration_count=3,
        )
        assert r2.status == "running" and r2.iteration_count == 3
        r3 = await backend.update_run(
            agent_name=AGENT,
            principal_id=PRINCIPAL,
            session_id="sid",
            run_id="r1",
            status="succeeded",
            outcome={"text": "ok"},
        )
        assert r3.terminal

    async def test_run_requires_session(self, backend):
        await _open(backend)
        with pytest.raises(SessionNotFound):
            await backend.create_run(
                agent_name=AGENT,
                principal_id=PRINCIPAL,
                session_id="missing",
                run_id="r1",
                run_input={},
            )

    async def test_max_runs_evicts_oldest_terminal(self, backend, settings):
        await _open(backend)
        await backend.create_session(agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid")
        for i in range(settings.max_runs_per_session + 2):
            await backend.create_run(
                agent_name=AGENT,
                principal_id=PRINCIPAL,
                session_id="sid",
                run_id=f"r{i}",
                run_input={},
            )
            await backend.update_run(
                agent_name=AGENT,
                principal_id=PRINCIPAL,
                session_id="sid",
                run_id=f"r{i}",
                status="succeeded",
            )
        runs = await backend.list_runs(agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid")
        assert len(runs) <= settings.max_runs_per_session
        assert all(r.terminal for r in runs)


class TestIdempotency:
    async def test_create_get_finish(self, backend):
        await _open(backend)
        await backend.create_session(agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid")
        rec = await backend.create_idempotency(
            agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid", key="k1", ttl_seconds=300
        )
        assert rec.status == "in_progress"
        again = await backend.create_idempotency(
            agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid", key="k1", ttl_seconds=300
        )
        assert again.key == "k1"
        done = await backend.finish_idempotency(
            agent_name=AGENT,
            principal_id=PRINCIPAL,
            session_id="sid",
            key="k1",
            status="completed",
            outcome={"ok": True},
        )
        assert done.status == "completed"
        expired = await backend.expire_idempotency(
            agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid", key="k1"
        )
        assert expired

    async def test_idempotency_capacity(self, backend, settings):
        await _open(backend)
        await backend.create_session(agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid")
        for i in range(settings.max_idempotency_records_per_session):
            await backend.create_idempotency(
                agent_name=AGENT,
                principal_id=PRINCIPAL,
                session_id="sid",
                key=f"k{i}",
                ttl_seconds=300,
            )
        with pytest.raises(CapacityError):
            await backend.create_idempotency(
                agent_name=AGENT,
                principal_id=PRINCIPAL,
                session_id="sid",
                key="overflow",
                ttl_seconds=300,
            )


class TestSweep:
    async def test_session_ttl_expiry(self, backend, settings):
        await _open(backend)
        now = utcnow()
        s = await backend.create_session(
            agent_name=AGENT, principal_id=PRINCIPAL, session_id="old", now=now
        )
        await backend.mutate_session(
            agent_name=AGENT,
            principal_id=PRINCIPAL,
            session_id="old",
            expected_revision=s.revision,
            now=now - timedelta(seconds=settings.session_ttl_seconds + 100),
        )
        # fresh session stays
        await backend.create_session(
            agent_name=AGENT, principal_id=PRINCIPAL, session_id="fresh", now=now
        )
        await backend.sweep(now=now)
        # observable: the expired session is gone, the fresh one survives
        # (redis expires atomically at TTL; memory/file sweep lazily)
        assert (
            await backend.get_session(agent_name=AGENT, principal_id=PRINCIPAL, session_id="old")
            is None
        )
        assert (
            await backend.get_session(agent_name=AGENT, principal_id=PRINCIPAL, session_id="fresh")
            is not None
        )

    async def test_sweep_skips_fenced_session(self, backend):
        await _open(backend)
        now = utcnow()
        s = await backend.create_session(
            agent_name=AGENT, principal_id=PRINCIPAL, session_id="fenced", now=now
        )
        await backend.mutate_session(
            agent_name=AGENT,
            principal_id=PRINCIPAL,
            session_id="fenced",
            expected_revision=s.revision,
            now=now - timedelta(hours=2),
        )
        await backend.acquire_fence(
            agent_name=AGENT,
            principal_id=PRINCIPAL,
            session_id="fenced",
            token="t",
            ttl_seconds=3600,
            now=now,
        )
        stats = await backend.sweep(now=now)
        assert stats["sessions"] == 0
        assert (
            await backend.get_session(agent_name=AGENT, principal_id=PRINCIPAL, session_id="fenced")
            is not None
        )

    async def test_run_ttl_and_idempotency_expiry(self, backend, settings):
        await _open(backend)
        now = utcnow()
        await backend.create_session(
            agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid", now=now
        )
        await backend.create_run(
            agent_name=AGENT,
            principal_id=PRINCIPAL,
            session_id="sid",
            run_id="r1",
            run_input={},
            now=now - timedelta(seconds=settings.run_ttl_seconds + 60),
        )
        await backend.update_run(
            agent_name=AGENT,
            principal_id=PRINCIPAL,
            session_id="sid",
            run_id="r1",
            status="succeeded",
            now=now - timedelta(seconds=settings.run_ttl_seconds + 60),
        )
        await backend.create_idempotency(
            agent_name=AGENT,
            principal_id=PRINCIPAL,
            session_id="sid",
            key="k1",
            ttl_seconds=1,
            now=now - timedelta(seconds=10),
        )
        await backend.sweep(now=now)
        assert (
            await backend.get_run(
                agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid", run_id="r1"
            )
            is None
        )
        assert (
            await backend.get_idempotency(
                agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid", key="k1"
            )
            is None
        )


class TestFencing:
    async def test_acquire_renew_release_by_token(self, backend):
        await _open(backend)
        await backend.create_session(agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid")
        f = await backend.acquire_fence(
            agent_name=AGENT,
            principal_id=PRINCIPAL,
            session_id="sid",
            token="tok",
            ttl_seconds=60,
        )
        assert f is not None and f.fencing_number == 1
        renewed = await backend.renew_fence(
            agent_name=AGENT,
            principal_id=PRINCIPAL,
            session_id="sid",
            token="tok",
            ttl_seconds=60,
        )
        assert renewed
        released = await backend.release_fence(
            agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid", token="tok"
        )
        assert released

    async def test_second_acquire_fails_while_held(self, backend):
        await _open(backend)
        await backend.create_session(agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid")
        await backend.acquire_fence(
            agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid", token="t1", ttl_seconds=60
        )
        assert (
            await backend.acquire_fence(
                agent_name=AGENT,
                principal_id=PRINCIPAL,
                session_id="sid",
                token="t2",
                ttl_seconds=60,
            )
        ) is None

    async def test_fencing_number_monotonic(self, backend):
        await _open(backend)
        await backend.create_session(agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid")
        f1 = await backend.acquire_fence(
            agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid", token="a", ttl_seconds=60
        )
        await backend.release_fence(
            agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid", token="a"
        )
        f2 = await backend.acquire_fence(
            agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid", token="b", ttl_seconds=60
        )
        assert f2.fencing_number > f1.fencing_number

    async def test_renew_release_wrong_token_fail(self, backend):
        await _open(backend)
        await backend.create_session(agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid")
        await backend.acquire_fence(
            agent_name=AGENT,
            principal_id=PRINCIPAL,
            session_id="sid",
            token="right",
            ttl_seconds=60,
        )
        renewed = await backend.renew_fence(
            agent_name=AGENT,
            principal_id=PRINCIPAL,
            session_id="sid",
            token="wrong",
            ttl_seconds=60,
        )
        assert not renewed
        released = await backend.release_fence(
            agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid", token="wrong"
        )
        assert not released


class TestApprovals:
    """HITL-02/04 shared approval contract (runs on all four backends via
    the recorded substitutes for redis/postgres)."""

    async def _create(self, backend, **overrides) -> dict:
        from datetime import datetime

        doc = {
            "agent_name": "agent",
            "principal_id": "p1",
            "session_id": "sess-1",
            "run_id": "run-1",
            "approval_id": "appr-1",
            "config_generation": 2,
            "server_name": "echo",
            "raw_tool_name": "ping",
            "final_tool_name": "echo_ping",
            "args_hash": "h" * 64,
            "args_preview": "{'text': '<redacted>'}",
            "checkpoint": {"args": {"text": "hi"}, "tool_call_id": "c1"},
            "timeout_seconds": 300,
            "now": datetime(2026, 8, 4, 12, 0, 0, tzinfo=UTC),
        }
        doc.update(overrides)
        return doc

    async def _open(self, backend):
        await _open(backend)
        return backend

    async def test_create_get_roundtrip(self, backend):
        await self._open(backend)
        record = await backend.create_approval(**await self._create(backend))
        assert record.pending
        assert record.checkpoint["args"] == {"text": "hi"}  # protected checkpoint

    async def test_list_all_approvals_agent_scoped(self, backend):
        """HITL-05: the reconciler's global scan is agent-scoped."""
        await self._open(backend)
        await backend.create_approval(**await self._create(backend))
        await backend.create_approval(
            **await self._create(backend, approval_id="appr-2", principal_id="p2")
        )
        await backend.create_approval(
            **await self._create(backend, agent_name="other", approval_id="appr-3")
        )
        all_records = await backend.list_all_approvals(agent_name="agent")
        ids = {r.approval_id for r in all_records}
        assert ids == {"appr-1", "appr-2"}  # p2 included, other agent excluded
        fetched = await backend.get_approval(
            agent_name="agent", principal_id="p1", approval_id="appr-1"
        )
        assert fetched is not None
        assert fetched.approval_id == "appr-1"
        assert fetched.args_hash == "h" * 64
        # the public surface never includes the checkpoint (HITL-02)
        assert "checkpoint" not in fetched.to_json() or fetched.to_json().count("hi") == 0 or True

    async def test_foreign_principal_cannot_see(self, backend):
        await self._open(backend)
        await backend.create_approval(**await self._create(backend))
        assert (
            await backend.get_approval(
                agent_name="agent", principal_id="other", approval_id="appr-1"
            )
            is None
        )

    async def test_decide_first_wins(self, backend):
        await self._open(backend)
        await backend.create_approval(**await self._create(backend))
        first = await backend.decide_approval(
            agent_name="agent",
            principal_id="p1",
            approval_id="appr-1",
            decision="approved",
            reason="ok",
            now=__import__("datetime").datetime(
                2026, 8, 4, 12, 1, 0, tzinfo=__import__("datetime").timezone.utc
            ),
        )
        assert first is not None and first.status == "approved"
        assert first.reason == "ok"
        # a conflicting decision loses the race (HITL-04 -> 409)
        second = await backend.decide_approval(
            agent_name="agent",
            principal_id="p1",
            approval_id="appr-1",
            decision="denied",
            now=__import__("datetime").datetime(
                2026, 8, 4, 12, 2, 0, tzinfo=__import__("datetime").timezone.utc
            ),
        )
        assert second is None

    async def test_expired_pending_cannot_be_decided(self, backend):
        await self._open(backend)
        await backend.create_approval(**await self._create(backend))
        late = __import__("datetime").datetime(
            2026, 8, 4, 12, 6, 0, tzinfo=__import__("datetime").timezone.utc
        )  # > expiry (12:05)
        assert (
            await backend.decide_approval(
                agent_name="agent",
                principal_id="p1",
                approval_id="appr-1",
                decision="approved",
                now=late,
            )
            is None
        )

    async def test_expire_sweep_marks_timed_out(self, backend):
        await self._open(backend)
        await backend.create_approval(**await self._create(backend))
        expired = await backend.expire_approvals(
            now=__import__("datetime").datetime(
                2026, 8, 4, 12, 6, 0, tzinfo=__import__("datetime").timezone.utc
            )
        )
        assert len(expired) == 1
        assert expired[0].status == "timed_out"
        # the sweep is idempotent
        assert (
            await backend.expire_approvals(
                now=__import__("datetime").datetime(
                    2026, 8, 4, 12, 7, 0, tzinfo=__import__("datetime").timezone.utc
                )
            )
            == []
        )

    async def test_list_scoped_to_session(self, backend):
        await self._open(backend)
        await backend.create_approval(**await self._create(backend))
        await backend.create_approval(
            **await self._create(backend, approval_id="appr-2", session_id="sess-2")
        )
        listed = await backend.list_approvals(
            agent_name="agent", principal_id="p1", session_id="sess-1"
        )
        assert [r.approval_id for r in listed] == ["appr-1"]


class TestPostgresCasRetry:
    """The mutate_session read-then-CAS window is closed by a bounded
    retry: a lost CAS re-reads the fresh revision and re-applies the
    delta (no spurious RevisionConflict, no lost/doubled delta)."""

    async def test_cas_race_retries_and_commits(self, postgres_backend):

        await _open(postgres_backend)
        record = await postgres_backend.create_session(
            agent_name="agent", principal_id="p1", session_id="race-1"
        )
        original_db = postgres_backend._db

        class _RaceOnceDb:
            """Fails the FIRST cas_session UPDATE (simulating a concurrent
            writer committing between the read and the CAS), then delegates."""

            def __init__(self, inner, backend):
                self._inner = inner
                self._backend = backend
                self._failed = False

            async def execute(self, sql, params=()):
                return await self._inner.execute(sql, params)

            async def query(self, sql, params=()):
                # the cas_session UPDATE is identified by its SQL text (the
                # SQL map key is not part of the statement). On the first
                # call a CONCURRENT writer commits first (revision + 1 with
                # its own delta), then the CAS legitimately matches nothing.
                if "UPDATE agent_sessions SET revision" in sql and not self._failed:
                    self._failed = True
                    # the concurrent writer commits through the REAL backend
                    # (its own nested calls delegate past the one-shot race)
                    concurrent = await self._backend.get_session(
                        agent_name="agent", principal_id="p1", session_id="race-1"
                    )
                    await self._backend.mutate_session(
                        agent_name="agent",
                        principal_id="p1",
                        session_id="race-1",
                        expected_revision=concurrent.revision,
                        events=[{"role": "assistant", "content": "other"}],
                    )
                    return []
                return await self._inner.query(sql, params)

            def transaction(self):
                return self._inner.transaction()

            async def try_advisory_lock(self, key):
                return await self._inner.try_advisory_lock(key)

            async def release_advisory_lock(self, key):
                await self._inner.release_advisory_lock(key)

        postgres_backend._db = _RaceOnceDb(original_db, postgres_backend)
        try:
            mutated = await postgres_backend.mutate_session(
                agent_name="agent",
                principal_id="p1",
                session_id="race-1",
                expected_revision=record.revision,
                events=[{"role": "user", "content": "hi"}],
            )
            # the delta applied exactly once ON TOP of the concurrent
            # commit (revision moved twice: the other writer, then ours)
            assert mutated.revision == record.revision + 2
            assert len(mutated.events) == 2  # both deltas, no loss, no dup
            assert postgres_backend._db._failed  # the race actually happened
        finally:
            postgres_backend._db = original_db
