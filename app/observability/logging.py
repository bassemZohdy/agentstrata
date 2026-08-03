"""Structured logging facade (REQUIREMENTS.md OBS-01, OBS-02).

Stdlib ``logging`` is the facade. JSON format emits one object per line with
``ts``/``level``/``logger``/``event``/``msg`` plus request correlation
(request_id, run_id, optional session_id, principal digest,
config_generation). Text format carries the same correlation values. Request
IDs are validated/generated per OBS-02; valid W3C ``traceparent`` is honored
independently.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
import uuid
from typing import Any

REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

_CORRELATION_FIELDS = (
    "request_id",
    "run_id",
    "session_id",
    "principal",
    "config_generation",
)


class JsonFormatter(logging.Formatter):
    """OBS-01: one JSON object per line with ts/level/logger/event/msg."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": _iso_time(record.created),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", record.getMessage()),
            "msg": record.getMessage(),
        }
        for field in _CORRELATION_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"), default=str)


class TextFormatter(logging.Formatter):
    """OBS-01: human-readable with the same correlation values."""

    def format(self, record: logging.LogRecord) -> str:
        base = f"{_iso_time(record.created)} {record.levelname} {record.name} {record.getMessage()}"
        extras = " ".join(
            f"{f}={getattr(record, f)}"
            for f in _CORRELATION_FIELDS
            if getattr(record, f, None) is not None
        )
        return f"{base} {extras}".rstrip()


def configure_logging(config: Any) -> None:
    """Wire the configured format/level on the root logger (OBS-01)."""
    formatter = JsonFormatter() if config.logFormat.value == "json" else TextFormatter()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(config.logLevel.value)


def normalize_request_id(incoming: str | None) -> str:
    """OBS-02: accept X-Request-Id only when it matches the pattern;
    otherwise generate UUIDv4 and log a value-free warning."""
    if incoming and REQUEST_ID_RE.match(incoming):
        return incoming
    if incoming is not None:
        logging.getLogger("agentstrata").warning(
            "invalid_request_id", extra={"event": "invalid_request_id"}
        )
    return str(uuid.uuid4())


def validate_traceparent(traceparent: str | None) -> bool:
    """OBS-02: honor a valid W3C traceparent independently of request ID."""
    if not traceparent:
        return False
    # version-traceid-parentid-flags (traceid 32 hex, parentid 16 hex)
    parts = traceparent.split("-")
    if len(parts) != 4:
        return False
    trace_id, parent_id = parts[1], parts[2]
    if len(trace_id) != 32 or len(parent_id) != 16:
        return False
    return all(c in "0123456789abcdef" for c in trace_id + parent_id)


def _iso_time(epoch: float) -> str:
    return (
        time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(epoch))
        + f".{int(epoch % 1 * 1_000_000):06d}Z"
    )


def event_logger(event: str) -> logging.Logger:
    """Logger for a named event with correlation support."""
    return logging.getLogger("agentstrata.events")
