"""Observability tests (OBS-01..06)."""

from __future__ import annotations

import json
import logging
import subprocess
import sys

from app.config.models import AgentConfig
from app.observability.logging import (
    JsonFormatter,
    normalize_request_id,
    validate_traceparent,
)
from app.observability.otel import Observability


def _config(otel_enabled: bool = False) -> AgentConfig:
    return AgentConfig.model_validate(
        {
            "name": "agent",
            "engine": {"systemInstruction": "t"},
            "llm": {"provider": "gemini", "model": "mock"},
            "observability": {"otel": {"enabled": otel_enabled}},
        }
    )


class TestJsonLogs:
    def test_json_formatter_fields(self):
        record = logging.LogRecord(
            name="agentstrata",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )
        record.request_id = "req-1"
        record.run_id = "run-1"
        line = JsonFormatter().format(record)
        try:
            payload = json.loads(line)
        except ValueError as exc:  # pragma: no cover — formatter output is ours
            raise AssertionError(f"invalid JSON log line: {line!r}") from exc
        assert payload["level"] == "INFO"
        assert payload["event"] == "hello"
        assert payload["msg"] == "hello"
        assert payload["request_id"] == "req-1"
        assert payload["run_id"] == "run-1"
        assert "ts" in payload and "logger" in payload


class TestRequestIds:
    def test_valid_request_id_accepted(self):
        assert normalize_request_id("abc.123:xyz-9_8") == "abc.123:xyz-9_8"

    def test_invalid_request_id_replaced(self):
        bad = "has spaces!"
        generated = normalize_request_id(bad)
        assert generated != bad
        assert len(generated) == 36  # uuid4

    def test_none_generates(self):
        assert len(normalize_request_id(None)) == 36

    def test_traceparent_validation(self):
        valid = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        assert validate_traceparent(valid) is True  # noqa: E712
        assert not validate_traceparent("bad")
        assert not validate_traceparent(None)


class TestZeroCostWhenDisabled:
    def test_otel_disabled_imports_nothing(self):
        """OBS-06: a subprocess with OTel disabled must not import
        opentelemetry (no spans/metrics allocated per request)."""
        code = (
            "import sys; "
            "from app.config.models import AgentConfig; "
            "from app.observability.otel import Observability; "
            "c = AgentConfig.model_validate({'name':'a','engine':{'systemInstruction':'t'},"
            "'llm':{'provider':'gemini','model':'m'},'observability':{'otel':{'enabled':False}}}); "
            "o = Observability(c); "
            "assert not o.enabled; "
            "print('otel-imported' if 'opentelemetry' in sys.modules else 'no-otel')"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=str(__import__("pathlib").Path(__file__).resolve().parents[2]),
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert "no-otel" in result.stdout

    def test_otel_enabled_creates_facade(self):
        obs = Observability(_config(otel_enabled=True))
        # without an OTLP endpoint reachable, export failure is nonfatal and
        # the facade degrades gracefully (OBS-04).
        assert obs.enabled or obs.export_failed
        obs.shutdown()
