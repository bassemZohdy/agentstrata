"""Security tests (SEC-01, SEC-05, SEC-09, SEC-10, SEC-11)."""

from __future__ import annotations

import logging
from typing import Any, cast

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


class TestJwtJwksRefresh:
    """SEC-08 (R-07): JWKS refresh on the refreshSeconds cadence with a
    stale-key cutoff and fail-closed unreachable handling.  The HTTP fetch
    is replaced by a controllable seam (``_refresh_jwks_locked``) and the
    clock by ``auth._monotonic`` so cadence logic is tested without
    network or asyncio-clock interference."""

    @staticmethod
    def _keys(kid: str):
        import json

        from cryptography.hazmat.primitives.asymmetric import rsa
        from jwt.algorithms import RSAAlgorithm

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
        jwk["kid"] = kid
        jwk["alg"] = "RS256"
        return key, jwk

    @staticmethod
    def _signed(key, kid: str, issuer: str = "iss", audience: str = "aud") -> str:
        import jwt

        return jwt.encode(
            {"sub": "u1", "iss": issuer, "aud": audience},
            key,
            algorithm="RS256",
            headers={"kid": kid},
        )

    @staticmethod
    def _auth(monkeypatch, *, refresh_seconds: int = 60, jwks: dict | None = None):
        import app.protocol.auth as auth_mod
        from app.protocol.auth import _JwtAuth

        auth = _JwtAuth(
            issuer="iss",
            audience="aud",
            jwks_url="https://idp.example/jwks",
            principal_claim="sub",
            refresh_seconds=refresh_seconds,
            timeout_seconds=5,
        )
        state: dict[str, Any] = {"jwks": jwks if jwks is not None else {}}

        async def _fetch():
            # Mirrors the real method's contract: a failure (IdP down or
            # empty) leaves the old keys in place and returns False.
            served = state["jwks"]
            if served:
                auth._jwks = served
                auth._jwks_fetched_at = auth_mod._monotonic()
                return True
            return False

        monkeypatch.setattr(auth, "_refresh_jwks_locked", _fetch)
        return auth, state

    @staticmethod
    def _request(token: str):
        from types import SimpleNamespace

        return cast(Any, SimpleNamespace(headers={"authorization": f"Bearer {token}"}))

    async def test_cadence_rotation_without_failed_verification(self, monkeypatch):
        """SEC-08: a key removed from the JWKS stops verifying after the
        refresh interval — no failed verification needed first."""
        import app.protocol.auth as auth_mod

        clock = {"now": 1_000.0}
        monkeypatch.setattr(auth_mod, "_monotonic", lambda: clock["now"])
        key_a, jwk_a = self._keys("kid-a")
        auth, state = self._auth(monkeypatch, jwks={"kid-a": jwk_a})

        token_a = self._signed(key_a, "kid-a")
        ok, err = await auth.authenticate(self._request(token_a))
        assert err is None and ok == "jwt:" + _jwt_principal_digest("u1")

        # Rotate: the IdP drops kid-a.  Advance past the cadence.
        key_b, jwk_b = self._keys("kid-b")
        state["jwks"] = {"kid-b": jwk_b}
        clock["now"] += 61  # > refresh_seconds(60)

        # kid-a token now fails — rotation happened on the cadence, before
        # any failed verification.
        bad, err = await auth.authenticate(self._request(token_a))
        assert err is not None and err.code == "auth_error"
        assert bad == ""
        # The rotated key works.
        ok2, err2 = await auth.authenticate(self._request(self._signed(key_b, "kid-b")))
        assert err2 is None and ok2 == "jwt:" + _jwt_principal_digest("u1")

    async def test_refresh_failure_keeps_old_keys_within_cutoff(self, monkeypatch):
        import app.protocol.auth as auth_mod

        clock = {"now": 1_000.0}
        monkeypatch.setattr(auth_mod, "_monotonic", lambda: clock["now"])
        key_a, jwk_a = self._keys("kid-a")
        auth, state = self._auth(monkeypatch, jwks={"kid-a": jwk_a})
        token_a = self._signed(key_a, "kid-a")
        assert (await auth.authenticate(self._request(token_a)))[1] is None

        # IdP down at the cadence: old keys stay trusted inside the cutoff.
        state["jwks"] = None  # fetch raises
        clock["now"] += 61
        ok, err = await auth.authenticate(self._request(token_a))
        assert err is None and ok  # still verified with the stale keys

    async def test_stale_key_cutoff_fails_closed(self, monkeypatch):
        import app.protocol.auth as auth_mod

        clock = {"now": 1_000.0}
        monkeypatch.setattr(auth_mod, "_monotonic", lambda: clock["now"])
        key_a, jwk_a = self._keys("kid-a")
        auth, state = self._auth(monkeypatch, jwks={"kid-a": jwk_a}, refresh_seconds=10)
        token_a = self._signed(key_a, "kid-a")
        assert (await auth.authenticate(self._request(token_a)))[1] is None

        # IdP down past 3x the interval: fail closed (503), no ancient keys.
        state["jwks"] = None
        clock["now"] += 31  # > 3 * refresh_seconds(10)
        bad, err = await auth.authenticate(self._request(token_a))
        assert bad == ""
        assert err is not None and err.code == "auth_unavailable"
        assert err.status == 503

    async def test_refresh_attempt_gated_to_once_per_interval(self, monkeypatch):
        import app.protocol.auth as auth_mod

        clock = {"now": 1_000.0}
        monkeypatch.setattr(auth_mod, "_monotonic", lambda: clock["now"])
        key_a, jwk_a = self._keys("kid-a")
        auth, state = self._auth(monkeypatch, jwks={"kid-a": jwk_a})
        token_a = self._signed(key_a, "kid-a")
        assert (await auth.authenticate(self._request(token_a)))[1] is None
        attempts = {"n": 0}
        original = auth._refresh_jwks_locked

        async def _counting():
            attempts["n"] += 1
            return await original()

        monkeypatch.setattr(auth, "_refresh_jwks_locked", _counting)
        state["jwks"] = None
        clock["now"] += 61  # due
        await auth.authenticate(self._request(token_a))
        await auth.authenticate(self._request(token_a))
        await auth.authenticate(self._request(token_a))
        # Due but gated: only the first request attempts a refresh.
        assert attempts["n"] == 1

    async def test_initial_unreachable_fails_closed(self, monkeypatch):
        import app.protocol.auth as auth_mod

        clock = {"now": 1_000.0}
        monkeypatch.setattr(auth_mod, "_monotonic", lambda: clock["now"])
        auth, state = self._auth(monkeypatch, jwks=None)  # fetch raises
        key_a, jwk_a = self._keys("kid-a")
        bad, err = await auth.authenticate(self._request(self._signed(key_a, "kid-a")))
        assert bad == ""
        assert err is not None and err.code == "auth_unavailable"
        assert err.status == 503


