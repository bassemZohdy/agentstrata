"""Context bounds and pruning (REQUIREMENTS.md ENG-04).

The configured system instruction is always first and is never pruned. For a
stateful run, before each LLM call a candidate context is formed by removing
the oldest complete persisted turns until both historyMaxMessages and
historyMaxBytes are satisfied after including the pending user message; the
removals are not committed until the successful ENG-06 transaction. The
pending user message is never pruned; if it cannot fit, fail with
context_length_exceeded.
"""

from __future__ import annotations

from .events import PublicError


def _msg_bytes(message: dict) -> int:
    return len(message.get("content", "").encode("utf-8"))


def prune_history(
    persisted: list[dict],
    pending_user: dict,
    history_max_messages: int,
    history_max_bytes: int,
) -> list[dict]:
    """ENG-04: remove oldest complete turns until both bounds fit with the
    pending user message included. Returns the pruned persisted history
    (the removals are committed only by the ENG-06 transaction)."""
    # the pending user message is never pruned
    pending_bytes = _msg_bytes(pending_user)
    if pending_bytes > history_max_bytes:
        raise PublicError(
            "context_length_exceeded",
            "The pending message exceeds the history byte limit.",
        )
    kept = list(persisted)
    total_bytes = sum(_msg_bytes(m) for m in kept) + pending_bytes
    while kept and (len(kept) + 1 > history_max_messages or total_bytes > history_max_bytes):
        removed = kept.pop(0)
        total_bytes -= _msg_bytes(removed)
    return kept


def trim_to_context_window(
    history: list[dict],
    pending_user: dict,
    context_window_tokens: int,
    reserved_output_tokens: int,
    estimate_tokens,
) -> list[dict]:
    """ENG-04: when llm.contextWindowTokens > 0, additionally drop oldest
    complete turns until estimated input + reserved output fits."""
    if context_window_tokens <= 0:
        return history
    reserved = max(reserved_output_tokens, 1)
    if reserved >= context_window_tokens:
        raise PublicError(
            "context_length_exceeded",
            "The reserved output exceeds the context window.",
        )
    budget = context_window_tokens - reserved
    pending_est = estimate_tokens(pending_user)
    if pending_est > budget:
        raise PublicError(
            "context_length_exceeded",
            "The pending message does not fit the context window.",
        )
    kept = list(history)
    total = sum(estimate_tokens(m) for m in kept) + pending_est
    while kept and total > budget:
        removed = kept.pop(0)
        total -= estimate_tokens(removed)
    return kept
