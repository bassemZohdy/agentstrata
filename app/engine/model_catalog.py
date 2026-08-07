"""E2-5: model capability registry.

Curated capability data for the models the E2-1 provider set commonly
runs, keyed by the CONFIG model name (the connector adds the LiteLLM
prefix; azure shares the openai model names, so name-keyed entries cover
both).  The registry is a REFERENCE with conservative gating:

- ``contextWindowTokens`` defaults from here when config leaves it 0
  (ENG-04 history trimming).
- A model KNOWN with ``tools=False`` is rejected at boot when MCP tools
  are configured (E2-5); an UNKNOWN model is never rejected — a stale
  table must not block valid deployments ("a stale table is worse than
  none").

Provenance/refresh: same policy as the price catalog — manual refresh
via ``scripts/refresh-pricing.py``'s upstream source (LiteLLM's
``model_prices_and_context_window.json`` carries context windows), no
network in CI.  Unknown models pass validation by design.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelCapability:
    context_window_tokens: int  # 0 = unknown
    tools: bool = True
    streaming: bool = True
    vision: bool = False
    structured_output: bool = False


MODEL_CAPABILITIES: dict[str, ModelCapability] = {
    # gemini
    "gemini-2.5-flash": ModelCapability(1_048_576, vision=True, structured_output=True),
    "gemini-2.5-pro": ModelCapability(1_048_576, vision=True, structured_output=True),
    # openai / azure (shared names)
    "gpt-4o": ModelCapability(128_000, vision=True, structured_output=True),
    "gpt-4o-mini": ModelCapability(128_000, vision=True, structured_output=True),
    "gpt-4.1": ModelCapability(1_047_576, vision=True, structured_output=True),
    "gpt-4.1-mini": ModelCapability(1_047_576, vision=True, structured_output=True),
    "gpt-3.5-turbo": ModelCapability(16_385),  # tools ok, no vision/structured
    # anthropic
    "claude-3-5-sonnet": ModelCapability(200_000, vision=True, structured_output=True),
    "claude-3-5-haiku": ModelCapability(200_000, vision=True, structured_output=True),
    # groq / together / fireworks / openrouter share open-weight names
    "llama-3.3-70b": ModelCapability(131_072, structured_output=True),
    "llama-3.1-8b": ModelCapability(131_072, structured_output=True),
    # mistral
    "mistral-large": ModelCapability(128_000, structured_output=True),
    # deepseek
    "deepseek-chat": ModelCapability(65_536, structured_output=True),
    # xai
    "grok-2": ModelCapability(131_072),
    # cohere
    "command-r": ModelCapability(128_000),
    "command-r-plus": ModelCapability(128_000, structured_output=True),
}


def capabilities_for(model: str) -> ModelCapability | None:
    """Registry lookup; ``None`` = unknown model (never rejected)."""
    return MODEL_CAPABILITIES.get(model)


def context_window_default(model: str) -> int:
    """E2-5: catalog context window for ENG-04 trimming (0 = unknown)."""
    entry = capabilities_for(model)
    return entry.context_window_tokens if entry is not None else 0