def _jwt_principal_digest(claim: str) -> str:
    import hashlib

    return hashlib.sha256("\0".join(["iss", "sub", claim]).encode("utf-8")).hexdigest()


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


class TestJwtStaleCutoffContinuous:
    """R-27: the stale-key cutoff fails closed on EVERY request past the
    bound — not just at the instants a refresh is attempted (the
    once-per-interval gate throttles only the fetch attempt)."""

    async def test_cutoff_fails_closed_between_attempt_boundaries(self, monkeypatch):
        import app.protocol.auth as auth_mod

        clock = {"now": 1_000.0}
        monkeypatch.setattr(auth_mod, "_monotonic", lambda: clock["now"])
        key_a, jwk_a = TestJwtJwksRefresh._keys("kid-a")
        auth, state = TestJwtJwksRefresh._auth(
            monkeypatch, jwks={"kid-a": jwk_a}, refresh_seconds=10
        )
        token_a = TestJwtJwksRefresh._signed(key_a, "kid-a")
        assert (await auth.authenticate(TestJwtJwksRefresh._request(token_a)))[1] is None

        # IdP down; advance just past the 3x cutoff (30 s).
        state["jwks"] = None
        clock["now"] += 30.5

        # Every probe past the cutoff fails closed — at the boundary...
        bad, err = await auth.authenticate(TestJwtJwksRefresh._request(token_a))
        assert bad == "" and err is not None and err.code == "auth_unavailable"
        # ...immediately after an attempt (inside the gate's window, where
        # the old code skipped the cutoff test entirely)...
        clock["now"] += 0.5
        bad2, err2 = await auth.authenticate(TestJwtJwksRefresh._request(token_a))
        assert bad2 == "" and err2 is not None and err2.code == "auth_unavailable"
        # ...and far past it.
        clock["now"] += 50
        bad3, err3 = await auth.authenticate(TestJwtJwksRefresh._request(token_a))
        assert bad3 == "" and err3 is not None and err3.code == "auth_unavailable"
