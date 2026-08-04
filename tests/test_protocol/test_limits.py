"""API-20 rate limiting and NFR-03 run-cap enforcement tests.

These cover the replica-local fixed-window rate limiter (429 ``rate_limited``
+ ``Retry-After``, health probes exempt) and the in-flight run cap
(503 ``overloaded`` before any model work, release on completion).
"""

from __future__ import annotations

import asyncio
import itertools
import types

import httpx
import pytest
from google.adk.agents import LlmAgent
from google.adk.models import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner as AdkRunner
from google.genai import types as genai_types

from app.config.models import AgentConfig
from app.engine.agent import AppliedConfig
from app.engine.runner import AgentRunner
from app.protocol.app import create_app
from app.storage.adk_adapter import AdkSessionService
from app.storage.memory import MemoryBackend

APP = "limits-test"


class HoldingLlm(BaseLlm):
    """Responds only once ``gate`` is set; used to keep runs in flight."""

    model: str = "mock"
    gate: asyncio.Event | None = None

    async def generate_content_async(self, llm_request, stream: bool = False):
        assert self.gate is not None
        await self.gate.wait()
        yield LlmResponse(
            content=genai_types.Content(role="model", parts=[genai_types.Part(text="done")])
        )


class StaticLlm(BaseLlm):
    """Immediately answers with ``text`` (for the reload-swap test)."""

    model: str = "mock"
    text: str = ""

    async def generate_content_async(self, llm_request, stream: bool = False):
        yield LlmResponse(
            content=genai_types.Content(role="model", parts=[genai_types.Part(text=self.text)])
        )


class MultiDeltaLlm(BaseLlm):
    """Yields one LlmResponse per ``deltas`` entry with a small delay, so a
    real streaming request must emit one content delta per entry."""

    model: str = "mock"
    deltas: list[str] = []

    async def generate_content_async(self, llm_request, stream: bool = False):
        for delta in self.deltas:
            await asyncio.sleep(0.05)
            yield LlmResponse(
                content=genai_types.Content(role="model", parts=[genai_types.Part(text=delta)])
            )


def _build_app(
    server: dict, gate: asyncio.Event, llm: BaseLlm | None = None
) -> tuple[httpx.ASGITransport, dict]:
    config = AgentConfig.model_validate(
        {
            "name": "agent",
            "engine": {"systemInstruction": "t"},
            "llm": {"provider": "gemini", "model": "mock"},
            "server": server,
        }
    )
    backend = MemoryBackend()
    applied = AppliedConfig.from_config(config)
    if llm is None:
        llm = HoldingLlm(gate=gate)
    agent = LlmAgent(name=config.name, instruction=config.engine.systemInstruction, model=llm)
    service = AdkSessionService(backend)
    adk_runner = AdkRunner(agent=agent, app_name=APP, session_service=service)
    runner = AgentRunner(applied, adk_runner, backend, app_name=APP)
    from app.engine.mcp.manager import ServerManager

    mcp = ServerManager(applied)
    components = {
        "applied": applied,
        "agent": None,
        "runner": runner,
        "mcp": mcp,
        "backend": backend,
        "session_service": service,
    }
    return httpx.ASGITransport(app=create_app(config, components, mode="standalone")), components


async def _request(
    transport: httpx.ASGITransport, method: str, url: str, json: dict | None = None
) -> httpx.Response:
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, url, json=json)


def _chat_body() -> dict:
    return {"model": "mock", "messages": [{"role": "user", "content": "hi"}]}


async def test_overloaded_at_cap_then_recovers():
    gate = asyncio.Event()
    transport, _ = _build_app({"maxConcurrentRequests": 2}, gate=gate)

    # Two runs admitted and held in model work.
    first = asyncio.create_task(_request(transport, "POST", "/v1/chat/completions", _chat_body()))
    second = asyncio.create_task(_request(transport, "POST", "/v1/chat/completions", _chat_body()))
    await asyncio.sleep(0.3)
    assert not first.done() and not second.done(), "both runs should be in flight"

    # Third concurrent run: 503 overloaded, before any model work.
    third = await _request(transport, "POST", "/v1/chat/completions", _chat_body())
    assert third.status_code == 503
    assert third.json()["error"]["code"] == "overloaded"

    # Health probes are not affected by the run cap.
    assert (await _request(transport, "GET", "/healthz")).status_code == 200
    assert (await _request(transport, "GET", "/readyz")).status_code == 200

    # Release: the held runs finish and the slot frees up.
    gate.set()
    resp1, resp2 = await asyncio.gather(first, second)
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert (
        await _request(transport, "POST", "/v1/chat/completions", _chat_body())
    ).status_code == 200


async def test_streaming_slots_are_released():
    """Streaming requests must release their run slot on completion (NFR-03;
    regression: the slot leaked on the streaming path, so after cap streaming
    requests every further request got 503 overloaded)."""
    gate = asyncio.Event()
    transport, _ = _build_app({"maxConcurrentRequests": 2}, gate=gate)
    body = {**_chat_body(), "stream": True}

    first = asyncio.create_task(_request(transport, "POST", "/v1/chat/completions", body))
    second = asyncio.create_task(_request(transport, "POST", "/v1/chat/completions", body))
    await asyncio.sleep(0.3)
    assert not first.done() and not second.done(), "both streaming runs should be in flight"

    # Cap is enforced for streaming too.
    third = await _request(transport, "POST", "/v1/chat/completions", body)
    assert third.status_code == 503
    assert third.json()["error"]["code"] == "overloaded"

    # Release: streams finish and their slots MUST be freed.
    gate.set()
    resp1, resp2 = await asyncio.gather(first, second)
    assert resp1.status_code == 200
    assert "[DONE]" in resp1.text
    assert '"content": "done"' in resp1.text, "success path must emit the model text"
    assert resp2.status_code == 200
    # Without the streaming release this next request would 503 forever.
    assert (
        await _request(transport, "POST", "/v1/chat/completions", _chat_body())
    ).status_code == 200


