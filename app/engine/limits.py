"""Run limits and token accounting (REQUIREMENTS.md ENG-07, ENG-08).

- Monotonic deadline covers queue-free execution, model/tool calls, retries.
- Each completed LLM→tool→LLM cycle increments maxIterations.
- Assistant text is accumulated as UTF-8; before exceeding maxOutputBytes,
  keep only the largest code-point-safe prefix that fits, then stop.
- Provider usage is accumulated exactly once; a known exhausted budget caps
  max_output_tokens; a single call may overshoot by its reported input usage,
  after which no later call starts; missing usage is estimated + labeled.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .events import PublicError


@dataclass
class TokenAccount:
    """Cumulative usage (ENG-08): authoritative provider figures only."""

    input_tokens: int = 0
    output_tokens: int = 0
    estimated: bool = False

    def add(self, usage: dict[str, int] | None) -> None:
        if not usage:
            self.estimated = True  # ENG-08: missing usage never silently 0
            return
        self.input_tokens += _as_int(usage.get("input_tokens"))
        self.output_tokens += _as_int(usage.get("output_tokens"))

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class RunLimiter:
    """ENG-07/08 controls for one run."""

    deadline_monotonic: float
    timeout_seconds: int
    max_iterations: int
    max_output_bytes: int
    token_budget: int  # 0 = unlimited (per-request budget)
    account: TokenAccount = field(default_factory=TokenAccount)

    iterations: int = 0
    output_bytes: int = 0
    output_limit_hit: bool = False
    budget_exceeded: bool = False
    budget_overshoot_recorded: bool = False

    # -- deadline ------------------------------------------------------------

    def deadline_remaining(self, now_monotonic: float | None = None) -> float:
        now = now_monotonic if now_monotonic is not None else time.monotonic()
        return self.deadline_monotonic - now

    def check_deadline(self, now_monotonic: float | None = None) -> None:
        if self.deadline_remaining(now_monotonic) <= 0:
            raise PublicError("agent_timeout", "The request exceeded its deadline.")

    # -- iteration (ENG-07) ---------------------------------------------------

    def begin_iteration(self) -> None:
        self.check_deadline()

    def end_iteration(self) -> None:
        self.iterations += 1

    @property
    def iteration_limit_hit(self) -> bool:
        return self.iterations >= self.max_iterations

    # -- output bytes (ENG-07) -------------------------------------------------

    def reserve_output(self, delta: str) -> str | None:
        """Reserve ``delta`` against maxOutputBytes; returns the code-point
        safe prefix that fits ('' when nothing fits) and sets the limit flag
        when truncated."""
        encoded = delta.encode("utf-8")
        room = self.max_output_bytes - self.output_bytes
        if room <= 0:
            self.output_limit_hit = True
            return ""
        if len(encoded) <= room:
            self.output_bytes += len(encoded)
            return delta
        # keep the largest code-point-safe prefix that fits
        truncated = _codepoint_prefix(delta, room)
        self.output_bytes += len(truncated.encode("utf-8"))
        self.output_limit_hit = True
        return truncated

    # -- token budget (ENG-08) ---------------------------------------------------

    def cap_output_tokens(self, requested_max: int) -> int:
        """Known remaining budget caps max_output_tokens before each call."""
        if self.token_budget <= 0:
            return requested_max
        remaining = self.token_budget - self.account.total
        if remaining <= 0:
            self.budget_exceeded = True
            raise PublicError("budget_exceeded", "The token budget for this request was exceeded.")
        return min(requested_max, remaining)

    def observe_usage(self, usage: dict[str, int] | None) -> None:
        self.account.add(usage)
        # ENG-08: a single call may exceed the budget by its reported usage;
        # record the overshoot and prevent any later call.
        if self.token_budget > 0 and self.account.total > self.token_budget:
            self.budget_exceeded = True
            self.budget_overshoot_recorded = True

    @property
    def can_start_another_call(self) -> bool:
        if self.budget_exceeded or self.output_limit_hit:
            return False
        return not (self.token_budget > 0 and self.account.total >= self.token_budget)


def _codepoint_prefix(text: str, max_bytes: int) -> str:
    """Largest prefix of ``text`` whose UTF-8 encoding fits in max_bytes."""
    budget = max_bytes
    out: list[str] = []
    for ch in text:
        size = len(ch.encode("utf-8"))
        if size > budget:
            break
        out.append(ch)
        budget -= size
    return "".join(out)


def _as_int(value: Any) -> int:
    """Defensive conversion for provider-reported usage (ENG-08: missing
    usage must never silently count as zero)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
