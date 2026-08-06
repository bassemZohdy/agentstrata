"""Prometheus /metrics endpoint (REQUIREMENTS.md OBS-05).

Covers the in-process registry (counters/gauges/histograms, label
cardinality cap, text exposition format) and the route integration: with
``observability.prometheus.enabled`` the endpoint serves the instrument
set recorded by a run; with it disabled the route does not exist.
"""

from __future__ import annotations

from app.observability.metrics import DEFAULT_BUCKETS, MetricsRegistry
from app.observability.otel import Observability
from app.protocol.app import create_app

from .conftest import build_components, make_config


class TestMetricsRegistry:
    def test_counter_and_labels(self):
        reg = MetricsRegistry()
        reg.add("agentbase_runs_admitted_total", 1)
        reg.add("agentbase_runs_admitted_total", 2)
        reg.add("agentbase_denials_total", 1, {"reason": "concurrency"})
        text = reg.render()
        assert "agentbase_runs_admitted_total 3" in text
        assert 'agentbase_denials_total{reason="concurrency"} 1' in text

    def test_gauge_inc_dec(self):
        reg = MetricsRegistry()
        reg.inc_gauge("agentbase_active_runs")
        reg.inc_gauge("agentbase_active_runs")
        reg.dec_gauge("agentbase_active_runs")
        assert "agentbase_active_runs 1" in reg.render()

    def test_histogram_buckets_and_sum_count(self):
        reg = MetricsRegistry()
        reg.record("agentbase_run_duration_seconds", 0.3)
        reg.record("agentbase_run_duration_seconds", 5.0)
        text = reg.render()
        assert "# TYPE agentbase_run_duration_seconds histogram" in text
        assert 'agentbase_run_duration_seconds_bucket{le="0.25"} 0' in text
        assert 'agentbase_run_duration_seconds_bucket{le="0.5"} 1' in text
        assert 'agentbase_run_duration_seconds_bucket{le="+Inf"} 2' in text
        assert "agentbase_run_duration_seconds_sum 5.3" in text
        assert "agentbase_run_duration_seconds_count 2" in text
        assert len(DEFAULT_BUCKETS) >= 10  # covers engine.timeoutSeconds up to 3600

    def test_cardinality_cap_drops_new_label_sets(self):
        reg = MetricsRegistry(max_label_sets=2)
        for i in range(5):
            reg.add("agentbase_denials_total", 1, {"reason": f"r{i}"})
        text = reg.render()
        # only the first two label sets are kept; the render never crashes
        assert text.count("agentbase_denials_total{") == 2

    def test_escaping(self):
        reg = MetricsRegistry()
        reg.add("agentbase_tool_calls_total", 1, {"tool": 'we"ird\\name'})
        # the label value is escaped: backslash doubled, quote backslash-escaped
        assert 'tool="we\\"ird\\\\name"' in reg.render()


class TestMetricsRoute:
    def test_route_records_run_end_to_end(self):
        """A chat run is reflected in the /metrics exposition."""
        from fastapi.testclient import TestClient

        config = make_config(observability={"prometheus": {"enabled": True}})
        observability = Observability(config)
        components = build_components(config, observability)
        app = create_app(config, components, mode="standalone")
        with TestClient(app) as client:
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": "mock",
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
            assert resp.status_code == 200
            metrics = client.get("/metrics")
            assert metrics.status_code == 200
            assert metrics.headers["content-type"].startswith("text/plain")
            text = metrics.text
            assert "agentbase_runs_admitted_total 1" in text
            assert 'agentbase_runs_completed_total{status="succeeded"} 1' in text
            assert "agentbase_active_runs 0" in text
            assert 'agentbase_llm_calls_total{model="mock"} 1' in text

    def test_route_absent_when_disabled(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 404

    def test_denial_recorded_on_rate_limit(self):
        """Rate-limit denials (API-20) feed the denials counter."""
        from fastapi.testclient import TestClient

        config = make_config(
            observability={"prometheus": {"enabled": True}},
            server={
                "rateLimit": {"enabled": True, "requestsPerMinute": 1},
                "auth": {"mode": "none"},
            },
        )
        observability = Observability(config)
        components = build_components(config, observability)
        app = create_app(config, components, mode="standalone")
        with TestClient(app) as client:
            first = client.post(
                "/v1/chat/completions",
                json={"model": "mock", "messages": [{"role": "user", "content": "hi"}]},
            )
            assert first.status_code == 200
            denied = client.post(
                "/v1/chat/completions",
                json={"model": "mock", "messages": [{"role": "user", "content": "hi"}]},
            )
            assert denied.status_code == 429
            text = client.get("/metrics").text
            assert 'agentbase_denials_total{reason="rate_limit"} 1' in text


class TestCardinalityAndHelp:
    """R-17: the label-set cap is PER METRIC (one high-cardinality metric
    cannot starve the others) and exposition carries # HELP."""

    def test_cap_is_per_metric(self):
        reg = MetricsRegistry(max_label_sets=2)
        # metric A exhausts its cap
        for i in range(5):
            reg.add("agentbase_denials_total", 1, {"reason": f"r{i}"})
        # metric B is unaffected — a fresh label set is still admitted
        reg.add("agentbase_runs_admitted_total", 1, {"mode": "standalone"})
        text = reg.render()
        assert text.count("agentbase_denials_total{") == 2
        assert 'agentbase_runs_admitted_total{mode="standalone"} 1' in text

    def test_exposition_includes_help(self):
        reg = MetricsRegistry()
        reg.register("agentbase_runs_admitted_total", "Runs admitted (ENG-03 step 7)")
        reg.register("agentbase_cost_usd_total", "Accumulated USD cost, by model (COST-01)")
        reg.add("agentbase_runs_admitted_total", 1)
        reg.add("agentbase_cost_usd_total", 1.0)
        text = reg.render()
        assert "# HELP agentbase_runs_admitted_total Runs admitted (ENG-03 step 7)" in text
        assert "# HELP agentbase_cost_usd_total Accumulated USD cost, by model (COST-01)" in text

    def test_help_flows_from_instrument_construction(self):
        """The description given to observability.counter() reaches the
        registry's exposition (the instruments are the production path)."""
        from app.config.models import AgentConfig
        from app.observability.otel import Observability

        config = AgentConfig.model_validate(
            {
                "name": "agent",
                "engine": {"systemInstruction": "t"},
                "llm": {"provider": "gemini", "model": "mock"},
                "observability": {"prometheus": {"enabled": True}},
            }
        )
        obs = Observability(config)
        counter = obs.counter("agentbase_llm_calls_total", "Root LLM invocations, by model")
        counter.add(1, {"model": "mock"})
        text = obs.registry.render()
        assert "# HELP agentbase_llm_calls_total Root LLM invocations, by model" in text
