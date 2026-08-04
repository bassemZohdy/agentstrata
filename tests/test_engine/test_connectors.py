"""Engine connector regression tests (LLM-01..03).

Covers the production model-connector path (``build_llm`` + ``RetryableLlm``)
that the API tests bypass by injecting plain ``BaseLlm`` subclasses: the ADK
request builder reads ``agent.model.model`` at request time, so the wrapper
must expose the wrapped model's name (regression for the M8 image-based NFR
probe that surfaced ``AttributeError: 'RetryableLlm' object has no attribute
'model'``).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest
from google.adk.agents import LlmAgent
from google.adk.models import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner as AdkRunner
from google.genai import types

from app.engine.connectors import RetryableLlm
from app.engine.runner import AgentRunner
from app.storage.adk_adapter import AdkSessionService
from app.storage.memory import MemoryBackend


class FakeModel(BaseLlm):
    model: str = "fake-model"

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="ok")]))


class GatedModel(FakeModel):
    """FakeModel that blocks until ``gate`` is set (holds a run in flight)."""

    gate: asyncio.Event | None = None

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        assert self.gate is not None
        await self.gate.wait()
        async for response in super().generate_content_async(llm_request, stream=stream):
            yield response


def test_retryable_llm_exposes_wrapped_model_name():
    wrapped = RetryableLlm(FakeModel())
    # ADK's request builder does ``model.model``; a missing field raises
    # AttributeError (the M8 regression).
    assert wrapped.model == "fake-model"


@pytest.mark.asyncio
async def test_cancel_mid_run_persists_terminal_state():
    """Cancellation stress (review finding): cancelling an in-flight
    ``execute()`` must persist a terminal run record (no RuntimeError, no
    orphan), and ``aclose()`` must not raise (GeneratorExit path)."""
    import asyncio

    from app.engine.runner import RunRequest

    gate = asyncio.Event()
    held = GatedModel(gate=gate)

    async def collect(gen, out):
        async for event in gen:
            out.append(event)

    backend = MemoryBackend()
    service = AdkSessionService(backend)
    agent = LlmAgent(name="agent", instruction="t", model=RetryableLlm(held))
    adk_runner = AdkRunner(agent=agent, app_name="agent", session_service=service)
    from app.config.models import AgentConfig
    from app.engine.agent import AppliedConfig

    config = AgentConfig.model_validate(
        {
            "name": "agent",
            "engine": {"systemInstruction": "t"},
            "llm": {"provider": "gemini", "model": "fake-model"},
        }
    )
    runner = AgentRunner(AppliedConfig.from_config(config), adk_runner, backend, app_name="agent")

    gen = runner.execute(
        RunRequest(principal_id="u1", user_message="hi", request_id="r2", session_id="s-cancel-1")
    )
    out: list = []
    task = asyncio.create_task(collect(gen, out))
    await asyncio.sleep(0.3)
    assert not task.done(), "run should be blocked in the gated model call"

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # aclose() after cancellation must not raise (GeneratorExit handled).
    await gen.aclose()

    # A terminal run record was persisted for the session.
    runs = await backend.list_runs(agent_name="agent", principal_id="u1", session_id="s-cancel-1")
    assert runs, "no run record persisted after cancellation"
    status = runs[-1].status
    assert status == "cancelled" or status == "failed"


def test_secret_resolver_reads_process_env(monkeypatch):
    """A bare SecretResolver must resolve real env refs (M8 regression:
    it used to snapshot an empty dict, so apiKeyEnv never resolved in the
    production path and every provider call went out without credentials)."""
    from app.engine.connectors import SecretRef, SecretResolver

    monkeypatch.setenv("AGENTBASE_TEST_SECRET", "sk-test")
    resolver = SecretResolver()
    assert resolver.resolve(SecretRef(env="AGENTBASE_TEST_SECRET")) == "sk-test"
    # file wins over env (SEC-04)
    assert (
        resolver.resolve(SecretRef(env="AGENTBASE_TEST_SECRET", file="/nonexistent")) == "sk-test"
    )
    assert resolver.resolve(SecretRef(env="AGENTBASE_TEST_MISSING")) is None
    # explicit env dict still overrides the process env (deterministic tests)
    explicit = SecretResolver(env={"K": "v"})
    assert explicit.resolve(SecretRef(env="K")) == "v"
    assert explicit.resolve(SecretRef(env="AGENTBASE_TEST_SECRET")) is None


@pytest.mark.asyncio
async def test_retryable_llm_drives_full_run_without_retry():
    """A full engine run through the real production wrapper succeeds."""
    from app.config.models import AgentConfig
    from app.engine.agent import AppliedConfig, build_agent_component

    config = AgentConfig.model_validate(
        {
            "name": "agent",
            "engine": {"systemInstruction": "t"},
            "llm": {"provider": "gemini", "model": "fake-model"},
        }
    )
    applied = AppliedConfig.from_config(config)
    # build the production component, then swap the connector for the fake so
    # the wrapper (the part under test) is exercised without a provider call.
    component = build_agent_component(config)
    wrapped = RetryableLlm(FakeModel())
    agent = LlmAgent(
        name=component.agent.name,
        instruction=component.agent.instruction,
        model=wrapped,
    )
    backend = MemoryBackend()
    service = AdkSessionService(backend)
    adk_runner = AdkRunner(agent=agent, app_name="agent", session_service=service)
    runner = AgentRunner(applied, adk_runner, backend, app_name="agent")

    from app.engine.events import Done, TextDelta
    from app.engine.runner import RunRequest

    events = [
        e
        async for e in runner.execute(
            RunRequest(principal_id="u1", user_message="hi", request_id="r1")
        )
    ]
    done = [e for e in events if isinstance(e, Done)]
    assert done, f"no terminal event: {[type(e).__name__ for e in events]}"
    status = done[0].x_agent_status
    assert status is None or status == "success"
    assert any(isinstance(e, TextDelta) and e.text == "ok" for e in events), (
        "expected the fake model text"
    )
