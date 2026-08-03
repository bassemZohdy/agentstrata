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
            assert body["capabilities"]["phase"] == "P1"

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

            g2 = c.get(f"/v1/sessions/{sid}")
            assert g2.status_code == 404

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


class TestCors:
    def test_wildcard_origin_allowed(self):
        with _client() as c:
            r = c.get("/health", headers={"Origin": "https://example.com"})
            # with default corsOrigins ["*"] and no credentials, requests pass
            assert r.status_code == 200
