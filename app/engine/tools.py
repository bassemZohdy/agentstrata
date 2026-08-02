"""Tool side-effect ledger (REQUIREMENTS.md ENG-09).

Each ADK tool-call ID is executed at most once within a run. For a stateful
run, a call record is persisted as ``executing`` before invocation and its
bounded result as ``completed``/``failed`` after return; a durable ID left
``executing`` across lost ownership becomes ``outcome_unknown`` and must not
be invoked again. Repeated delivery of a completed ID returns the stored
result. The runtime never automatically retries a tool.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolRecord:
    call_id: str
    name: str
    state: str = "executing"  # executing | completed | failed | outcome_unknown
    result: Any = None
    error: str | None = None
    started_at: float = field(default_factory=time.monotonic)


class ToolLedger:
    """ENG-09 dedup + side-effect records for one run.

    ``persist`` is an optional async callback ``(call_id, record) -> None``
    used by the stateful runner to store records incrementally; stateless
    runs keep everything in process memory (API-06 forbids durable data).
    """

    def __init__(self, persist=None) -> None:
        self._records: dict[str, ToolRecord] = {}
        self._persist = persist

    async def begin(self, call_id: str, name: str) -> ToolRecord:
        existing = self._records.get(call_id)
        if existing is not None:
            if existing.state == "completed":
                return existing  # repeated delivery returns the stored result
            if existing.state in ("failed", "outcome_unknown"):
                return existing
        record = ToolRecord(call_id=call_id, name=name)
        self._records[call_id] = record
        if self._persist is not None:
            await self._persist(call_id, record)
        return record

    def complete(self, call_id: str, result: Any) -> ToolRecord:
        record = self._records.get(call_id)
        if record is None:
            record = ToolRecord(call_id=call_id, name="?")
            self._records[call_id] = record
        record.state = "completed"
        record.result = result
        return record

    def fail(self, call_id: str, error: str) -> ToolRecord:
        record = self._records.get(call_id)
        if record is None:
            record = ToolRecord(call_id=call_id, name="?")
            self._records[call_id] = record
        record.state = "failed"
        record.error = error
        return record

    def outcome_unknown(self, call_id: str) -> ToolRecord:
        """ENG-09: an executing record across lost ownership -> outcome_unknown."""
        record = self._records.get(call_id)
        if record is None:
            record = ToolRecord(call_id=call_id, name="?")
            self._records[call_id] = record
        record.state = "outcome_unknown"
        return record

    def record_for(self, call_id: str) -> ToolRecord | None:
        return self._records.get(call_id)

    def executing_ids(self) -> list[str]:
        return [c for c, r in self._records.items() if r.state == "executing"]

    def reconcile_executing(self) -> list[str]:
        """ENG-05/09: after restart, orphaned executing records become
        outcome_unknown and must not be re-invoked."""
        ids = self.executing_ids()
        for call_id in ids:
            self.outcome_unknown(call_id)
        return ids
