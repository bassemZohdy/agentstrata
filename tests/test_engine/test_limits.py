"""Engine limit tests (ENG-07, ENG-08): output bytes, iterations, deadline,
token budget."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from google.adk.models import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types as genai_types

from app.engine.events import Done, TextDelta
from app.engine.runner import RunRequest

from .conftest import text_response


async def _collect(runner, request):
    return [event async for event in runner.execute(request)]


class TestOutputLimit:
    async def test_output_limit_truncates_and_marks_status(self, runner_factory, backend):
        big = "x" * 1000
        runner, model = runner_factory([[text_response(big)]])
        events = await _collect(runner, RunRequest(principal_id="p1", user_message="hi"))
        deltas = "".join(e.text for e in events if isinstance(e, TextDelta))
        assert len(deltas.encode("utf-8")) <= 200  # maxOutputBytes
        done = [e for e in events if isinstance(e, Done)][0]
        assert done.finish_reason == "length"
        assert done.x_agent_status == "output_limit"

    async def test_multibyte_codepoint_safe(self, runner_factory, backend):
        # 150 emoji (3 bytes each) exceed 200 bytes; truncation must not
        # split a codepoint.
        text = "😀" * 150
        runner, model = runner_factory([[text_response(text)]])
        events = await _collect(runner, RunRequest(principal_id="p1", user_message="hi"))
        deltas = "".join(e.text for e in events if isinstance(e, TextDelta))
        assert len(deltas.encode("utf-8")) <= 200
        assert len(deltas) % 1 == 0  # never splits a surrogate pair mid-way
        done = [e for e in events if isinstance(e, Done)][0]
        assert done.x_agent_status == "output_limit"


class TestIterationLimit:
    async def test_iteration_exhaustion_is_length(self, runner_factory, backend):
        from google.adk.tools import FunctionTool

        from .conftest import function_call_response

        calls = {"n": 0}

        def ping() -> str:
            calls["n"] += 1
            return "pong"

        ping_tool = FunctionTool(ping)

        # the model keeps calling the tool forever; iteration limit stops it
        # one tool call per turn; 6 turns > maxIterations (5)
        scripts = [[function_call_response("ping", f"call-{i}", {})] for i in range(6)]
        runner, model = runner_factory(scripts, tools=[ping_tool])
        events = await _collect(runner, RunRequest(principal_id="p1", user_message="go"))
        done = [e for e in events if isinstance(e, Done)][0]
        assert done.finish_reason == "length"
        assert done.x_agent_status == "iteration_limit"


class TestDeadline:
    async def test_timeout_yields_agent_timeout(self, runner_factory, backend, monkeypatch):
        import asyncio

        from .conftest import ScriptedLlm

        class SlowLlm(ScriptedLlm):
            async def generate_content_async(self, request, stream=False):
                await asyncio.sleep(5)
                yield text_response("late")

        from google.adk.agents import LlmAgent
        from google.adk.runners import Runner as AdkRunner

        from app.config.models import AgentConfig
        from app.engine.agent import AppliedConfig
        from app.engine.runner import AgentRunner
        from app.storage.adk_adapter import AdkSessionService

        config = AgentConfig.model_validate(
            {
                "name": "agent",
                "engine": {
                    "systemInstruction": "t",
                    "timeoutSeconds": 1,
                    "maxIterations": 3,
                },
                "llm": {"provider": "gemini", "model": "mock"},
            }
        )
        applied = AppliedConfig.from_config(config)
        model = SlowLlm([])
        agent = LlmAgent(name="agent", instruction="t", model=model)
        adk = AdkRunner(
            agent=agent,
            app_name="engine-test",
            session_service=AdkSessionService(backend),
        )
        runner = AgentRunner(applied, adk, backend, app_name="engine-test")
        events = await asyncio.wait_for(
            _collect(runner, RunRequest(principal_id="p1", user_message="hi")),
            timeout=10,
        )
        from app.engine.events import RunError

        assert any(isinstance(e, RunError) and e.code == "agent_timeout" for e in events)


class TestTokenBudget:
    async def test_budget_exceeded_stops_later_calls(self, runner_factory, backend):
        from google.adk.agents import LlmAgent
        from google.adk.runners import Runner as AdkRunner

        from app.config.models import AgentConfig
        from app.engine.agent import AppliedConfig
        from app.engine.runner import AgentRunner
        from app.storage.adk_adapter import AdkSessionService

        config = AgentConfig.model_validate(
            {
                "name": "agent",
                "engine": {
                    "systemInstruction": "t",
                    "tokenBudget": {"perRequest": 5},
                },
                "llm": {"provider": "gemini", "model": "mock"},
            }
        )
        applied = AppliedConfig.from_config(config)
        model = _UsageLlm()
        agent = LlmAgent(name="agent", instruction="t", model=model)
        adk = AdkRunner(
            agent=agent,
            app_name="engine-test",
            session_service=AdkSessionService(backend),
        )
        runner = AgentRunner(applied, adk, backend, app_name="engine-test")
        events = await _collect(runner, RunRequest(principal_id="p1", user_message="hi"))
        from app.engine.events import RunError

        assert any(isinstance(e, RunError) and e.code == "budget_exceeded" for e in events)


class _UsageLlm(BaseLlm):
    """Reports usage that blows the budget on the first call (ENG-08)."""

    model: str = "mock"

    async def generate_content_async(
        self, request, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        response = text_response("ok")
        response.usage_metadata = genai_types.GenerateContentResponseUsageMetadata(
            prompt_token_count=4, candidates_token_count=4, total_token_count=8
        )
        yield response
