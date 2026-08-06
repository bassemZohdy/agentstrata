"""Tool side-effect ledger (REQUIREMENTS.md ENG-09).

Each ADK tool-call ID is executed at most once within a run (the dedup
state machine below).  Completed tool activity is also recorded in the
run audit (ENG-06/SES-01); a DURABLE per-call record that survives lost
ownership (the ``executing`` -> ``outcome_unknown`` cross-restart
contract) is not persisted — the storage layer has no tool-record store,
and a crashed process's nonterminal run is reconciled to
``failed/run_interrupted`` by the storage sweep (ENG-05).  The runtime
never automatically retries a tool.
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
    """ENG-09 dedup state machine for one run (in-process only).

    R-20: the durable ``persist`` callback and the cross-restart
    reconcilers were unwired dead code and are removed — see the module
    docstring for the scope decision.
    """

    def __init__(self) -> None:
        self._records: dict[str, ToolRecord] = {}

    async def begin(self, call_id: str, name: str) -> ToolRecord:
        existing = self._records.get(call_id)
        if existing is not None:
            if existing.state == "completed":
                return existing  # repeated delivery returns the stored result
            if existing.state in ("failed", "outcome_unknown"):
                return existing
        record = ToolRecord(call_id=call_id, name=name)
        self._records[call_id] = record
        return record

    def complete(self, call_id: str, result: Any) -> ToolRecord:
        record = self._records.get(call_id)
        if record is None:
            record = ToolRecord(call_id=call_id, name="?")
            self._records[call_id] = record
        record.state = "completed"
        record.result = result
        return record

    def outcome_unknown(self, call_id: str) -> ToolRecord:
        """ENG-09: an executing record across lost ownership -> outcome_unknown."""
        record = self._records.get(call_id)
        if record is None:
            record = ToolRecord(call_id=call_id, name="?")
            self._records[call_id] = record
        record.state = "outcome_unknown"
        return record
