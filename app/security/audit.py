"""Security: audit events, egress allowlist, trusted proxy, hardening
(REQUIREMENTS.md SEC-01, SEC-05, SEC-09, SEC-10, SEC-11).
"""

from __future__ import annotations

import ipaddress
import logging
import re
from typing import Any

audit_logger = logging.getLogger("agentbase.audit")

# SEC-10: audit-worthy events
AUDIT_EVENTS = {
    "auth_failure",
    "rate_limited",
    "foreign_session_access",
    "capability_rejected",
    "config_applied",
    "config_rejected",
    "auth_warn_none_bind",
    "idempotency_conflict",
}


def audit(event: str, **fields: Any) -> None:
    """SEC-10: one structured audit line per security-relevant event."""
    if event not in AUDIT_EVENTS:
        event = "audit_unknown"
    audit_logger.info("audit_event=%s %s", event, _kv(fields))


def _kv(fields: dict[str, Any]) -> str:
    return " ".join(f"{k}={_safe(v)}" for k, v in fields.items())


def _safe(value: Any) -> str:
    text = str(value)
    # SEC-11: log-injection guards on ids/claims/tool names.
    text = text.replace("\n", "\\n").replace("\r", "\\r")
    return text[:200]


# ---------------------------------------------------------------------------
# SEC-05 egress allowlist
# ---------------------------------------------------------------------------


def validate_egress_targets(config: Any) -> list[str]:
    """SEC-05: the runtime may initiate network requests only to provider
    base URLs, MCP URLs, JWKS URL, storage connection target, Kubernetes
    API, and the OTLP endpoint derived from trusted configuration. HTTPS
    verification is never disabled (CFG-14 rejects insecure options)."""
    problems: list[str] = []
    llm = config.llm
    if llm.baseUrl and not _http_scheme(llm.baseUrl):
        problems.append(f"llm.baseUrl must be http(s): {llm.baseUrl}")
    for server in config.tools.mcpServers:
        if server.url and not _http_scheme(server.url):
            problems.append(f"MCP {server.name} url must be http(s): {server.url}")
    if config.server.auth.mode.value == "jwt":
        jwks = config.server.auth.jwt.jwksUrl
        if jwks and not jwks.startswith("https://") and not _is_loopback(jwks):
            problems.append("JWKS url must be https (except loopback)")
    return problems


def _http_scheme(url: str) -> bool:
    return url.startswith("http://") or url.startswith("https://")


def _is_loopback(url: str) -> bool:
    match = re.match(r"https?://([^:/]+)", url)
    if not match:
        return False
    host = match.group(1)
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# SEC-09 trusted proxy
# ---------------------------------------------------------------------------


def parse_forwarded_for(
    header: str, trusted_proxy_cidrs: list[str], direct_peer: str
) -> str | None:
    """SEC-09: only trusted direct peers may supply forwarding headers;
    selects the rightmost untrusted hop. Returns the client IP or None."""
    if not trusted_proxy_cidrs or not header:
        return None
    if not _peer_trusted(direct_peer, trusted_proxy_cidrs):
        return None
    hops = [hop.strip() for hop in header.split(",") if hop.strip()]
    for hop in reversed(hops):
        if not _peer_trusted(hop, trusted_proxy_cidrs):
            return hop
    return hops[0] if hops else None


def _peer_trusted(peer: str, trusted_proxy_cidrs: list[str]) -> bool:
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return False
    for cidr in trusted_proxy_cidrs:
        try:
            if addr in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


# ---------------------------------------------------------------------------
# SEC-11 response hardening
# ---------------------------------------------------------------------------

HARDENING_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}


def hardening_headers() -> dict[str, str]:
    return dict(HARDENING_HEADERS)
