"""ACP surface tests (API-16 + frozen annex §13.1 A-1..A-6).

Exercises the /acp routes through the real app: manifest shape, non-streaming
run, streaming run with an agent_transfer event, idempotency replay, error
mapping, and ACP-disabled 404s. (The CAP gate that rejects acp: true at boot
is tested in test_validation; these tests construct the app directly.)
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import httpx
from google.adk.models import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner as AdkRunner
from google.genai import types

from app.config.models import AgentConfig
from app.engine.agent import AppliedConfig, build_agent_component
from app.engine.runner import AgentRunner
from app.protocol.app import create_app
from app.storage.adk_adapter import AdkSessionService
from app.storage.memory import MemoryBackend

APP = "acp-test"


class SequenceLlm(BaseLlm):
    """Yields the scripted responses one turn at a time (transfer-friendly)."""

    model: str = "mock"
    scripts: list[list[dict]] = []

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        idx = min(len(self.scripts) - 1, getattr(self, "_turn", 0))
        self._turn = getattr(self, "_turn", 0) + 1
        for part in self.scripts[idx]:
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(**part)]))


class StaticLlm(BaseLlm):
    model: str = "mock"
    text: str = "acp-ok"

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=self.text)]))


def _build_app(
    acp: bool = True,
    agents: list[dict] | None = None,
    model: BaseLlm | None = None,
    streaming: str = "text",
) -> tuple[httpx.ASGITransport, dict]:
    config = AgentConfig.model_validate(
        {
            "name": "agent",
            "engine": {"systemInstruction": "You are the root.", "streaming": streaming},
            "llm": {"provider": "gemini", "model": "mock"},
            "server": {"protocols": {"openaiCompat": False, "acp": acp}},
            "agents": agents or [],
        }
    )
    backend = MemoryBackend()
    component = build_agent_component(config)
    root = component.agent
    if model is not None:
        root.model = model
    service = AdkSessionService(backend)
    adk = AdkRunner(agent=root, app_name=APP, session_service=service)
    runner = AgentRunner(AppliedConfig.from_config(config), adk, backend, app_name=APP)
    components = {
        "applied": AppliedConfig.from_config(config),
        "agent": component,
        "runner": runner,
        "mcp": None,
        "backend": backend,
        "session_service": service,
    }
    return httpx.ASGITransport(app=create_app(config, components, mode="standalone")), components


async def _request(
    transport: httpx.ASGITransport, method: str, url: str, json: dict | None = None
) -> httpx.Response:
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, url, json=json)


def _run_body(stream: bool = False, **overrides) -> dict:
    body = {
        "message": {"role": "user", "content": "research X"},
        "stream": stream,
    }
    body.update(overrides)
    return body


async def test_manifest_shape_with_sub_agents():
    transport, _ = _build_app(
        acp=True,
        agents=[
            {"name": "researcher", "systemInstruction": "research"},
            {"name": "writer", "systemInstruction": "write", "description": "writes"},
        ],
    )
    r = await _request(transport, "GET", "/acp/agents")
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "agent.manifest"
    assert body["name"] == "agent"
    assert body["tools"] == []
    subs = body["sub_agents"]
    assert [s["name"] for s in subs] == ["researcher", "writer"]
    assert subs[0]["description"] == ""
    assert subs[1]["description"] == "writes"


async def test_non_streaming_run_shape():
    transport, _ = _build_app(acp=True, model=StaticLlm(text="acp-ok"))
    r = await _request(transport, "POST", "/acp/runs", _run_body())
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "run.completion"
    assert body["run_id"].startswith("run-")
    assert "session_id" in body
    assert body["choices"][0]["message"]["content"] == "acp-ok"
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"]["total_tokens"] >= 0


async def test_streaming_run_emits_agent_transfer():
    # Annex A-4: the ACP stream carries the full event vocabulary, so the
    # run uses events streaming mode (the API-13 gating still keeps text
    # mode text-only).
    transport, components = _build_app(
        acp=True,
        agents=[{"name": "researcher", "systemInstruction": "research"}],
        model=SequenceLlm(
            scripts=[
                [
                    {
                        "function_call": {
                            "id": "t1",
                            "name": "transfer_to_agent",
                            "args": {"agent_name": "researcher"},
                        }
                    }
                ],
                [{"text": "done"}],
            ]
        ),
        streaming="events",
    )
    # the sub-agent is built from the config llm (keyless gemini); give it a
    # mock so the transfer completes.
    components["agent"].agent.sub_agents[0].model = StaticLlm(text="findings")
    r = await _request(transport, "POST", "/acp/runs", _run_body(stream=True))
    assert r.status_code == 200
    assert '"type": "agent_transfer"' in r.text
    assert '"from": "agent"' in r.text and '"to": "researcher"' in r.text
    assert "data: [DONE]" in r.text


async def test_idempotency_replay():
    transport, _ = _build_app(acp=True, model=StaticLlm(text="acp-ok"))
    body = _run_body(idempotency_key="same-key")
    first = await _request(transport, "POST", "/acp/runs", body)
    second = await _request(transport, "POST", "/acp/runs", body)
    assert first.status_code == 200 and second.status_code == 200
    assert second.json()["choices"][0]["message"]["content"] == "acp-ok"


async def test_error_mapping_and_validation():
    transport, _ = _build_app(acp=True, model=StaticLlm())
    # unknown field -> 400 invalid_request
    bad = await _request(transport, "POST", "/acp/runs", _run_body(bogus=1))
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "invalid_request"
    # missing message -> 400
    missing = await _request(transport, "POST", "/acp/runs", {})
    assert missing.status_code == 400
    # wrong method -> 405
    wrong = await _request(transport, "PATCH", "/acp/agents")
    assert wrong.status_code == 405


async def test_acp_disabled_returns_404():
    transport, _ = _build_app(acp=False)
    assert (await _request(transport, "GET", "/acp/agents")).status_code == 404
    assert (await _request(transport, "POST", "/acp/runs", _run_body())).status_code == 404
