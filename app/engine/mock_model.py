"""Deterministic in-process mock model for the release performance gates.

REQUIREMENTS.md §6 (NFR-00/NFR-02): "Release performance gates run against
the Linux amd64 image ... and the deterministic mock AgentRunner." The
``AGENT_MOCK_MODEL`` env selects this model at component build time so the
NFR-02 measurement covers exactly "request receipt through validation/
session work to serialization of a deterministic in-process mock result" —
no provider, no network, no timing jitter. The hook is inert unless the
env is set; normal deployments never construct it.
"""

from __future__ import annotations

from google.adk.models import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types


class MockLlm(BaseLlm):
    """Returns one fixed, deterministic response instantly."""

    model: str = "mock"

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False):
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="ok")]))
