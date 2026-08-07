"""R-33 (API-12): per-request temperature / max_tokens overrides are
APPLIED to the provider call via the RunConfig.labels -> RetryableLlm
seam — no longer validated-and-discarded."""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.models import BaseLlm
from google.adk.models.llm_request import LlmRequest

from app.engine.connectors import (
    _OVERRIDE_LABEL_MAX_TOKENS,
    _OVERRIDE_LABEL_TEMPERATURE,
    RetryableLlm,
)
from app.engine.runner import AgentRunner, RunRequest

from .conftest import APP, text_response


class _CapturingLlm(BaseLlm):
    """Records the GenerateContentConfig the wrapper hands to the provider."""

    model: str = "mock"

    def __init__(self) -> None:
        super().__init__(model="mock")
        self._seen: list[LlmRequest] = []

    @property
    def seen(self) -> list[LlmRequest]:
        return self._seen

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False):
        self._seen.append(llm_request)
        yield text_response("hello")


async def _run(runner: AgentRunner, **overrides) -> list:
    request = RunRequest(
        principal_id="p1",
        user_message="hi",
        **overrides,
    )
    return [event async for event in runner.execute(request)]


def _make_runner(applied_config, backend, llm) -> tuple[AgentRunner, _CapturingLlm]:
    agent = LlmAgent(
        name=applied_config.name,
        instruction=applied_config.system_instruction,
        model=llm,
    )
    from app.engine.runner import AdkRunner
    from app.storage.adk_adapter import AdkSessionService

    service = AdkSessionService(backend)

    adk_runner = AdkRunner(agent=agent, app_name=APP, session_service=service)
    return (
        AgentRunner(applied_config, adk_runner, backend, app_name=APP),
        llm,
    )


async def test_temperature_override_reaches_provider_call(applied_config, backend):
    """R-33: temperature: 0.1 on the request lands in the provider call's
    GenerateContentConfig — and the synthetic label is stripped."""
    capturing = _CapturingLlm()
    wrapped = RetryableLlm(capturing)
    runner, _ = _make_runner(applied_config, backend, wrapped)

    await _run(runner, temperature_override=0.1)

    assert capturing.seen, "provider was never called"
    req = capturing.seen[0]
    assert req.config.temperature == 0.1
    assert _OVERRIDE_LABEL_TEMPERATURE not in (req.config.labels or {})


async def test_max_tokens_override_reaches_provider_call(applied_config, backend):
    capturing = _CapturingLlm()
    wrapped = RetryableLlm(capturing)
    runner, _ = _make_runner(applied_config, backend, wrapped)

    await _run(runner, max_tokens_override=123)

    assert capturing.seen
    req = capturing.seen[0]
    assert req.config.max_output_tokens == 123
    assert _OVERRIDE_LABEL_MAX_TOKENS not in (req.config.labels or {})


async def test_no_override_leaves_provider_config_untouched(applied_config, backend):
    capturing = _CapturingLlm()
    wrapped = RetryableLlm(capturing)
    runner, _ = _make_runner(applied_config, backend, wrapped)

    await _run(runner)

    assert capturing.seen
    req = capturing.seen[0]
    assert req.config.temperature is None
    assert req.config.max_output_tokens is None
