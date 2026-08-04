"""Session/run/idempotency data model (REQUIREMENTS.md SES-01).

Records carry an explicit internal ``schema_version``; stored JSON is
revisioned per mutation (SES-05). Runs, tool-audit records, and idempotency
records are associated data and are never blindly replayed as model
conversation.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

SCHEMA_VERSION = 1

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

# SES-03 principal namespace prefixes
PRINCIPAL_ANONYMOUS = "anonymous"


def new_session_id() -> str:
    """SES-02: generate UUIDv4 when no session_id is supplied."""
    return str(uuid.uuid4())


def validate_session_id(session_id: str) -> bool:
    return bool(SESSION_ID_RE.match(session_id))


def utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _parse_iso(value: str | None, default: datetime | None = None) -> datetime:
    """Parse a stored ISO timestamp, falling back to ``default`` (or now)."""
    if not value:
        return default or utcnow()
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return default or utcnow()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


@dataclass
class SessionRecord:
    """The authoritative conversation record (SES-01)."""

    agent_name: str
    principal_id: str
    session_id: str
    revision: int = 1
    events: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    history_truncated: bool = False
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
    schema_version: int = SCHEMA_VERSION

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.agent_name, self.principal_id, self.session_id)

    def to_json(self) -> str:
        return json.dumps(
            {
                "schema_version": self.schema_version,
                "agent_name": self.agent_name,
                "principal_id": self.principal_id,
                "session_id": self.session_id,
                "revision": self.revision,
                "events": self.events,
                "usage": self.usage,
                "history_truncated": self.history_truncated,
                "created_at": _iso(self.created_at),
                "updated_at": _iso(self.updated_at),
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, raw: str) -> SessionRecord:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("corrupt SessionRecord record") from exc
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"unsupported storage schema_version {data.get('schema_version')}")
        return cls(
            agent_name=data["agent_name"],
            principal_id=data["principal_id"],
            session_id=data["session_id"],
            revision=int(data.get("revision", 1)),
            events=list(data.get("events", [])),
            usage=dict(data.get("usage", {})),
            history_truncated=bool(data.get("history_truncated", False)),
            created_at=_parse_iso(data.get("created_at"), _parse_iso(data.get("updated_at"))),
            updated_at=_parse_iso(data.get("updated_at"), _parse_iso(data.get("created_at"))),
        )


@dataclass
class RunRecord:
    """One agent run associated with a session (never replayed as context)."""

    agent_name: str
    principal_id: str
    session_id: str
    run_id: str
    status: str = "created"  # created|running|succeeded|failed|cancelled|cancelling
    iteration_count: int = 0
    input: dict[str, Any] = field(default_factory=dict)
    outcome: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
    schema_version: int = SCHEMA_VERSION

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.agent_name, self.principal_id, self.session_id, self.run_id)

    @property
    def terminal(self) -> bool:
        return self.status in ("succeeded", "failed", "cancelled")

    def to_json(self) -> str:
        return json.dumps(
            {
                "schema_version": self.schema_version,
                "agent_name": self.agent_name,
                "principal_id": self.principal_id,
                "session_id": self.session_id,
                "run_id": self.run_id,
                "status": self.status,
                "iteration_count": self.iteration_count,
                "input": self.input,
                "outcome": self.outcome,
                "usage": self.usage,
                "created_at": _iso(self.created_at),
                "updated_at": _iso(self.updated_at),
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, raw: str) -> RunRecord:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("corrupt RunRecord record") from exc
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"unsupported storage schema_version {data.get('schema_version')}")
        return cls(
            agent_name=data["agent_name"],
            principal_id=data["principal_id"],
            session_id=data["session_id"],
            run_id=data["run_id"],
            status=str(data.get("status", "created")),
            iteration_count=int(data.get("iteration_count", 0)),
            input=dict(data.get("input", {})),
            outcome=dict(data.get("outcome", {})),
            usage=dict(data.get("usage", {})),
            created_at=_parse_iso(data.get("created_at"), _parse_iso(data.get("updated_at"))),
            updated_at=_parse_iso(data.get("updated_at"), _parse_iso(data.get("created_at"))),
        )


@dataclass
class IdempotencyRecord:
    """One idempotency key scoped to a session (API-06a / SES-06/07)."""

    agent_name: str
    principal_id: str
    session_id: str
    key: str
    status: str = "in_progress"  # in_progress|completed|failed
    outcome: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)
    expires_at: datetime | None = None
    schema_version: int = SCHEMA_VERSION

    @property
    def key_tuple(self) -> tuple[str, str, str, str]:
        return (self.agent_name, self.principal_id, self.session_id, self.key)

    def to_json(self) -> str:
        return json.dumps(
            {
                "schema_version": self.schema_version,
                "agent_name": self.agent_name,
                "principal_id": self.principal_id,
                "session_id": self.session_id,
                "key": self.key,
                "status": self.status,
                "outcome": self.outcome,
                "created_at": _iso(self.created_at),
                "expires_at": _iso(self.expires_at),
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, raw: str) -> IdempotencyRecord:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("corrupt IdempotencyRecord record") from exc
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"unsupported storage schema_version {data.get('schema_version')}")
        return cls(
            agent_name=data["agent_name"],
            principal_id=data["principal_id"],
            session_id=data["session_id"],
            key=data["key"],
            status=str(data.get("status", "in_progress")),
            outcome=dict(data.get("outcome", {})),
            created_at=_parse_iso(data.get("created_at")),
            expires_at=_parse_iso(data.get("expires_at")),
        )


@dataclass
class Fence:
    """Session ownership fence (SES-05): token + monotonic fencing number."""

    token: str
    fencing_number: int
    expires_at: datetime | None = None


@dataclass
class ApprovalRecord:
    """HITL-02/04: durable tool-approval record + protected checkpoint.

    ``checkpoint`` holds the FULL canonical tool-call arguments required for
    exact resume and is never exposed publicly; the public metadata surface is
    ``args_hash`` + ``args_preview`` only. ``revision`` is the CAS anchor for
    the first-wins decision race (HITL-04). Status transitions:
    pending -> approved|denied|timed_out|cancelled|stale (exactly one wins).
    """

    agent_name: str
    principal_id: str
    session_id: str
    run_id: str
    approval_id: str
    config_generation: int
    server_name: str
    raw_tool_name: str
    final_tool_name: str
    args_hash: str
    args_preview: str
    checkpoint: dict[str, Any]
    status: str = "pending"
    timeout_seconds: int = 300
    reason: str | None = None
    created_at: datetime = field(default_factory=utcnow)
    expires_at: datetime = field(default_factory=utcnow)
    decided_at: datetime | None = None
    revision: int = 1
    schema_version: int = SCHEMA_VERSION

    @property
    def pending(self) -> bool:
        return self.status == "pending"

    @property
    def decided(self) -> bool:
        return self.status in ("approved", "denied")

    def to_json(self) -> str:
        data = {
            "agent_name": self.agent_name,
            "principal_id": self.principal_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "approval_id": self.approval_id,
            "config_generation": self.config_generation,
            "server_name": self.server_name,
            "raw_tool_name": self.raw_tool_name,
            "final_tool_name": self.final_tool_name,
            "args_hash": self.args_hash,
            "args_preview": self.args_preview,
            "checkpoint": self.checkpoint,
            "status": self.status,
            "timeout_seconds": self.timeout_seconds,
            "reason": self.reason,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "revision": self.revision,
            "schema_version": self.schema_version,
        }
        return json.dumps(data, default=str)

    @classmethod
    def from_json(cls, data: dict[str, Any] | str) -> ApprovalRecord:
        parsed: dict[str, Any]
        if isinstance(data, str):
            parsed = json.loads(data)
        else:
            parsed = data
        if parsed.get("schema_version", 1) != SCHEMA_VERSION:
            raise ValueError(f"unsupported storage schema_version {parsed.get('schema_version')}")
        return cls(
            agent_name=parsed["agent_name"],
            principal_id=parsed["principal_id"],
            session_id=parsed["session_id"],
            run_id=parsed["run_id"],
            approval_id=parsed["approval_id"],
            config_generation=parsed.get("config_generation", 1),
            server_name=parsed.get("server_name", ""),
            raw_tool_name=parsed.get("raw_tool_name", ""),
            final_tool_name=parsed.get("final_tool_name", ""),
            args_hash=parsed.get("args_hash", ""),
            args_preview=parsed.get("args_preview", ""),
            checkpoint=dict(parsed.get("checkpoint", {})),
            status=str(parsed.get("status", "pending")),
            timeout_seconds=parsed.get("timeout_seconds", 300),
            reason=parsed.get("reason"),
            created_at=_parse_iso(parsed.get("created_at")),
            expires_at=_parse_iso(parsed.get("expires_at")),
            decided_at=_parse_iso(parsed.get("decided_at")) if parsed.get("decided_at") else None,
            revision=parsed.get("revision", 1),
        )
