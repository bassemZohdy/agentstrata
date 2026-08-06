"""Engine run tests (ENG-02/05/06): success path, event stream, persistence."""

from __future__ import annotations

import asyncio

from app.engine.events import Done, TextDelta
from app.engine.runner import RunRequest

from .conftest import text_response


async def _collect(runner, request):
    events = [event async for event in runner.execute(request)]
    return events


class _RecordingGauge:
    def __init__(self):
        self.value = 0
        self.max = 0

    def inc(self, amount: int = 1):
        self.value += amount
        self.max = max(self.max, self.value)

    def dec(self, amount: int = 1):
        self.value -= amount


class _RecordingCounter:
    def __init__(self):
        self.calls: list[tuple[int, dict[str, str] | None]] = []

    def add(self, amount: int = 1, labels: dict[str, str] | None = None):
        self.calls.append((amount, labels))


class _RecordingHistogram:
    def __init__(self):
        self.records: list[tuple[float, dict[str, str] | None]] = []

    def record(self, value: float, labels: dict[str, str] | None = None):
        self.records.append((value, labels))


class _MockMetrics:
    def __init__(self):
        self.runs_admitted = _RecordingCounter()
        self.llm_calls = _RecordingCounter()
        self.active_runs = _RecordingGauge()
        self.runs_completed = _RecordingCounter()
        self.runs_failed = _RecordingCounter()
        self.tokens = _RecordingCounter()
        self.run_duration = _RecordingHistogram()


class TestSuccessPath:
    async def test_single_text_turn(self, runner_factory, backend):
        runner, model = runner_factory([[text_response("hello world")]])
        events = await _collect(runner, RunRequest(principal_id="p1", user_message="hi"))
        deltas = [e for e in events if isinstance(e, TextDelta)]
        done = [e for e in events if isinstance(e, Done)]
        assert "".join(d.text for d in deltas) == "hello world"
        assert done and done[0].finish_reason == "stop"
        assert done[0].x_agent_status is None
        # exactly one terminal Done (ENG-05)
        assert len(done) == 1

    async def test_persists_turn_and_run_succeeded(self, runner_factory, backend):
        runner, model = runner_factory([[text_response("ok")]])
        events = await _collect(runner, RunRequest(principal_id="p1", user_message="hello"))
        done = [e for e in events if isinstance(e, Done)][0]
        _ = done
        sessions = await backend.list_sessions(agent_name="engine-test", principal_id="p1")
        assert len(sessions) == 1
        record = sessions[0]
        texts = [part.get("text", "") for e in record.events for part in e.get("parts", [])]
        assert "hello" in texts  # user message committed (ENG-06)
        assert "ok" in texts  # assistant text committed
        runs = await backend.list_runs(
            agent_name="engine-test", principal_id="p1", session_id=record.session_id
        )
        assert len(runs) == 1
        assert runs[0].status == "succeeded"

    async def test_no_history_on_failure(self, runner_factory, backend):
        # a failing model leaves the session without user/assistant turn
        from .conftest import error_response

        runner, model = runner_factory([[error_response("provider_error", "boom")]])
        events = await _collect(runner, RunRequest(principal_id="p1", user_message="hello"))
        from app.engine.events import RunError

        assert any(isinstance(e, RunError) for e in events)
        sessions = await backend.list_sessions(agent_name="engine-test", principal_id="p1")
        assert len(sessions) == 1
        assert sessions[0].events == []  # ENG-06: no history append on failure
        runs = await backend.list_runs(
            agent_name="engine-test", principal_id="p1", session_id=sessions[0].session_id
        )
        assert runs[0].status == "failed"

    async def test_session_reuse_accumulates_history(self, runner_factory, backend):
        runner, model = runner_factory([[text_response("one")], [text_response("two")]])
        req = RunRequest(principal_id="p1", user_message="first")
        await _collect(runner, req)
        sessions = await backend.list_sessions(agent_name="engine-test", principal_id="p1")
        sid = sessions[0].session_id
        req2 = RunRequest(principal_id="p1", user_message="second", session_id=sid)
        await _collect(runner, req2)
        record = await backend.get_session(
            agent_name="engine-test", principal_id="p1", session_id=sid
        )
        texts = [part.get("text", "") for e in record.events for part in e.get("parts", [])]
        assert texts == ["first", "one", "second", "two"]


class TestConcurrency:
    async def test_concurrent_runs_record_independent_durations(self, runner_factory, backend):
        """R-03: per-run state must not live on the shared runner singleton."""
        metrics = _MockMetrics()
        # Two identical scripts so each run gets the same response regardless
        # of which script entry it consumes.
        runner, model = runner_factory(
            [[text_response("a")], [text_response("b")]], metrics=metrics
        )

        async def _run(principal: str, message: str):
            return await _collect(runner, RunRequest(principal_id=principal, user_message=message))

        results = await asyncio.gather(_run("p1", "hi"), _run("p2", "ho"))
        for events in results:
            done = [e for e in events if isinstance(e, Done)]
            assert done and done[0].finish_reason == "stop"

        # Each run admitted one LLM call and one completion.
        assert len(metrics.runs_admitted.calls) == 2
        assert len(metrics.llm_calls.calls) == 2
        assert len(metrics.runs_completed.calls) == 2

        # Active-runs gauge is balanced and saw concurrency.
        assert metrics.active_runs.value == 0
        assert metrics.active_runs.max >= 2

        # Two independent duration records (would have been overwritten or
        # mismatched when _run_started lived on the singleton).
        assert len(metrics.run_duration.records) == 2
        for value, _labels in metrics.run_duration.records:
            assert value >= 0
