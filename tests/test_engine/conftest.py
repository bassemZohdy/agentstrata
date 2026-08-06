"""Shared fixtures for engine tests (ACC-01: faked model responses only)."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from google.adk.agents import LlmAgent
from google.adk.models import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner as AdkRunner
from google.genai import types

from app.engine.agent import AppliedConfig
from app.engine.runner import AgentRunner
from app.storage.adk_adapter import AdkSessionService
from app.storage.memory import MemoryBackend

APP = "engine-test"


class ScriptedLlm(BaseLlm):
    """Yields a scripted sequence of LlmResponses; the script advances once
    per generate_content_async call (one per LLM turn)."""

    model: str = "mock"
    calls: int = 0

    def __init__(self, scripts) -> None:
        super().__init__(model="mock")
        self._scripts = scripts
        self._turn = 0
        self.calls = 0

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self.calls += 1
        idx = min(self._turn, len(self._scripts) - 1)
        self._turn += 1
        script = self._scripts[idx]
        for response in script:
            yield response


def text_response(text: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))


def function_call_response(name: str, call_id: str, args: dict | None = None) -> LlmResponse:
    return LlmResponse(
        content=types.Content(
            role="model",
            parts=[
                types.Part(function_call=types.FunctionCall(id=call_id, name=name, args=args or {}))
            ],
        )
    )


def error_response(code: str, message: str) -> LlmResponse:
    return LlmResponse(error_code=code, error_message=message)


@pytest.fixture()
def applied_config() -> AppliedConfig:
    # A minimal config object with engine/llm sections
    from app.config.models import AgentConfig

    config = AgentConfig.model_validate(
        {
            "name": "agent",
            "engine": {
                "systemInstruction": "You are a test agent.",
                "maxIterations": 5,
                "maxOutputBytes": 200,
                "timeoutSeconds": 10,
            },
            "llm": {"provider": "gemini", "model": "mock"},
        }
    )
    return AppliedConfig.from_config(config)


@pytest.fixture()
def backend():
    return MemoryBackend()


@pytest.fixture()
def runner_factory(applied_config, backend):
    def make(scripts, tools=None, metrics=None):
        model = ScriptedLlm(scripts)
        agent = LlmAgent(
            name=applied_config.name,
            instruction=applied_config.system_instruction,
            model=model,
            tools=tools or [],
        )
        service = AdkSessionService(backend)
        adk_runner = AdkRunner(agent=agent, app_name=APP, session_service=service)
        return (
            AgentRunner(applied_config, adk_runner, backend, app_name=APP, metrics=metrics),
            model,
        )

    return make
