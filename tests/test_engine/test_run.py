"""Engine run tests (ENG-02/05/06): success path, event stream, persistence."""

from __future__ import annotations

from app.engine.events import Done, TextDelta
from app.engine.runner import RunRequest

from .conftest import text_response


async def _collect(runner, request):
    events = [event async for event in runner.execute(request)]
    return events


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
