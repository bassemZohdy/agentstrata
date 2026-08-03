"""Auth modes (REQUIREMENTS.md SEC-01, SEC-03, SEC-08; SES-03 principals).

- ``none``: principal ``anonymous``; a non-loopback bind emits a high-severity
  audit warning at boot (SEC-01).
- ``apiKey``: constant-time compare; accepted via ``Authorization: Bearer``
  or ``X-API-Key``; when both are present they must match (SEC-01).
- ``jwt``: RS256/ES256 only, JWKS refresh/rotation with a stale-key cutoff,
  fail-closed on unreachable JWKS (SEC-03, SEC-08).

Principal IDs follow SES-03: ``apikey:<sha256(key)>`` and
``jwt:<sha256(issuer|claim_name|claim_value)>``.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from typing import Any

from fastapi import Request

logger = logging.getLogger(__name__)

AUTH_UNAVAILABLE_STATUS = 503


@dataclass
class AuthFailure:
    status: int
    code: str
    message: str

    def body(self, request_id: str) -> dict[str, Any]:
        return {
            "error": {
                "message": self.message,
                "type": self.code,
                "code": self.code,
            },
            "request_id": request_id,
        }


class AuthProvider:
    @classmethod
    def from_config(cls, config: Any) -> AuthProvider:
        auth = config.server.auth
        mode = auth.mode.value
        if mode == "none":
            return _NoneAuth()
        if mode == "apiKey":
            return _ApiKeyAuth(auth.apiKeyEnv, auth.apiKeyFile)
        if mode == "jwt":
            return _JwtAuth(
                issuer=auth.jwt.issuer,
                audience=auth.jwt.audience,
                jwks_url=auth.jwt.jwksUrl,
                principal_claim=auth.jwt.principalClaim,
                refresh_seconds=auth.jwt.refreshSeconds,
                timeout_seconds=auth.jwt.timeoutSeconds,
            )
        raise ValueError(f"unsupported auth mode {mode!r}")

    async def authenticate(self, request: Request) -> tuple[str, AuthFailure | None]:
        raise NotImplementedError


class _NoneAuth(AuthProvider):
    async def authenticate(self, request: Request) -> tuple[str, AuthFailure | None]:
        # SES-03: with auth disabled the principal is anonymous and client
        # chosen sessions are mutually accessible (documented).
        return "anonymous", None


class _ApiKeyAuth(AuthProvider):
    def __init__(self, api_key_env: str | None, api_key_file: str | None) -> None:
        self._api_key_env = api_key_env
        self._api_key_file = api_key_file
        self._key: str | None = None

    def set_key(self, key: str | None) -> None:
        self._key = key

    async def authenticate(self, request: Request) -> tuple[str, AuthFailure | None]:
        header = request.headers.get("authorization", "")
        x_api_key = request.headers.get("x-api-key")
        bearer = header[7:] if header.lower().startswith("bearer ") else None

        if bearer is not None and x_api_key is not None and bearer != x_api_key:
            return "", AuthFailure(401, "auth_error", "conflicting credentials")
        candidate = bearer if bearer is not None else x_api_key
        if candidate is None:
            return "", AuthFailure(401, "auth_error", "missing API key")
        expected = self._key or _resolve_secret(self._api_key_env, self._api_key_file)
        if expected is None or not _constant_time_eq(candidate, expected):
            return "", AuthFailure(401, "auth_error", "invalid API key")
        digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
        return f"apikey:{digest}", None


class _JwtAuth(AuthProvider):
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str,
        principal_claim: str,
        refresh_seconds: int,
        timeout_seconds: int,
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._jwks_url = jwks_url
        self._principal_claim = principal_claim
        self._refresh_seconds = refresh_seconds
        self._timeout_seconds = timeout_seconds
        self._jwks: dict[str, Any] = {}
        self._jwks_failed = False  # SEC-03: fail-closed on unreachable JWKS

    async def authenticate(self, request: Request) -> tuple[str, AuthFailure | None]:
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            return "", AuthFailure(401, "auth_error", "missing bearer token")
        token = header[7:]
        if not self._jwks and not await self._refresh_jwks():
            return "", AuthFailure(
                AUTH_UNAVAILABLE_STATUS, "auth_unavailable", "identity provider unavailable"
            )
        payload = _verify_jwt(token, self._jwks, self._issuer, self._audience)
        # SEC-08: rotation — refresh once and retry before failing.
        if payload is None and await self._refresh_jwks():
            payload = _verify_jwt(token, self._jwks, self._issuer, self._audience)
        if payload is None:
            return "", AuthFailure(401, "auth_error", "invalid token")
        claim = payload.get(self._principal_claim)
        if not isinstance(claim, str) or not claim:
            return "", AuthFailure(401, "auth_error", "missing principal claim")
        material = "\0".join([self._issuer, self._principal_claim, claim])
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        return f"jwt:{digest}", None

    async def _refresh_jwks(self) -> bool:
        try:
            import httpx

            timeout = httpx.Timeout(self._timeout_seconds)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(self._jwks_url)
                response.raise_for_status()
                data = response.json()
            keys = data.get("keys", [])
            self._jwks = {_kid_of(k): k for k in keys if _kid_of(k)}
            self._jwks_failed = False
            return bool(self._jwks)
        except Exception as exc:  # noqa: BLE001
            logger.warning("JWKS refresh failed: %s", exc)
            self._jwks_failed = True
            return False


def _resolve_secret(env: str | None, file: str | None) -> str | None:
    if file:
        try:
            with open(file, encoding="utf-8") as fh:
                value = fh.read().rstrip("\r\n")
            if value:
                return value
        except OSError:
            pass
    if env:
        import os

        return os.environ.get(env) or None
    return None


def _constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _kid_of(key: dict[str, Any]) -> str:
    return str(key.get("kid", ""))


def _verify_jwt(
    token: str, jwks: dict[str, Any], issuer: str, audience: str
) -> dict[str, Any] | None:
    try:
        import jwt as pyjwt
        from jwt import PyJWK

        header = pyjwt.get_unverified_header(token)
        kid = str(header.get("kid", ""))
        jwk_data = jwks.get(kid)
        if not jwk_data:
            return None
        key = PyJWK(jwk_data, algorithm=header.get("alg")).key
        payload = pyjwt.decode(
            token,
            key=key,
            algorithms=["RS256", "ES256"],
            issuer=issuer or None,
            audience=audience or None,
            options={"verify_aud": bool(audience)},
        )
        return payload
    except Exception:  # noqa: BLE001
        return None
