"""ADK session-service adapter tests (REQUIREMENTS.md SES-09).

Proves one revisioned transaction path shared with ADK events: a real ADK
``LlmAgent`` run driven through ``Runner.run_async`` persists every event into
the runtime backend record — no independent ADK in-memory history.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from google.adk.agents import LlmAgent
from google.adk.events import Event
from google.adk.models import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.genai import types

from app.storage.adk_adapter import AdkSessionService
from app.storage.memory import MemoryBackend

APP = "adk-app"
USER = "adk-user"


class EchoLlm(BaseLlm):
    """Minimal public-API model stub (no network)."""

    model: str = "echo"

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        for _ in llm_request.contents:
            pass
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text="pong from adapter test")])
        )


@pytest.mark.asyncio
async def test_adk_run_persists_events_via_backend():
    backend = MemoryBackend()
    await backend.initialize()
    service = AdkSessionService(backend)

    agent = LlmAgent(
        name="spike_agent",
        instruction="You are a spike agent.",
        model=EchoLlm(),
    )
    runner = Runner(agent=agent, app_name=APP, session_service=service)

    session = await service.create_session(app_name=APP, user_id=USER)
    assert session.id

    async for event in runner.run_async(
        user_id=USER,
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text="ping")]),
    ):
        assert isinstance(event, Event)

    # SES-09: the authoritative backend record holds the conversation; the
    # adapter did NOT keep an independent ADK history.
    record = await backend.get_session(agent_name=APP, principal_id=USER, session_id=session.id)
    assert record is not None
    assert record.revision > 1
    texts = [
        part.get("text", "")
        for event in record.events
        if event.get("content")
        for part in event["content"].get("parts", [])
    ]
    assert "pong from adapter test" in " ".join(texts)
    assert "ping" in " ".join(texts)

    # revision CAS is enforced: a stale append fails cleanly
    from app.storage.contract import RevisionConflict

    with pytest.raises(RevisionConflict):
        await backend.mutate_session(
            agent_name=APP,
            principal_id=USER,
            session_id=session.id,
            expected_revision=0,
        )
    await backend.close()
