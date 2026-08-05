"""Per-request cost-in-dollars accounting (REQUIREMENTS.md COST-01/02).

The costs table prices tokens (USD per 1M) per model; when
``costs.enabled`` the run outcome + usage surface carry ``cost_usd`` /
``costUsd`` and the OBS-05 cost counter records. When disabled the
surface is byte-identical to before (no cost fields anywhere).
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.observability.otel import Observability
from app.protocol.app import create_app

from .conftest import build_components, make_config


def _app(costs: dict | None = None, observability: dict | None = None):
    config = make_config(observability=observability)
    if costs is not None:
        # make_config builds a pydantic model; rebuild with costs injected
        from app.config.models import AgentConfig

        doc = config.model_dump(by_alias=True, mode="json")
        doc["costs"] = costs
        config = AgentConfig.model_validate(doc)
    obs = Observability(config)
    components = build_components(config, obs)
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
