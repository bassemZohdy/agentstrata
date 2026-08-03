"""Security tests (SEC-01, SEC-05, SEC-09, SEC-10, SEC-11)."""

from __future__ import annotations

import logging

from app.security.audit import (
    audit,
    hardening_headers,
    parse_forwarded_for,
    validate_egress_targets,
)


class TestAudit:
    def test_audit_emits_structured_line(self, caplog):
        with caplog.at_level(logging.INFO, logger="agentbase.audit"):
            audit("auth_failure", code="auth_error", path="/v1/models")
        assert any("audit_event=auth_failure" in r.message for r in caplog.records)

    def test_audit_guards_log_injection(self, caplog):
        with caplog.at_level(logging.INFO, logger="agentbase.audit"):
            audit("auth_failure", request_id="abc\nINJECTED")
        assert not any("\nINJECTED" in r.message for r in caplog.records)


class TestEgress:
    def test_egress_validates_http_schemes(self):
        config = _config_with(mcp_url="ftp://bad")
        problems = validate_egress_targets(config)
        assert any("must be http(s)" in p for p in problems)

    def test_egress_allows_https(self):
        config = _config_with(mcp_url="https://mcp.example.com")
        assert validate_egress_targets(config) == []


class TestTrustedProxy:
    def test_untrusted_peer_ignored(self):
        assert parse_forwarded_for("1.2.3.4", ["10.0.0.0/8"], "5.6.7.8") is None

    def test_trusted_peer_selects_rightmost_untrusted(self):
        assert parse_forwarded_for("1.2.3.4, 10.0.0.5", ["10.0.0.0/8"], "10.0.0.5") == "1.2.3.4"

    def test_all_trusted_uses_first(self):
        assert parse_forwarded_for("10.0.0.1, 10.0.0.2", ["10.0.0.0/8"], "10.0.0.2") == "10.0.0.1"


class TestHardening:
    def test_nosniff_and_csp_present(self):
        headers = hardening_headers()
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert "default-src 'none'" in headers["Content-Security-Policy"]


def _config_with(**overrides):
    from app.config.models import AgentConfig

    doc = {
        "name": "agent",
        "engine": {"systemInstruction": "t"},
        "llm": {"provider": "gemini", "model": "m"},
        "tools": {"mcpServers": [{"name": "s", "transport": "sse", "url": "https://x"}]},
    }
    if "mcp_url" in overrides:
        doc["tools"]["mcpServers"][0]["url"] = overrides["mcp_url"]
    return AgentConfig.model_validate(doc)
