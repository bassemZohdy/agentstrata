"""API golden-fixture tests (API-00..20, SEC-01..11).

Uses the FastAPI TestClient against mock engine components — no live LLM or
network (ACC-01). Verifies health/readyz/healthz, /config redaction,
OpenAI-compatible chat (non-streaming + SSE), /v1/models, session endpoints
with identical 404s, apiKey auth, and CORS.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.protocol.app import create_app

from .conftest import build_components, make_config


def _client(**server_overrides) -> TestClient:
    config = make_config(server=server_overrides)
    components = build_components(config)
    app = create_app(config, components, mode="standalone")
    return TestClient(app)


class TestHealth:
    def test_healthz_live(self):
        with _client() as c:
            r = c.get("/healthz")
            assert r.status_code == 200
            assert r.json() == {"status": "ok"}

    def test_readyz_ok(self):
        with _client() as c:
            r = c.get("/readyz")
            assert r.status_code == 200
            assert r.json()["status"] == "ready"

    def test_health_reports_components(self):
        with _client() as c:
            r = c.get("/health")
            body = r.json()
            assert body["status"] in ("ok", "degraded")
            assert "storage" in body["components"]
            assert "mcp" in body["components"]
            assert body["mode"] == "standalone"
            assert body["capabilities"]["phase"] == "P5"
            # COST-01 is build-included after P5-4 (not phase-gated), so it
            # reports true whenever the acceptance suite is present.
            assert body["capabilities"]["costs"] is True

    def test_config_redacts_and_masks_system_instruction(self):
        with _client() as c:
            r = c.get("/config")
            body = r.json()
            # systemInstruction excluded by default (exposeSystemInstruction=false)
            assert "systemInstruction" not in body["engine"]
            assert body["name"] == "agent"

    def test_config_has_cache_no_store(self):
        with _client() as c:
            r = c.get("/config")
            assert r.headers.get("cache-control") == "no-store"

    def test_healthz_exempt_from_auth(self, monkeypatch):
        monkeypatch.setenv("API_KEY", "sekret")
        # auth middleware skips /healthz and /readyz (API-00)
        with _client(auth={"mode": "apiKey", "apiKeyEnv": "API_KEY"}) as c:
            assert c.get("/healthz").status_code == 200
            assert c.get("/readyz").status_code == 200
            assert c.get("/v1/models").status_code == 401

    def test_request_id_present(self):
        with _client() as c:
            r = c.get("/health")
            assert "x-request-id" in r.headers


class TestChat:
    def test_non_streaming(self):
        with _client() as c:
            r = c.post(
                "/v1/chat/completions",
                json={"model": "mock", "messages": [{"role": "user", "content": "hi"}]},
            )
            assert r.status_code == 200
            body = r.json()
            assert body["object"] == "chat.completion"
            assert body["choices"][0]["message"]["role"] == "assistant"
            assert "hello from mock" in body["choices"][0]["message"]["content"]
            assert body["choices"][0]["finish_reason"] == "stop"
            assert "usage" in body

    def test_streaming_sse(self):
        with _client() as c:
            r = c.post(
                "/v1/chat/completions",
                json={
                    "model": "mock",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                },
            )
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/event-stream")
            body = r.text
            assert "data: [DONE]" in body
            assert "chat.completion.chunk" in body

    def test_unsupported_field_400(self):
        with _client() as c:
            r = c.post(
                "/v1/chat/completions",
                json={"model": "mock", "messages": [{"role": "user", "content": "hi"}], "bogus": 1},
            )
            assert r.status_code == 400
            assert r.json()["error"]["code"] == "invalid_request"

    def test_oversized_body_413(self):
        """R-09: an oversized POST is rejected with 413 (API-20)."""
        import json as _json

        with _client(maxRequestBytes=1024) as c:
            big = _json.dumps(
                {
                    "model": "mock",
                    "messages": [{"role": "user", "content": "x" * 5000}],
                }
            )
            r = c.post(
                "/v1/chat/completions",
                content=big,
                headers={"content-type": "application/json"},
            )
            assert r.status_code == 413
            assert r.json()["error"]["code"] == "invalid_request"

    async def test_read_body_aborts_at_cap_before_buffering_completes(self):
        """R-09: the body cap is enforced WHILE streaming — the receive
        channel is not drained past the limit (an oversized POST is not
        absorbed in full before the 413)."""
        import pytest
        from fastapi import Request

        from app.protocol.errors import PublicErrorResponse
        from app.protocol.routes.chat import _read_body

        class _FakeReceive:
            def __init__(self, chunks: list[bytes]) -> None:
                self._chunks = list(chunks)
                self.consumed = 0

            async def __call__(self):
                if not self._chunks:
                    return {"type": "http.request", "body": b"", "more_body": False}
                self.consumed += 1
                more = len(self._chunks) > 1
                return {
                    "type": "http.request",
                    "body": self._chunks.pop(0),
                    "more_body": more,
                }

        config = make_config(server={"maxRequestBytes": 1024})
        receive = _FakeReceive([b"x" * 100] * 20)  # 2000 bytes in 100-byte chunks
        request = Request(
            {"type": "http", "method": "POST", "path": "/", "headers": []},
            receive,
        )
        with pytest.raises(PublicErrorResponse) as excinfo:
            await _read_body(request, config)
        assert excinfo.value.status == 413
        # Aborted mid-stream: the 20th chunk was never requested.
        assert receive.consumed < 20

    def test_missing_messages_400(self):
        with _client() as c:
            r = c.post("/v1/chat/completions", json={"model": "mock"})
            assert r.status_code == 400

    def test_idempotency_replay(self):
        with _client() as c:
            payload = {
                "model": "mock",
                "messages": [{"role": "user", "content": "hi"}],
                "idempotency_key": "k1",
            }
            r1 = c.post("/v1/chat/completions", json=payload)
            r2 = c.post("/v1/chat/completions", json=payload)
            assert r1.status_code == 200 and r2.status_code == 200
            assert r2.json().get("replayed") is True

    def test_models_endpoint(self):
        with _client() as c:
            r = c.get("/v1/models")
            assert r.status_code == 200
            assert r.json()["data"][0]["id"] == "mock"


class TestSessions:
    def test_create_get_delete(self):
        with _client() as c:
            r = c.post("/v1/sessions", json={})
            assert r.status_code == 200
            sid = r.json()["session_id"]
            assert sid

            g = c.get(f"/v1/sessions/{sid}")
            assert g.status_code == 200

            d = c.delete(f"/v1/sessions/{sid}")
            assert d.status_code == 204
            # R-18: RFC 9110 — a 204 has an empty body and no content-type.
            assert d.content == b""
            assert d.headers.get("content-type") is None

            g2 = c.get(f"/v1/sessions/{sid}")
            assert g2.status_code == 404

    def test_delete_invalid_session_id_400(self):
        with _client() as c:
            r = c.delete("/v1/sessions/bad id!")
            assert r.status_code == 400
            assert r.json()["error"]["code"] == "invalid_session_id"

    def test_get_invalid_session_id_400(self):
        with _client() as c:
            r = c.get("/v1/sessions/bad id!")
            assert r.status_code == 400
            assert r.json()["error"]["code"] == "invalid_session_id"

    def test_capacity_error_maps_to_storage_capacity(self):
        """R-19: a maxSessions CapacityError surfaces as 503
        storage_capacity, not a generic 5xx outage."""
        from app.protocol.app import create_app
        from app.storage.contract import CapacityError

        class _FullBackend:
            async def create_session(self, **kwargs):
                raise CapacityError("maxSessions reached")

            async def get_session(self, **kwargs):
                return None

            async def delete_session(self, **kwargs):
                return False

        config = make_config()
        components = {"backend": _FullBackend(), "mcp": None}
        with TestClient(create_app(config, components, mode="standalone")) as c:
            r = c.post("/v1/sessions", json={})
            assert r.status_code == 503
            assert r.json()["error"]["code"] == "storage_capacity"

    def test_session_busy_maps_to_409(self):
        """R-19: deleting a session with a nonterminal run surfaces 409
        session_busy instead of a generic error."""
        from app.protocol.app import create_app
        from app.storage.contract import SessionBusy

        class _BusyBackend:
            async def create_session(self, **kwargs):
                raise SessionBusy("busy")

            async def get_session(self, **kwargs):
                return None

            async def delete_session(self, **kwargs):
                raise SessionBusy("busy")

        config = make_config()
        components = {"backend": _BusyBackend(), "mcp": None}
        with TestClient(create_app(config, components, mode="standalone")) as c:
            d = c.delete("/v1/sessions/sid")
            assert d.status_code == 409
            assert d.json()["error"]["code"] == "session_busy"

    def test_unknown_session_identical_404(self):
        with _client() as c:
            r = c.get("/v1/sessions/nonexistent")
            assert r.status_code == 404
            assert r.json()["error"]["code"] == "session_not_found"

    def test_invalid_session_id_400(self):
        with _client() as c:
            r = c.post("/v1/sessions", json={"session_id": "bad id!"})
            assert r.status_code == 400


class TestAuth:
    def test_api_key_required(self, monkeypatch):
        monkeypatch.setenv("API_KEY", "sekret")
        with _client(auth={"mode": "apiKey", "apiKeyEnv": "API_KEY"}) as c:
            r = c.get("/v1/models")
            assert r.status_code == 401
            r_ok = c.get("/v1/models", headers={"Authorization": "Bearer sekret"})
            assert r_ok.status_code == 200
            r_ok2 = c.get("/v1/models", headers={"X-API-Key": "sekret"})
            assert r_ok2.status_code == 200

    def test_api_key_wrong_credentials(self, monkeypatch):
        monkeypatch.setenv("API_KEY", "sekret")
        with _client(auth={"mode": "apiKey", "apiKeyEnv": "API_KEY"}) as c:
            r = c.get("/v1/models", headers={"Authorization": "Bearer wrong"})
            assert r.status_code == 401
            r2 = c.get("/v1/models", headers={"X-API-Key": "wrong"})
            assert r2.status_code == 401

    def test_api_key_conflicting_headers(self, monkeypatch):
        monkeypatch.setenv("API_KEY", "sekret")
        with _client(auth={"mode": "apiKey", "apiKeyEnv": "API_KEY"}) as c:
            r = c.get(
                "/v1/models",
                headers={"Authorization": "Bearer a", "X-API-Key": "b"},
            )
            assert r.status_code == 401

    def test_none_auth_anonymous(self):
        with _client() as c:
            r = c.get("/v1/models")
            assert r.status_code == 200


class TestMiddlewareOrder:
    """R-01: request-id + hardening middleware wrap auth and rate-limit,
    so failure responses still carry X-Request-Id, every hardening header,
    and a non-empty body request_id — and the auth_failure audit records
    the real request id instead of "" ."""

    def test_auth_failure_carries_request_id_and_hardening(self, monkeypatch, caplog):
        import logging

        from app.security.audit import HARDENING_HEADERS

        monkeypatch.setenv("API_KEY", "sekret")
        with _client(auth={"mode": "apiKey", "apiKeyEnv": "API_KEY"}) as c:
            with caplog.at_level(logging.INFO, logger="agentbase.audit"):
                r = c.get("/v1/models", headers={"Authorization": "Bearer wrong"})
            assert r.status_code == 401
            assert r.headers.get("x-request-id")
            for key in HARDENING_HEADERS:
                assert key in r.headers
            assert r.json().get("request_id")
            # SEC-10: the audit record carries the same request id.
            records = [
                x for x in caplog.records if x.message.startswith("audit_event=auth_failure")
            ]
            assert records
            assert "request_id=" + r.headers["x-request-id"] in records[0].message

    def test_rate_limited_carries_request_id_and_hardening(self):
        from app.security.audit import HARDENING_HEADERS

        with _client(rateLimit={"enabled": True, "requestsPerMinute": 1}) as c:
            assert c.get("/v1/models").status_code == 200
            r = c.get("/v1/models")
            assert r.status_code == 429
            assert r.headers.get("x-request-id")
            for key in HARDENING_HEADERS:
                assert key in r.headers
            assert r.json().get("request_id")


class TestCors:
    def test_wildcard_origin_allowed(self):
        with _client() as c:
            r = c.get("/health", headers={"Origin": "https://example.com"})
            # with default corsOrigins ["*"] and no credentials, requests pass
            assert r.status_code == 200
