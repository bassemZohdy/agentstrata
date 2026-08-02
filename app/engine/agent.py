"""ENG-01: one immutable root-agent component per Applied Config generation.

Builds the ADK ``LlmAgent`` from the config: name, instruction, model per
LLM-01, and the generate-content config (temperature, top_p,
max_output_tokens). A component swap follows REL-02; a run never observes
half of two generations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from google.adk.agents import LlmAgent
from google.genai import types

from .connectors import RetryableLlm, build_llm


@dataclass(frozen=True)
class AppliedConfig:
    """The immutable per-generation configuration snapshot the engine runs
    against (engine/llm/storage sections + capability flags)."""

    generation: int
    name: str
    system_instruction: str
    temperature: float
    top_p: float
    max_tokens: int
    max_output_bytes: int
    timeout_seconds: int
    max_iterations: int
    history_max_messages: int
    history_max_bytes: int
    context_window_tokens: int
    token_budget_per_request: int
    token_budget_per_session: int
    overrides_allow_temperature: bool
    overrides_allow_max_tokens: bool
    overrides_temperature_max: float
    overrides_max_tokens_max: int
    llm_provider: str
    llm_model: str
    config: Any = None  # the validated AgentConfig (for connectors/toolsets)

    @classmethod
    def from_config(cls, config: Any, generation: int = 1) -> AppliedConfig:
        engine = config.engine
        llm = config.llm
        return cls(
            generation=generation,
            name=config.name,
            system_instruction=engine.systemInstruction,
            temperature=engine.temperature,
            top_p=engine.topP,
            max_tokens=engine.maxTokens,
            max_output_bytes=engine.maxOutputBytes,
            timeout_seconds=engine.timeoutSeconds,
            max_iterations=engine.maxIterations,
            history_max_messages=engine.historyMaxMessages,
            history_max_bytes=engine.historyMaxBytes,
            context_window_tokens=llm.contextWindowTokens,
            token_budget_per_request=engine.tokenBudget.perRequest,
            token_budget_per_session=engine.tokenBudget.perSession,
            overrides_allow_temperature=engine.overrides.allowTemperature,
            overrides_allow_max_tokens=engine.overrides.allowMaxTokens,
            overrides_temperature_max=engine.overrides.temperatureMax,
            overrides_max_tokens_max=engine.overrides.maxTokensMax,
            llm_provider=llm.provider.value,
            llm_model=llm.model,
            config=config,
        )


@dataclass(frozen=True)
class AgentComponent:
    """One immutable agent + its model connector (ENG-01)."""

    agent: LlmAgent
    generation: int
    model: RetryableLlm


def build_agent_component(config: Any, generation: int = 1) -> AgentComponent:
    """Construct the root agent from the validated AgentConfig (ENG-01)."""
    applied = AppliedConfig.from_config(config, generation)
    model = RetryableLlm(build_llm(config.llm))
    agent = LlmAgent(
        name=applied.name,
        instruction=applied.system_instruction,
        model=model,
        generate_content_config=types.GenerateContentConfig(
            temperature=applied.temperature,
            top_p=applied.top_p,
            max_output_tokens=applied.max_tokens,
        ),
    )
    return AgentComponent(agent=agent, generation=applied.generation, model=model)
