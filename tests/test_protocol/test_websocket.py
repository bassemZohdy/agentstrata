"""WebSocket API (REQUIREMENTS.md WS-01).

The bidirectional surface: run.start/run.cancel/approval.decide/ping over
one connection with engine events pushed back. Auth reuses the REST
provider (token via ``?token=`` for browser clients); one active run per
connection; oversize inbound messages close with 1009.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.observability.otel import Observability
from app.protocol.app import create_app

from .conftest import build_components, make_config


def _app(server: dict | None = None, observability: dict | None = None):
    config = make_config(server=server, observability=observability)
    obs = Observability(config)
    components = build_components(config, obs)
    app = create_app(config, components, mode="standalone")
    return TestClient(app), config, components


def _ws(server: dict | None = None):
    client, _config, _components = _app(server=server or {"protocols": {"websocket": True}})
    return client


class TestWebSocket:
    def test_run_round_trip(self):
        with _ws().websocket_connect("/v1/ws") as ws:
            ws.send_json({"type": "run.start", "message": "hello"})
            started = ws.receive_json()
            assert started["type"] == "run.started"
            kinds = []
            while True:
                msg = ws.receive_json()
                kinds.append(msg["type"])
                if msg["type"] == "run.done":
                    assert msg["finishReason"] == "stop"
                    break
            assert "run.delta" in kinds

    def test_ping_pong(self):
        with _ws().websocket_connect("/v1/ws") as ws:
            ws.send_json({"type": "ping", "ts": 123})
            msg = ws.receive_json()
            assert msg == {"type": "pong", "ts": 123}

    def test_cancel_with_no_active_run(self):
        with _ws().websocket_connect("/v1/ws") as ws:
            ws.send_json({"type": "run.cancel"})
            msg = ws.receive_json()
            assert msg["type"] == "error" and msg["code"] == "no_active_run"

    def test_sequential_runs(self):
        """One connection can run several runs one after another."""
        with _ws().websocket_connect("/v1/ws") as ws:
            for n in range(2):
                ws.send_json({"type": "run.start", "message": f"run {n}"})
                assert ws.receive_json()["type"] == "run.started"
                while True:
                    if ws.receive_json()["type"] == "run.done":
                        break
            # still usable afterwards
            ws.send_json({"type": "ping"})
            assert ws.receive_json()["type"] == "pong"

    def test_approval_decide_unknown(self):
        with _ws().websocket_connect("/v1/ws") as ws:
            ws.send_json({"type": "approval.decide", "approvalId": "nope", "decision": "approve"})
            msg = ws.receive_json()
            assert msg["type"] == "error" and msg["code"] == "approval_not_found"

    def test_invalid_message(self):
        with _ws().websocket_connect("/v1/ws") as ws:
            ws.send_json({"type": "bogus"})
            msg = ws.receive_json()
            assert msg["type"] == "error" and msg["code"] == "invalid_message"

    def test_oversize_message_closes(self):
        with pytest.raises(WebSocketDisconnect) as exc, _ws().websocket_connect("/v1/ws") as ws:
            ws.send_text(json.dumps({"type": "run.start", "message": "x" * 1_000_000}))
            ws.receive_json()
        assert exc.value.code == 1009

    def test_oversize_multibyte_message_closes(self):
        """R-13: the cap counts UTF-8 BYTES — a payload under the code-point
        limit but over the byte cap (multi-byte characters) still closes
        with 1009."""
        with (
            pytest.raises(WebSocketDisconnect) as exc,
            _ws({"protocols": {"websocket": True}, "maxMessageBytes": 1024}).websocket_connect(
                "/v1/ws"
            ) as ws,
        ):
            # 400 emoji ≈ 1600 UTF-8 bytes but only ~400 code points.
            ws.send_text(json.dumps({"type": "ping", "emoji": "😀" * 400}, ensure_ascii=False))
            ws.receive_json()
        assert exc.value.code == 1009

    def test_run_start_rate_limited(self):
        """R-13: run.start is rate-limited on the connection (the HTTP
        middleware never sees WS frames) — a third start in the window is
        denied with a rate_limited error."""
        client, _config, _components = _app(
            server={
                "protocols": {"websocket": True},
                "rateLimit": {"enabled": True, "requestsPerMinute": 2},
            }
        )
        with client.websocket_connect("/v1/ws") as ws:
            for _ in range(2):
                ws.send_json({"type": "run.start", "message": "hello"})
                assert ws.receive_json()["type"] == "run.started"
                while ws.receive_json().get("type") != "run.done":
                    pass
            ws.send_json({"type": "run.start", "message": "hello"})
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert msg["code"] == "rate_limited"

    def test_auth_required(self, monkeypatch):
        monkeypatch.setenv("AGENT_WS_TEST_KEY", "secret")
        client, _config, _components = _app(
            server={
                "protocols": {"websocket": True},
                "auth": {"mode": "apiKey", "apiKeyEnv": "AGENT_WS_TEST_KEY"},
            }
        )
        # without a token the server accepts then closes with 1008
        with pytest.raises(WebSocketDisconnect) as exc, client.websocket_connect("/v1/ws") as ws:
            ws.receive_json()
        assert exc.value.code == 1008
        # with the token the round trip works
        with client.websocket_connect("/v1/ws?token=secret") as ws:
            ws.send_json({"type": "ping"})
            assert ws.receive_json()["type"] == "pong"

    def test_ws_disabled_by_default(self):
        client, _config, _components = _app()
        with pytest.raises(WebSocketDisconnect), client.websocket_connect("/v1/ws") as ws:
            ws.receive_json()
