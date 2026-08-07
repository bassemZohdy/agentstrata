"""E2-6/E2-7 (COST-01): the default price catalog and per-agent cost
pricing (MA-02)."""

from __future__ import annotations

import dataclasses

from google.adk.agents import LlmAgent
from google.genai import types

from app.engine.connectors import RetryableLlm
from app.engine.events import Done
from app.engine.runner import AdkRunner, AgentRunner, RunRequest
from app.storage.adk_adapter import AdkSessionService

from .conftest import APP, ScriptedLlm, text_response


class _TokenScriptedLlm(ScriptedLlm):
    """ScriptedLlm that also reports deterministic token usage (ENG-08)."""

    def __init__(self, scripts) -> None:
        super().__init__(scripts)

    async def generate_content_async(self, llm_request, stream=False):
        async for r in super().generate_content_async(llm_request, stream=stream):
            r.usage_metadata = types.GenerateContentResponseUsageMetadata(
                prompt_token_count=1000, candidates_token_count=2000
            )
            yield r


def _make_runner(applied_config, backend, scripts, agent_name: str = "agent") -> AgentRunner:
    model = RetryableLlm(_TokenScriptedLlm(scripts))
    agent = LlmAgent(name=agent_name, instruction="i", model=model)
    service = AdkSessionService(backend)
    adk_runner = AdkRunner(agent=agent, app_name=APP, session_service=service)
    return AgentRunner(applied_config, adk_runner, backend, app_name=APP)


async def _collect(runner, principal: str = "p") -> list:
    return [e async for e in runner.execute(RunRequest(principal_id=principal, user_message="hi"))]


class TestPriceCatalogAndPerAgent:
    """E2-6/E2-7 (COST-01): the default price catalog and per-agent
    cost pricing (MA-02)."""

    def test_catalog_lookup(self):
        from app.engine.pricing import PRICE_CATALOG, catalog_price

        assert catalog_price("openai", "gpt-4o") == PRICE_CATALOG[("openai", "gpt-4o")]
        assert (
            catalog_price("gemini", "gemini-2.5-flash")
            == PRICE_CATALOG[("gemini", "gemini-2.5-flash")]
        )  # noqa: E501
        assert catalog_price("unknown-provider", "x") is None
        assert catalog_price("openai", "not-in-catalog") is None

    async def test_catalog_chain_exact_beats_catalog_beats_defaults(self, applied_config, backend):
        """E2-6: exact costs.models entry > catalog > flat defaults."""
        cfg = dataclasses.replace(
            applied_config,
            llm_provider="openai",
            llm_model="gpt-4o",
            costs_enabled=True,
            costs_default_input=0.10,
            costs_default_output=0.20,
            costs_models={},
            agent_llm_models={"agent": ("openai", "gpt-4o")},
        )
        runner = _make_runner(cfg, backend, [[text_response("hi")]])
        done = [e for e in await _collect(runner) if isinstance(e, Done)][0]
        # catalog price: 1000 in + 2000 out at 2.50/10.00
        assert done.usage["cost_usd"] == round((1000 * 2.5 + 2000 * 10.0) / 1e6, 6)

        # exact entry wins over the catalog
        cfg2 = dataclasses.replace(cfg, costs_models={"gpt-4o": (1.0, 2.0)})
        runner2 = _make_runner(cfg2, backend, [[text_response("hi")]])
        done2 = [e for e in await _collect(runner2) if isinstance(e, Done)][0]
        assert done2.usage["cost_usd"] == round((1000 * 1.0 + 2000 * 2.0) / 1e6, 6)

    async def test_catalog_miss_falls_back_to_defaults(self, applied_config, backend):
        cfg = dataclasses.replace(
            applied_config,
            llm_provider="openai",
            llm_model="not-in-catalog",
            costs_enabled=True,
            costs_default_input=0.10,
            costs_default_output=0.20,
            costs_models={},
            agent_llm_models={"agent": ("openai", "not-in-catalog")},
        )
        runner = _make_runner(cfg, backend, [[text_response("hi")]])
        done = [e for e in await _collect(runner) if isinstance(e, Done)][0]
        assert done.usage["cost_usd"] == round((1000 * 0.10 + 2000 * 0.20) / 1e6, 6)

    async def test_per_agent_pricing(self, applied_config, backend):
        """E2-7: tokens authored by a sub-agent are priced with the
        SUB-agent's effective (provider, model)."""
        cfg = dataclasses.replace(
            applied_config,
            llm_provider="openai",
            llm_model="gpt-4o",
            costs_enabled=True,
            costs_default_input=0.10,
            costs_default_output=0.20,
            costs_models={},
            agent_llm_models={
                "agent": ("openai", "gpt-4o"),
                "sub": ("anthropic", "claude-3-5-sonnet"),
            },
        )
        # root-authored usage -> root prices (openai/gpt-4o catalog)
        runner = _make_runner(cfg, backend, [[text_response("hi")]], agent_name="agent")
        done = [e for e in await _collect(runner) if isinstance(e, Done)][0]
        assert done.usage["cost_usd"] == round((1000 * 2.5 + 2000 * 10.0) / 1e6, 6)

        # sub-agent-authored usage -> sub prices (claude-3-5-sonnet)
        model = RetryableLlm(_TokenScriptedLlm([[text_response("sub reply")]]))
        agent = LlmAgent(name="sub", instruction="i", model=model)
        service = AdkSessionService(backend)
        adk_runner = AdkRunner(agent=agent, app_name=APP, session_service=service)
        sub_runner = AgentRunner(cfg, adk_runner, backend, app_name=APP)
        done_sub = [e for e in await _collect(sub_runner) if isinstance(e, Done)][0]
        assert done_sub.usage["cost_usd"] == round((1000 * 3.0 + 2000 * 15.0) / 1e6, 6)

    async def test_disabled_costs_still_absent(self, applied_config, backend):
        cfg = dataclasses.replace(
            applied_config, costs_enabled=False, agent_llm_models={"agent": ("gemini", "mock")}
        )
        runner = _make_runner(cfg, backend, [[text_response("hi")]])
        done = [e for e in await _collect(runner) if isinstance(e, Done)][0]
        assert "cost_usd" not in done.usage
