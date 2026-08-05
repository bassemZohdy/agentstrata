"""Shared fixtures for API tests: a FastAPI app over mock engine components."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from google.adk.agents import LlmAgent
from google.adk.models import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner as AdkRunner
from google.genai import types

from app.config.models import AgentConfig
from app.engine.agent import AppliedConfig
from app.engine.runner import AgentRunner
from app.protocol.app import create_app
from app.storage.adk_adapter import AdkSessionService
from app.storage.memory import MemoryBackend

APP_NAME = "api-test"


class EchoLlm(BaseLlm):
    model: str = "mock"

    async def generate_content_async(
        self, llm_request, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text="hello from mock")])
        )


def make_config(server: dict | None = None, observability: dict | None = None) -> AgentConfig:
    doc = {
        "name": "agent",
        "engine": {"systemInstruction": "You are a test agent."},
        "llm": {"provider": "gemini", "model": "mock"},
        "server": server or {},
    }
    if observability is not None:
        doc["observability"] = observability
    return AgentConfig.model_validate(doc)


def build_components(config: AgentConfig, observability: Any = None, model: Any = None) -> dict:
    backend = MemoryBackend()
    applied = AppliedConfig.from_config(config)
    metrics = None
    if observability is not None and getattr(observability, "prometheus_enabled", False):
        from app.observability.metrics import MetricBundle

        metrics = MetricBundle(observability)
    model = model if model is not None else EchoLlm()
    agent = LlmAgent(name=config.name, instruction=config.engine.systemInstruction, model=model)
    service = AdkSessionService(backend)
    adk_runner = AdkRunner(agent=agent, app_name=APP_NAME, session_service=service)
    runner = AgentRunner(applied, adk_runner, backend, app_name=APP_NAME, metrics=metrics)
    from app.engine.mcp.manager import ServerManager

    mcp = ServerManager(applied)
    return {
        "applied": applied,
        "agent": None,
        "runner": runner,
        "mcp": mcp,
        "backend": backend,
        "session_service": service,
        "metrics": metrics,
        "observability": observability,
    }


@pytest.fixture()
def client():
    config = make_config()
    components = build_components(config)
    app = create_app(config, components, mode="standalone")
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def app_ctx():
    config = make_config()
    components = build_components(config)
    app = create_app(config, components, mode="standalone")
    return {"app": app, "config": config, "components": components}
