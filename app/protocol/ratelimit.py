"""Replica-local fixed-window rate limiting (REQUIREMENTS.md API-20).

Each replica keeps an in-process fixed UTC-minute window keyed by the
authenticated principal (or, with auth disabled, the direct peer IP — or the
first valid forwarded client IP when the direct peer is inside
``trustedProxyCidrs``). Health probes (``/healthz``, ``/readyz``) are never
rate-limited. Exceeding the window returns 429 ``rate_limited`` with
``Retry-After`` set to the remaining whole seconds until window reset.
"""

from __future__ import annotations

import math
import time
from typing import Any


class FixedWindowLimiter:
    """Fixed UTC-minute counter per key with lazy stale-bucket eviction."""

    def __init__(self, requests_per_minute: int) -> None:
        self._limit = requests_per_minute
        self._buckets: dict[str, tuple[int, int]] = {}

    def allow(self, key: str, now: float | None = None) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds).

        ``now`` is injected for deterministic tests. The window is the UTC
        minute containing ``now``; retry_after is whole seconds until the next
        minute boundary (API-20: remaining whole seconds to window reset).
        """
        current = math.floor(time.time() if now is None else now)
        bucket = current // 60
        retry_after = 60 - (current % 60)
        entry = self._buckets.get(key)
        if entry is None or entry[0] != bucket:
            self._buckets[key] = (bucket, 1)
            return True, 0
        if entry[1] >= self._limit:
            return False, retry_after
        self._buckets[key] = (bucket, entry[1] + 1)
        return True, 0

    def prune(self, now: float | None = None) -> None:
        """Drop buckets older than the current minute (bounded memory)."""
        current = math.floor(time.time() if now is None else now) // 60
        stale = [k for k, (b, _) in self._buckets.items() if b < current]
        for key in stale:
            del self._buckets[key]

    @classmethod
    def key_for_request(cls, request: Any, principal: str | None) -> str:
        """API-20 keying: authenticated principal, else peer IP (forwarded
        client IP only when the direct peer is in trustedProxyCidrs)."""
        if principal:
            return f"p:{principal}"
        client_ip = getattr(request.state, "client_ip", "") or (
            request.client.host if request.client else "unknown"
        )
        return f"ip:{client_ip}"

    @classmethod
    def build_if_enabled(cls, rate_limit_cfg: Any) -> FixedWindowLimiter | None:
        if getattr(rate_limit_cfg, "enabled", False):
            return cls(rate_limit_cfg.requestsPerMinute)
        return None
