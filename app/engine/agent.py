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
    """One immutable agent set + connectors (ENG-01/MA-02).

    ``agent`` is the root (a coordinator carrying ``sub_agents`` when the
    config has any). ``tool_targets`` maps each agent to the MCP servers it
    may see (None = every server): the MCP manager attaches final tool names
    to the right agents on connect (MA-03).
    """

    agent: LlmAgent
    generation: int
    model: RetryableLlm
    sub_agents: tuple[LlmAgent, ...] = ()
    tool_targets: tuple[tuple[LlmAgent, list[str] | None], ...] = ()


def _merge_llm(root: Any, override: Any | None) -> Any:
    """MA-01: the sub-agent's optional llm block is deep-merged over the
    root's (sub-agent values win per leaf)."""
    if override is None:
        return root
    merged = root.model_dump(by_alias=True, mode="json")
    merged.update(override.model_dump(by_alias=True, mode="json"))
    from app.config.models import Llm

    return Llm.model_validate(merged)


def build_agent_component(config: Any, generation: int = 1) -> AgentComponent:
    """Construct the root agent (and, with a non-empty ``agents`` list, the
    sub-agents + coordinator) from the validated AgentConfig (ENG-01/MA-02).
    An empty list retains P1 behavior exactly."""
    from google.adk.agents import LlmAgent

    applied = AppliedConfig.from_config(config, generation)

    def _agent(
        name: str, instruction: str, description: str, llm_cfg: Any
    ) -> tuple[LlmAgent, RetryableLlm]:
        model = RetryableLlm(build_llm(llm_cfg))
        agent = LlmAgent(
            name=name,
            instruction=instruction,
            description=description,
            model=model,
            generate_content_config=types.GenerateContentConfig(
                temperature=applied.temperature,
                top_p=applied.top_p,
                max_output_tokens=applied.max_tokens,
            ),
        )
        return agent, model

    sub_agents: list[Any] = []  # ADK's param type is list[BaseAgent]
    tool_targets: list[tuple[LlmAgent, list[str] | None]] = []
    for agent_def in config.agents:
        merged_llm = _merge_llm(config.llm, agent_def.llm)
        sub, _ = _agent(
            agent_def.name,
            agent_def.systemInstruction,
            agent_def.description,
            merged_llm,
        )
        sub_agents.append(sub)
        tool_targets.append((sub, agent_def.toolServers))

    root, root_model = _agent(
        applied.name,
        applied.system_instruction,
        "",
        config.llm,
    )
    if sub_agents:
        # MA-02: the root becomes a coordinator carrying sub_agents in
        # configured order; routing via ADK's native transfer.
        root = LlmAgent(
            name=applied.name,
            instruction=applied.system_instruction,
            description="",
            model=root_model,
            sub_agents=sub_agents,
            generate_content_config=types.GenerateContentConfig(
                temperature=applied.temperature,
                top_p=applied.top_p,
                max_output_tokens=applied.max_tokens,
            ),
        )
    # The root sees every configured MCP server (MA-03: the coordinator's
    # own tools); sub-agents see only their toolServers.
    tool_targets.insert(0, (root, None))
    return AgentComponent(
        agent=root,
        generation=applied.generation,
        model=root_model,
        sub_agents=tuple(sub_agents),
        tool_targets=tuple(tool_targets),
    )