async def test_streaming_disconnect_releases_slot():
    """A client that disconnects mid-stream must also free its slot (the
    _stream teardown path runs on generator close)."""
    gate = asyncio.Event()
    transport, _ = _build_app({"maxConcurrentRequests": 1}, gate=gate)
    body = {**_chat_body(), "stream": True}

    held = asyncio.create_task(_request(transport, "POST", "/v1/chat/completions", body))
    await asyncio.sleep(0.3)
    # Cancel the client request mid-stream: the generator is closed, teardown
    # must release the slot.
    held.cancel()
    with pytest.raises(asyncio.CancelledError):
        await held
    await asyncio.sleep(0.2)

    gate.set()
    # A new run must be admitted (slot released by the disconnect teardown).
    assert (
        await _request(transport, "POST", "/v1/chat/completions", _chat_body())
    ).status_code == 200


async def test_rate_limited_429_with_retry_after_and_probe_exemption(monkeypatch):
    import app.protocol.ratelimit as ratelimit_mod

    # Deterministic clock: fixed UTC-second base, advancing 1 s per call.
    clock = itertools.count(1_000_000, 1)
    monkeypatch.setattr(ratelimit_mod, "time", types.SimpleNamespace(time=lambda: next(clock)))
    transport, _ = _build_app(
        {"rateLimit": {"enabled": True, "requestsPerMinute": 2}}, asyncio.Event()
    )

    for _ in range(2):
        assert (await _request(transport, "GET", "/v1/models")).status_code == 200
    limited = await _request(transport, "GET", "/v1/models")
    assert limited.status_code == 429
    body = limited.json()
    assert body["error"]["code"] == "rate_limited"
    assert body["error"]["type"] == "rate_limited"
    # Deterministic with the fake clock: the 429 fires at t=1_000_002
    # (each allow() call advances the clock by 1 s) and 1_000_002 % 60 == 42,
    # so the remaining whole seconds to reset are 60 - 42 = 18.
    assert limited.headers["retry-after"] == "18"

    # API-20: health probes are never rate-limited.
    for _ in range(5):
        assert (await _request(transport, "GET", "/healthz")).status_code == 200
    # Still limited for API routes in the same window.
    assert (await _request(transport, "GET", "/v1/models")).status_code == 429


async def test_rate_limit_disabled_by_default():
    transport, _ = _build_app({}, asyncio.Event())
    for _ in range(10):
        assert (await _request(transport, "GET", "/v1/models")).status_code == 200


async def test_rebuild_swap_reaches_chat_route():
    """A component-rebuild reload swaps components['runner'] in place; new
    requests must resolve it per request (NFR-08: later requests use the new
    generation). Regression: the route captured the runner at register time,
    so rebuilds updated /health but never the chat surface."""
    from google.adk.agents import LlmAgent
    from google.adk.runners import Runner as AdkRunner

    from app.engine.agent import AppliedConfig
    from app.engine.runner import AgentRunner
    from app.storage.adk_adapter import AdkSessionService

    gate = asyncio.Event()
    transport, components = _build_app({"maxConcurrentRequests": 100}, gate=gate)

    # Before the swap, the first runner (HoldingLlm with an unset gate would
    # block) — keep the gate set so it answers immediately.
    gate.set()
    before = await _request(transport, "POST", "/v1/chat/completions", _chat_body())
    assert before.status_code == 200
    assert before.json()["choices"][0]["message"]["content"] == "done"

    # Simulate a component-rebuild reload: same components dict, new runner.
    config = components["applied"].config
    backend = components["backend"]
    applied2 = AppliedConfig.from_config(config, generation=2)
    agent2 = LlmAgent(
        name=config.name,
        instruction=config.engine.systemInstruction,
        model=StaticLlm(text="swapped"),
    )
    service2 = AdkSessionService(backend)
    adk2 = AdkRunner(agent=agent2, app_name=APP, session_service=service2)
    components["runner"] = AgentRunner(applied2, adk2, backend, app_name=APP)

    after = await _request(transport, "POST", "/v1/chat/completions", _chat_body())
    assert after.status_code == 200
    assert after.json()["choices"][0]["message"]["content"] == "swapped"


async def test_streaming_emits_real_model_deltas():
    """API-13: a streaming request against a multi-delta model must emit one
    content delta per model delta. Regression: streaming_mode was never set on
    the ADK RunConfig, so the model call was non-streaming and the SSE surface
    emitted a single big delta at the end."""
    import re

    transport, _ = _build_app(
        {"maxConcurrentRequests": 100},
        asyncio.Event(),
        llm=MultiDeltaLlm(deltas=["a", "b", "c"]),
    )
    r = await _request(transport, "POST", "/v1/chat/completions", {**_chat_body(), "stream": True})
    assert r.status_code == 200
    content_deltas = re.findall(r'"content": "([^"]*)"', r.text)
    assert content_deltas == ["a", "b", "c"], content_deltas
