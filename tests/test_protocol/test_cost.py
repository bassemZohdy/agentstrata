"""Per-request cost-in-dollars accounting (REQUIREMENTS.md COST-01/02).

The costs table prices tokens (USD per 1M) per model; when
``costs.enabled`` the run outcome + usage surface carry ``cost_usd`` /
``costUsd`` and the OBS-05 cost counter records. When disabled the
surface is byte-identical to before (no cost fields anywhere).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient
from google.adk.models import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from app.observability.otel import Observability
from app.protocol.app import create_app

from .conftest import build_components, make_config


class TokenLlm(BaseLlm):
    """EchoLlm that also reports deterministic token usage (ENG-08)."""

    model: str = "mock"

    async def generate_content_async(self, llm_request, stream: bool = False):
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text="hello from mock")]),
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=1000, candidates_token_count=2000
            ),
        )


class FailingLlm(BaseLlm):
    """Always fails the model call (provider_error path, API-15)."""

    model: str = "mock"

    async def generate_content_async(self, llm_request, stream: bool = False):
        yield LlmResponse(error_code="provider_error", error_message="boom")


def _app(
    costs: dict | None = None,
    observability: dict | None = None,
    model: Any | None = None,
    server: dict | None = None,
):
    config = make_config(server=server, observability=observability)
    if costs is not None:
        # make_config builds a pydantic model; rebuild with costs injected
        from app.config.models import AgentConfig

        doc = config.model_dump(by_alias=True, mode="json")
        doc["costs"] = costs
        config = AgentConfig.model_validate(doc)
    obs = Observability(config)
    components = build_components(config, obs, model=model)
    app = create_app(config, components, mode="standalone")
    return TestClient(app), config, components


def _runner_with_costs(costs: dict) -> Any:
    """A real engine runner whose AppliedConfig carries the costs table."""
    from app.config.models import AgentConfig

    config = make_config()
    doc = config.model_dump(by_alias=True, mode="json")
    doc["costs"] = costs
    config = AgentConfig.model_validate(doc)
    _obs = Observability(config)
    components = build_components(config, _obs)
    return components["runner"]


class TestCostTable:
    def test_exact_model_entry_wins(self):
        runner = _runner_with_costs(
            {
                "enabled": True,
                "defaultInputPerMillion": 1.0,
                "defaultOutputPerMillion": 2.0,
                "models": [{"model": "mock", "inputPerMillion": 3.0, "outputPerMillion": 4.0}],
            }
        )
        cost = runner._cost_usd({"input_tokens": 1_000_000, "output_tokens": 500_000})
        assert cost == 3.0 + 2.0  # 3.0 input + 4.0*0.5 output

    def test_defaults_when_no_entry(self):
        runner = _runner_with_costs(
            {"enabled": True, "defaultInputPerMillion": 1.0, "defaultOutputPerMillion": 2.0}
        )
        assert runner._cost_usd({"input_tokens": 1_000_000, "output_tokens": 0}) == 1.0

    def test_disabled_returns_none(self):
        runner = _runner_with_costs({"enabled": False})
        assert runner._cost_usd({"input_tokens": 1, "output_tokens": 1}) is None


class TestCostSurface:
    def test_usage_carries_cost_when_enabled(self):
        client, _config, _components = _app(costs={"enabled": True})
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "mock", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["usage"]["total_tokens"] == 0
        assert "costUsd" in body["usage"]  # 0 tokens -> 0.0 cost, field present

    def test_usage_omits_cost_when_disabled(self):
        client, _config, _components = _app()
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "mock", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 200
        assert "costUsd" not in resp.json()["usage"]

    def test_run_outcome_records_cost(self):
        from app.storage.memory import MemoryBackend

        client, config, components = _app(costs={"enabled": True})
        backend = components["backend"]
        assert isinstance(backend, MemoryBackend)
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "mock", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 200
        runs = list(backend._runs.values())
        assert runs and "cost_usd" in (runs[-1].outcome or {})
        assert runs[-1].outcome["cost_usd"] == 0.0
        # COST-01: the cost also rides in the committed run usage (the
        # session usage stays token-only for the ENG-08 budget).
        assert runs[-1].usage.get("cost_usd") == 0.0
        assert runs[-1].usage.get("input_tokens") == 0

    def test_cost_metric_recorded(self):
        client, _config, components = _app(
            costs={"enabled": True, "defaultInputPerMillion": 1.0},
            observability={"prometheus": {"enabled": True}},
        )
        client.post(
            "/v1/chat/completions",
            json={"model": "mock", "messages": [{"role": "user", "content": "hi"}]},
        )
        text = client.get("/metrics").text
        assert 'agentbase_cost_usd_total{model="mock"} 0' in text

    def test_cost_metric_absent_when_disabled(self):
        client, _config, components = _app(observability={"prometheus": {"enabled": True}})
        client.post(
            "/v1/chat/completions",
            json={"model": "mock", "messages": [{"role": "user", "content": "hi"}]},
        )
        text = client.get("/metrics").text
        assert "agentbase_cost_usd_total" not in text

    def test_cost_metric_not_recorded_for_failed_run(self):
        # OBS-05: the cost counter lives only in the succeeded-run branch;
        # a failed run (no consumed tokens, no committed cost) must never
        # record agentbase_cost_usd_total.
        client, _config, components = _app(
            costs={"enabled": True, "defaultInputPerMillion": 1.0},
            observability={"prometheus": {"enabled": True}},
            model=FailingLlm(),
        )
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "mock", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 200
        assert resp.json()["choices"][0]["finish_reason"] == "error"
        runs = list(components["backend"]._runs.values())
        assert runs[-1].status == "failed"
        assert "cost_usd" not in (runs[-1].outcome or {})
        text = client.get("/metrics").text
        assert "agentbase_cost_usd_total" not in text


class TestCostLookup:
    def test_unknown_model_falls_back_to_defaults(self):
        # A models[] entry for a different model must not shadow the
        # defaults for the actual llm.model ("mock").
        runner = _runner_with_costs(
            {
                "enabled": True,
                "defaultInputPerMillion": 1.0,
                "defaultOutputPerMillion": 2.0,
                "models": [
                    {"model": "other-model", "inputPerMillion": 9.0, "outputPerMillion": 9.0}
                ],
            }
        )
        assert runner._cost_usd({"input_tokens": 1_000_000, "output_tokens": 0}) == 1.0

    def test_empty_model_list_uses_defaults(self):
        runner = _runner_with_costs(
            {
                "enabled": True,
                "defaultInputPerMillion": 2.0,
                "defaultOutputPerMillion": 0.5,
                "models": [],
            }
        )
        assert runner._cost_usd({"input_tokens": 500_000, "output_tokens": 2_000_000}) == 2.0


class TestNonzeroCostCalculation:
    def test_cost_calculated_from_reported_tokens(self):
        # A real run (mock engine) that reports 1000 input / 2000 output
        # tokens; COST-01: cost = (in*inPrice + out*outPrice) / 1e6, rounded
        # to 6 decimals.
        client, _config, components = _app(
            costs={
                "enabled": True,
                "defaultInputPerMillion": 3.0,
                "defaultOutputPerMillion": 4.0,
            },
            model=TokenLlm(),
        )
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "mock", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 200
        usage = resp.json()["usage"]
        assert usage["prompt_tokens"] == 1000
        assert usage["completion_tokens"] == 2000
        assert usage["total_tokens"] == 3000
        assert usage["costUsd"] == 0.011  # (1000*3 + 2000*4) / 1e6
        runs = list(components["backend"]._runs.values())
        assert runs[-1].usage["cost_usd"] == 0.011
        assert runs[-1].outcome["cost_usd"] == 0.011


class TestStreamingCost:
    @staticmethod
    def _parse_sse_data(line: str) -> dict:
        """Parse one ``data: {json}`` SSE line, failing loudly on garbage."""
        try:
            return json.loads(line[6:])
        except json.JSONDecodeError as exc:  # pragma: no cover - test helper
            raise AssertionError(f"malformed SSE data chunk: {line!r}") from exc

    @staticmethod
    def _usage_chunk(client) -> dict:
        # API-14: the usage chunk requires stream_options.include_usage.
        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "mock",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
                "stream_options": {"include_usage": True},
            },
        ) as resp:
            body = resp.read().decode()
        usage_chunks = [
            TestStreamingCost._parse_sse_data(line)
            for line in body.splitlines()
            if line.startswith("data: ") and '"usage"' in line
        ]
        assert len(usage_chunks) == 1
        return usage_chunks[0]["usage"]

    def test_streaming_usage_chunk_carries_costUsd_when_enabled(self):
        client, _config, _components = _app(costs={"enabled": True})
        usage = self._usage_chunk(client)
        # COST-01: the final SSE usage chunk uses the same normalized
        # OpenAI-compatible shape as non-streaming.
        assert usage["prompt_tokens"] == 0
        assert usage["total_tokens"] == 0
        assert usage["costUsd"] == 0.0
        assert "input_tokens" not in usage
        assert "cost_usd" not in usage

    def test_streaming_usage_chunk_omits_cost_when_disabled(self):
        client, _config, _components = _app()
        usage = self._usage_chunk(client)
        assert "costUsd" not in usage
        assert usage["prompt_tokens"] == 0
        assert "input_tokens" not in usage


class TestCrossSurfaceUsage:
    """R-14: chat / ACP / WebSocket share one normalized usage shape
    (prompt/completion/total_tokens + costUsd when costs computed one)."""

    @staticmethod
    def _acp_run(client, costs_enabled: bool):
        resp = client.post(
            "/acp/runs",
            json={
                "message": {"role": "user", "content": "hi"},
                "session_id": "s-r14",
            },
        )
        assert resp.status_code == 200
        return resp.json()["usage"]

    def test_acp_usage_carries_costUsd_when_enabled(self):
        client, _config, _components = _app(
            costs={"enabled": True}, server={"protocols": {"acp": True}}
        )
        usage = self._acp_run(client, costs_enabled=True)
        assert usage["prompt_tokens"] == 0
        assert usage["total_tokens"] == 0
        assert "costUsd" in usage
        assert "input_tokens" not in usage

    def test_acp_usage_omits_cost_when_disabled(self):
        client, _config, _components = _app(server={"protocols": {"acp": True}})
        usage = self._acp_run(client, costs_enabled=False)
        assert "costUsd" not in usage
        assert usage["prompt_tokens"] == 0
        assert "input_tokens" not in usage

    @staticmethod
    def _ws_done_usage(client, costs_enabled: bool) -> dict:
        with client.websocket_connect("/v1/ws") as ws:
            ws.send_json({"type": "run.start", "message": "hi"})
            while True:
                msg = ws.receive_json()
                if msg["type"] == "run.done":
                    return msg["usage"]

    def test_ws_done_usage_normalized_with_cost(self):
        client, _config, _components = _app(
            costs={"enabled": True}, server={"protocols": {"websocket": True}}
        )
        usage = self._ws_done_usage(client, costs_enabled=True)
        assert usage["prompt_tokens"] == 0
        assert usage["total_tokens"] == 0
        assert "costUsd" in usage
        assert "input_tokens" not in usage

    def test_ws_done_usage_omits_cost_when_disabled(self):
        client, _config, _components = _app(server={"protocols": {"websocket": True}})
        usage = self._ws_done_usage(client, costs_enabled=False)
        assert "costUsd" not in usage
        assert usage["prompt_tokens"] == 0
        assert "input_tokens" not in usage
