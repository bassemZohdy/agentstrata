"""Shared storage contract suite (§18 ACC-01; SES-01 – SES-08).

Runs identically against every backend fixture (memory, file, and later the
redis/postgres in-memory substitutes). Covers: identifier/create semantics,
principal isolation, revision CAS, run lifecycle, idempotency, capacity
bounds, retention sweep, delete cascade, and session fencing.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.storage.contract import (
    CapacityError,
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
        assert s.history_truncated is True


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
        assert ok is True
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
        assert (
            await backend.delete_session(
                agent_name=AGENT, principal_id=PRINCIPAL, session_id="nope"
            )
        ) is False


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
        assert r3.terminal is True

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
        assert (
            await backend.expire_idempotency(
                agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid", key="k1"
            )
        ) is True

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
        stats = await backend.sweep(now=now)
        assert stats["sessions"] >= 1
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
        stats = await backend.sweep(now=now)
        assert stats["runs"] >= 1
        assert stats["idempotency"] >= 1


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
        assert (
            await backend.renew_fence(
                agent_name=AGENT,
                principal_id=PRINCIPAL,
                session_id="sid",
                token="tok",
                ttl_seconds=60,
            )
            is True
        )
        assert (
            await backend.release_fence(
                agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid", token="tok"
            )
            is True
        )

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
        assert (
            await backend.renew_fence(
                agent_name=AGENT,
                principal_id=PRINCIPAL,
                session_id="sid",
                token="wrong",
                ttl_seconds=60,
            )
            is False
        )
        assert (
            await backend.release_fence(
                agent_name=AGENT, principal_id=PRINCIPAL, session_id="sid", token="wrong"
            )
            is False
        )
