"""STACK-02 spike: ADK session/event lifecycle smoke (google-adk 2.6.1).

Proves the documented lifecycle is reachable through public seams only:
- `LlmAgent` construction (ENG-01 seam)
- `Runner.run_async` event stream (ENG-02 seam)
- `BaseSessionService` implementation/injection (SES-09 seam)

Run with the project venv: `.venv/Scripts/python scripts/spike_adk_lifecycle.py`
"""

import asyncio
from collections.abc import AsyncGenerator

from google.adk.agents import LlmAgent
from google.adk.events import Event
from google.adk.models import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

APP_NAME = "spike_app"
USER_ID = "spike_user"


class EchoLlm(BaseLlm):
    """Minimal public-API model stub: answers with a fixed text response."""

    model: str = "echo"

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        for _ in llm_request.contents:  # consume input
            pass
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text="pong from EchoLlm")])
        )


async def main() -> None:
    session_service = InMemorySessionService()

    agent = LlmAgent(
        name="spike_agent",
        instruction="You are a spike agent.",
        model=EchoLlm(),
    )

    runner = Runner(
        agent=agent,
        app_name=APP_NAME,
        session_service=session_service,
    )

    session = await session_service.create_session(app_name=APP_NAME, user_id=USER_ID)
    print("session created:", session.id, "events:", len(session.events))

    seen: list[str] = []
    async for event in runner.run_async(
        user_id=USER_ID,
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text="ping")]),
    ):
        assert isinstance(event, Event)
        if event.content:
            for part in event.content.parts or []:
                if part.text:
                    seen.append(part.text)
        if event.actions and event.actions.end_of_agent:
            seen.append(f"<end_of_agent:{event.finish_reason}>")

    print("event stream saw:", seen)

    # Session must now hold the authoritative event history (SES-09 shared path).
    stored = await session_service.get_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=session.id
    )
    assert stored is not None
    print("stored session events:", len(stored.events))
    assert any(
        "pong from EchoLlm" in (part.text or "")
        for e in stored.events
        if e.content
        for part in (e.content.parts or [])
    )
    assert "pong from EchoLlm" in " ".join(seen)
    print("LIFECYCLE-SPIKE-OK")


if __name__ == "__main__":
    asyncio.run(main())
