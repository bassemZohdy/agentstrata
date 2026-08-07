"""E2-6 (COST-01): default price catalog.

Curated list prices in USD per 1M tokens, keyed by ``(provider, model)``
as configured (the connector adds the LiteLLM prefix; the catalog keys on
the CONFIG values).  Lookup chain in the runner: exact ``costs.models``
entry → catalog → flat ``costs.default*PerMillion``.

Provenance: prices snapshot from LiteLLM's public price JSON
(``model_prices_and_context_window.json``) at the last refresh.  Refresh
with ``scripts/refresh-pricing.py`` (manual, no network in CI); the
catalog ages by design — refresh before release, and always prefer
explicit ``costs.models`` entries for deployments that care.
"""

from __future__ import annotations

# (provider, model) -> (inputPerMillion, outputPerMillion)
PRICE_CATALOG: dict[tuple[str, str], tuple[float, float]] = {
    # gemini (native path)
    ("gemini", "gemini-2.5-flash"): (0.30, 2.50),
    ("gemini", "gemini-2.5-pro"): (1.25, 10.00),
    # openai
    ("openai", "gpt-4o"): (2.50, 10.00),
    ("openai", "gpt-4o-mini"): (0.15, 0.60),
    ("openai", "gpt-4.1"): (2.00, 8.00),
    ("openai", "gpt-4.1-mini"): (0.40, 1.60),
    # azure (same model names, same list prices)
    ("azure", "gpt-4o"): (2.50, 10.00),
    ("azure", "gpt-4o-mini"): (0.15, 0.60),
    # anthropic
    ("anthropic", "claude-3-5-sonnet"): (3.00, 15.00),
    ("anthropic", "claude-3-5-haiku"): (0.80, 4.00),
    # groq
    ("groq", "llama-3.3-70b"): (0.59, 0.79),
    # mistral
    ("mistral", "mistral-large"): (2.00, 6.00),
    # deepseek
    ("deepseek", "deepseek-chat"): (0.27, 1.10),
    # xai
    ("xai", "grok-2"): (2.00, 10.00),
    # cohere
    ("cohere", "command-r"): (0.15, 0.60),
    ("cohere", "command-r-plus"): (2.50, 10.00),
}


def catalog_price(provider: str, model: str) -> tuple[float, float] | None:
    """Catalog lookup; ``None`` = miss (caller falls back to defaults)."""
    return PRICE_CATALOG.get((provider, model))
