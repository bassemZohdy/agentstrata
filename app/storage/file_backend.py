"""File storage backend (REQUIREMENTS.md SES-01 file row, SES-04, SES-05).

Layout: ``{path}/{agent_name}/{principal_digest}/{session_id}.json`` with safe
fixed-format components. Writes use an exclusive temp file in the target
directory, fsync contents, same-filesystem replace, then fsync the parent
directory. Traversal through attacker-controlled symlinks is rejected. The
directory must pass create/write/fsync/rename/delete probing before
readiness. This backend supports one replica/process only (SES-05).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import stat
import tempfile
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any

from .contract import (
    BackendUnavailableError,
    CapacityError,
    InvalidSessionId,
    RevisionConflict,
    SessionBusy,
    SessionNotFound,
    StorageBackend,
    StorageSettings,
)
from .model import (
    ApprovalRecord,
    Fence,
    IdempotencyRecord,
    RunRecord,
    SessionRecord,
    new_session_id,
    utcnow,
    validate_session_id,
)

logger = logging.getLogger(__name__)


def _principal_digest(principal_id: str) -> str:
    """SHA-256 hex digest of the principal id — a safe fixed-format component."""
    return hashlib.sha256(principal_id.encode("utf-8")).hexdigest()


def _safe_component(value: str, what: str) -> str:
    if not value or value in (".", "..") or "/" in value or "\\" in value or "\x00" in value:
        raise BackendUnavailableError(f"unsafe {what} component {value!r}")
    return value


class FileBackend(StorageBackend):
    kind = "file"

    def __init__(self, base_path: str, settings: StorageSettings | None = None) -> None:
        self._base = Path(base_path)
        self._settings = settings or StorageSettings()
        self._lock = asyncio.Lock()
        self._fences: dict[tuple[str, str, str], Fence] = {}
        self._fence_counters: dict[tuple[str, str, str], int] = {}
        self._ready = False
        self._closed = False

    # -- lifecycle ------------------------------------------------------------------

    async def initialize(self) -> None:
        try:
            await asyncio.to_thread(self._probe)
        except BackendUnavailableError:
            raise
        except OSError as exc:
            raise BackendUnavailableError(f"file storage unavailable: {exc}") from exc
        self._ready = True

    def _probe(self) -> None:
        """SES-04: create/write/fsync/rename/delete probe before readiness."""
        self._check_base()
        probe = self._base / ".agentbase-probe"
        fd, tmp = tempfile.mkstemp(prefix=".probe-", dir=self._base)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("probe")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, probe)
            self._fsync_dir(self._base)
            os.unlink(probe)
        finally:
            with suppress(OSError):
                os.unlink(tmp)

    def _check_base(self) -> None:
        if not self._base.exists():
            raise BackendUnavailableError(f"file storage path absent: {self._base} (SES-04)")
        try:
            st = self._base.lstat()
        except OSError as exc:
            raise BackendUnavailableError(f"file storage path unreadable: {exc}") from exc
        if stat.S_ISLNK(st.st_mode):
            raise BackendUnavailableError(f"file storage base must not be a symlink: {self._base}")
        if not stat.S_ISDIR(st.st_mode):
            raise BackendUnavailableError(f"file storage path is not a directory: {self._base}")

    @staticmethod
    def _fsync_dir(directory: Path) -> None:
        # POSIX: fsync the directory so the rename itself is durable. Windows
        # cannot open a directory for fsync (os.replace is still atomic there).
        if os.name == "nt":
            return
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    async def close(self) -> None:
        self._closed = True
        self._ready = False

    async def health(self) -> bool:
        return self._ready and not self._closed

    # -- path helpers -----------------------------------------------------------------

    def _session_dir(self, agent_name: str, principal_id: str) -> Path:
        self._check_base()
        current = self._base
        components = (
            _safe_component(agent_name, "agent"),
            _safe_component(_principal_digest(principal_id), "principal"),
        )
        for component in components:
            current = current / component
            try:
                st = current.lstat()
            except FileNotFoundError:
                current.mkdir()
                st = current.lstat()
            if stat.S_ISLNK(st.st_mode):
                raise BackendUnavailableError(f"refusing to traverse symlink: {current} (SES-01)")
            if not stat.S_ISDIR(st.st_mode):
                raise BackendUnavailableError(f"not a directory: {current}")
        return current

    def _session_path(self, agent_name: str, principal_id: str, session_id: str) -> Path:
        _safe_component(session_id, "session")
        return self._session_dir(agent_name, principal_id) / f"{session_id}.json"

    def _atomic_write(self, path: Path, content: str) -> None:
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
            self._fsync_dir(path.parent)
        except BaseException:
            with suppress(OSError):
                os.unlink(tmp)
            raise

    def _read_record(self, path: Path, parser: Callable[[str], Any]) -> Any | None:
        if not path.is_file() or path.is_symlink():
            return None
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            return parser(raw)
        except ValueError:
            logger.warning("corrupt record at %s; treating as absent", path)
            return None

    # -- sessions ----------------------------------------------------------------------

    async def create_session(
        self,
        *,
        agent_name: str,
        principal_id: str,
        session_id: str | None = None,
        initial_events: list[dict[str, Any]] | None = None,
        now: datetime | None = None,
    ) -> SessionRecord:
        if not self._ready:
            raise BackendUnavailableError("file storage not ready")
        sid = session_id if session_id is not None else new_session_id()
        if not validate_session_id(sid):
            raise InvalidSessionId(f"invalid session_id {sid!r} (SES-02)")
        now = now or utcnow()
        directory = self._session_dir(agent_name, principal_id)
        path = directory / f"{sid}.json"
        async with self._lock:
            existing = await asyncio.to_thread(self._read_record, path, SessionRecord.from_json)
            if existing is not None:
                return existing
            record = SessionRecord(
                agent_name=agent_name,
                principal_id=principal_id,
                session_id=sid,
                events=list(initial_events or []),
                created_at=now,
                updated_at=now,
            )
            await asyncio.to_thread(self._atomic_write, path, record.to_json())
            return record

    async def get_session(
        self, *, agent_name: str, principal_id: str, session_id: str
    ) -> SessionRecord | None:
        if not self._ready:
            raise BackendUnavailableError("file storage not ready")
        path = self._session_path(agent_name, principal_id, session_id)
        return await asyncio.to_thread(self._read_record, path, SessionRecord.from_json)

    async def mutate_session(
        self,
        *,
        agent_name: str,
        principal_id: str,
        session_id: str,
        expected_revision: int,
        events: list[dict[str, Any]] | None = None,
        usage: dict[str, int] | None = None,
        history_truncated: bool | None = None,
        now: datetime | None = None,
    ) -> SessionRecord:
        if not self._ready:
            raise BackendUnavailableError("file storage not ready")
        now = now or utcnow()
        path = self._session_path(agent_name, principal_id, session_id)

        def _mutate() -> SessionRecord:
            record = self._read_record(path, SessionRecord.from_json)
            if record is None:
                raise SessionNotFound(f"session {session_id!r} not found")
            if record.revision != expected_revision:
                raise RevisionConflict(f"revision {expected_revision} != current {record.revision}")
            record.events = list(record.events) + list(events or [])
            if usage:
                for k, v in usage.items():
                    record.usage[k] = record.usage.get(k, 0) + v
            if history_truncated is not None:
                record.history_truncated = history_truncated
            record.revision += 1
            record.updated_at = now
            self._atomic_write(path, record.to_json())
            return record

        async with self._lock:
            return await asyncio.to_thread(_mutate)

    async def truncate_session_events(
        self,
        *,
        agent_name: str,
        principal_id: str,
        session_id: str,
        keep_revision: int,
    ) -> None:
        path = self._session_path(agent_name, principal_id, session_id)
        async with self._lock:
            record = await asyncio.to_thread(self._read_record, path, SessionRecord.from_json)
            if record is None or record.revision <= keep_revision:
                return
            drop = record.revision - keep_revision
            record.events = record.events[:-drop]
            record.revision = keep_revision
            await asyncio.to_thread(self._atomic_write, path, record.to_json())

    async def delete_session(self, *, agent_name: str, principal_id: str, session_id: str) -> bool:
        if not self._ready:
            raise BackendUnavailableError("file storage not ready")
        path = self._session_path(agent_name, principal_id, session_id)
        directory = path.parent
        async with self._lock:
            record = await asyncio.to_thread(self._read_record, path, SessionRecord.from_json)
            if record is None:
                return False
            runs = await self.list_runs(
                agent_name=agent_name, principal_id=principal_id, session_id=session_id
            )
            if any(not r.terminal for r in runs):
                raise SessionBusy(f"session {session_id!r} has a nonterminal run")
            path.unlink(missing_ok=True)
            self._fsync_dir(directory)
            # cascade run + idempotency files
            for run_path in directory.glob(f"{session_id}.run-*.json"):
                run_path.unlink(missing_ok=True)
            for idem_path in directory.glob(f"{session_id}.idem-*.json"):
                idem_path.unlink(missing_ok=True)
            return True

    async def list_sessions(self, *, agent_name: str, principal_id: str) -> list[SessionRecord]:
        directory = self._session_dir(agent_name, principal_id)
        records: list[SessionRecord] = []
        for path in directory.glob("*.json"):
            if ".run-" in path.name or ".idem-" in path.name:
                continue
            record = await asyncio.to_thread(self._read_record, path, SessionRecord.from_json)
            if record is not None:
                records.append(record)
        return records

    # -- runs ----------------------------------------------------------------------------

    async def create_run(
        self,
        *,
        agent_name: str,
        principal_id: str,
        session_id: str,
        run_id: str,
        run_input: dict[str, Any],
        now: datetime | None = None,
    ) -> RunRecord:
        if not self._ready:
            raise BackendUnavailableError("file storage not ready")
        now = now or utcnow()
        directory = self._session_dir(agent_name, principal_id)
        path = directory / f"{session_id}.run-{run_id}.json"
        async with self._lock:
            existing = await asyncio.to_thread(self._read_record, path, RunRecord.from_json)
            if existing is not None:
                return existing
            session = await asyncio.to_thread(
                self._read_record,
                directory / f"{session_id}.json",
                SessionRecord.from_json,
            )
            if session is None:
                raise SessionNotFound(f"session {session_id!r} not found")
            self._enforce_run_capacity(directory, session_id)
            record = RunRecord(
                agent_name=agent_name,
                principal_id=principal_id,
                session_id=session_id,
                run_id=run_id,
                input=dict(run_input),
                created_at=now,
                updated_at=now,
            )
            await asyncio.to_thread(self._atomic_write, path, record.to_json())
            return record

    async def admit_run(
        self,
        *,
        agent_name: str,
        principal_id: str,
        session_id: str | None,
        run_id: str,
        run_input: dict[str, Any],
        now: datetime | None = None,
    ) -> tuple[str, int]:
        """Atomic admission under ONE lock hold: ensure the session (file
        create when absent) and create the run record together."""
        if not self._ready:
            raise BackendUnavailableError("file storage not ready")
        from .model import new_session_id, validate_session_id

        now = now or utcnow()
        sid = session_id if session_id is not None else new_session_id()
        if not validate_session_id(sid):
            raise InvalidSessionId(f"invalid session_id {sid!r} (SES-02)")
        directory = self._session_dir(agent_name, principal_id)
        session_path = directory / f"{sid}.json"
        run_path = directory / f"{sid}.run-{run_id}.json"
        async with self._lock:
            session = await asyncio.to_thread(
                self._read_record, session_path, SessionRecord.from_json
            )
            if session is None:
                session = SessionRecord(
                    agent_name=agent_name,
                    principal_id=principal_id,
                    session_id=sid,
                    created_at=now,
                    updated_at=now,
                )
                await asyncio.to_thread(self._atomic_write, session_path, session.to_json())
            run = await asyncio.to_thread(self._read_record, run_path, RunRecord.from_json)
            if run is not None:
                return sid, session.revision
            self._enforce_run_capacity(directory, sid)
            record = RunRecord(
                agent_name=agent_name,
                principal_id=principal_id,
                session_id=sid,
                run_id=run_id,
                input=dict(run_input),
                created_at=now,
                updated_at=now,
            )
            await asyncio.to_thread(self._atomic_write, run_path, record.to_json())
            return sid, session.revision

    async def get_run(
        self, *, agent_name: str, principal_id: str, session_id: str, run_id: str
    ) -> RunRecord | None:
        directory = self._session_dir(agent_name, principal_id)
        path = directory / f"{session_id}.run-{run_id}.json"
        return await asyncio.to_thread(self._read_record, path, RunRecord.from_json)

    async def update_run(
        self,
        *,
        agent_name: str,
        principal_id: str,
        session_id: str,
        run_id: str,
        status: str | None = None,
        iteration_count: int | None = None,
        outcome: dict[str, Any] | None = None,
        usage: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> RunRecord | None:
        now = now or utcnow()
        directory = self._session_dir(agent_name, principal_id)
        path = directory / f"{session_id}.run-{run_id}.json"
        async with self._lock:
            record = await asyncio.to_thread(self._read_record, path, RunRecord.from_json)
            if record is None:
                return None
            if status is not None:
                record.status = status
            if iteration_count is not None:
                record.iteration_count = iteration_count
            if outcome is not None:
                record.outcome = dict(outcome)
            if usage:
                for k, v in usage.items():
                    record.usage[k] = record.usage.get(k, 0) + v
            record.updated_at = now
            await asyncio.to_thread(self._atomic_write, path, record.to_json())
            return record

    async def list_runs(
        self, *, agent_name: str, principal_id: str, session_id: str
    ) -> list[RunRecord]:
        directory = self._session_dir(agent_name, principal_id)
        records: list[RunRecord] = []
        for path in directory.glob(f"{session_id}.run-*.json"):
            record = await asyncio.to_thread(self._read_record, path, RunRecord.from_json)
            if record is not None:
                records.append(record)
        return records

    # -- idempotency ------------------------------------------------------------------------

    async def create_idempotency(
        self,
        *,
        agent_name: str,
        principal_id: str,
        session_id: str,
        key: str,
        ttl_seconds: int,
        now: datetime | None = None,
    ) -> IdempotencyRecord:
        now = now or utcnow()
        directory = self._session_dir(agent_name, principal_id)
        path = directory / f"{session_id}.idem-{key}.json"
        async with self._lock:
            existing = await asyncio.to_thread(self._read_record, path, IdempotencyRecord.from_json)
            if existing is not None:
                return existing
            self._enforce_idempotency_capacity(directory, session_id)
            record = IdempotencyRecord(
                agent_name=agent_name,
                principal_id=principal_id,
                session_id=session_id,
                key=key,
                created_at=now,
                expires_at=now + __import__("datetime").timedelta(seconds=ttl_seconds),
            )
            await asyncio.to_thread(self._atomic_write, path, record.to_json())
            return record

    async def get_idempotency(
        self, *, agent_name: str, principal_id: str, session_id: str, key: str
    ) -> IdempotencyRecord | None:
        directory = self._session_dir(agent_name, principal_id)
        path = directory / f"{session_id}.idem-{key}.json"
        return await asyncio.to_thread(self._read_record, path, IdempotencyRecord.from_json)

    async def finish_idempotency(
        self,
        *,
        agent_name: str,
        principal_id: str,
        session_id: str,
        key: str,
        status: str,
        outcome: dict[str, Any],
        now: datetime | None = None,
    ) -> IdempotencyRecord | None:
        directory = self._session_dir(agent_name, principal_id)
        path = directory / f"{session_id}.idem-{key}.json"
        async with self._lock:
            record = await asyncio.to_thread(self._read_record, path, IdempotencyRecord.from_json)
            if record is None:
                return None
            record.status = status
            record.outcome = dict(outcome)
            await asyncio.to_thread(self._atomic_write, path, record.to_json())
            return record

    async def expire_idempotency(
        self, *, agent_name: str, principal_id: str, session_id: str, key: str
    ) -> bool:
        directory = self._session_dir(agent_name, principal_id)
        path = directory / f"{session_id}.idem-{key}.json"
        async with self._lock:
            if not path.is_file() or path.is_symlink():
                return False
            path.unlink(missing_ok=True)
            self._fsync_dir(directory)
            return True

    # -- retention & capacity ----------------------------------------------------------------

    async def sweep(self, *, now: datetime | None = None) -> dict[str, int]:
        now = now or utcnow()
        stats = {"sessions": 0, "runs": 0, "idempotency": 0}
        async with self._lock:
            for agent_dir in list(self._base.iterdir()) if self._base.is_dir() else []:
                if not agent_dir.is_dir() or agent_dir.is_symlink():
                    continue
                for principal_dir in list(agent_dir.iterdir()):
                    if not principal_dir.is_dir() or principal_dir.is_symlink():
                        continue
                    for path in list(principal_dir.glob("*.json")):
                        name = path.stem
                        if name.endswith(".run") or ".run-" in name:
                            stats["runs"] += await asyncio.to_thread(
                                self._maybe_delete_run, path, now
                            )
                        elif name.endswith(".idem") or ".idem-" in name:
                            stats["idempotency"] += await asyncio.to_thread(
                                self._maybe_delete_idem, path, now
                            )
                        else:
                            stats["sessions"] += await asyncio.to_thread(
                                self._maybe_delete_session, path, now
                            )
        return stats

    def _enforce_run_capacity(self, directory: Path, session_id: str) -> None:
        """SES-07: at maxRunsPerSession delete the oldest terminal records;
        if capacity cannot be freed, fail storage_capacity."""
        cap = self._settings.max_runs_per_session
        runs = [
            r
            for path in directory.glob(f"{session_id}.run-*.json")
            if (r := self._read_record(path, RunRecord.from_json)) is not None
        ]
        terminal = sorted([r for r in runs if r.terminal], key=lambda r: r.updated_at)
        while len(runs) >= cap and terminal:
            oldest = terminal.pop(0)
            (directory / f"{session_id}.run-{oldest.run_id}.json").unlink(missing_ok=True)
            runs = [r for r in runs if r.run_id != oldest.run_id]
        if len(runs) >= cap:
            raise CapacityError("maxRunsPerSession reached; cannot free capacity")

    def _enforce_idempotency_capacity(self, directory: Path, session_id: str) -> None:
        """SES-07: at maxIdempotencyRecordsPerSession a new key fails rather
        than evicting an unexpired record."""
        cap = self._settings.max_idempotency_records_per_session
        count = len(list(directory.glob(f"{session_id}.idem-*.json")))
        if count >= cap:
            raise CapacityError("maxIdempotencyRecordsPerSession reached")

    def _maybe_delete_session(self, path: Path, now: datetime) -> int:
        record = self._read_record(path, SessionRecord.from_json)
        if record is None:
            return 0
        # SES-06: skip a session with a live run/lease (fence)
        fkey = (path.parent.parent.name, path.parent.name, record.session_id)
        if fkey in self._fences:
            return 0
        ttl = self._settings.session_ttl_seconds
        if ttl != 0 and (now - record.updated_at).total_seconds() > ttl:
            runs = list(path.parent.glob(f"{record.session_id}.run-*.json"))
            run_recs = [self._read_record(rp, RunRecord.from_json) for rp in runs]
            if not any(r is not None and not r.terminal for r in run_recs):
                path.unlink(missing_ok=True)
                return 1
        return 0

    def _maybe_delete_run(self, path: Path, now: datetime) -> int:
        record = self._read_record(path, RunRecord.from_json)
        if (
            record is not None
            and record.terminal
            and (now - record.updated_at).total_seconds() > self._settings.run_ttl_seconds
        ):
            path.unlink(missing_ok=True)
            return 1
        return 0

    def _maybe_delete_idem(self, path: Path, now: datetime) -> int:
        record = self._read_record(path, IdempotencyRecord.from_json)
        if record is not None and record.expires_at is not None and record.expires_at <= now:
            path.unlink(missing_ok=True)
            return 1
        return 0

    # -- fencing (in-process; one replica, SES-05) -----------------------------

    @staticmethod
    def _fkey(agent_name: str, principal_id: str, session_id: str) -> tuple[str, str, str]:
        """Fence keys use the principal digest so sweep can match them from
        file paths without a principal index."""
        return (agent_name, _principal_digest(principal_id), session_id)

    async def acquire_fence(
        self,
        *,
        agent_name: str,
        principal_id: str,
        session_id: str,
        token: str,
        ttl_seconds: float,
        now: datetime | None = None,
    ) -> Fence | None:
        now = now or utcnow()
        key = self._fkey(agent_name, principal_id, session_id)
        async with self._lock:
            current = self._fences.get(key)
            if current is not None and (current.expires_at is None or current.expires_at > now):
                return None
            number = self._fence_counters.get(key, 0) + 1
            self._fence_counters[key] = number
            fence = Fence(
                token=token,
                fencing_number=number,
                expires_at=now + __import__("datetime").timedelta(seconds=ttl_seconds),
            )
            self._fences[key] = fence
            return fence

    async def renew_fence(
        self,
        *,
        agent_name: str,
        principal_id: str,
        session_id: str,
        token: str,
        ttl_seconds: float,
    ) -> bool:
        key = self._fkey(agent_name, principal_id, session_id)
        async with self._lock:
            current = self._fences.get(key)
            if current is None or current.token != token:
                return False
            current.expires_at = utcnow() + __import__("datetime").timedelta(seconds=ttl_seconds)
            return True

    async def release_fence(
        self, *, agent_name: str, principal_id: str, session_id: str, token: str
    ) -> bool:
        key = self._fkey(agent_name, principal_id, session_id)
        async with self._lock:
            current = self._fences.get(key)
            if current is None or current.token != token:
                return False
            self._fences.pop(key, None)
            return True

    async def current_fence(
        self, *, agent_name: str, principal_id: str, session_id: str
    ) -> Fence | None:
        return self._fences.get(self._fkey(agent_name, principal_id, session_id))

    async def find_run(
        self, *, agent_name: str, principal_id: str, run_id: str
    ) -> RunRecord | None:
        directory = self._session_dir(agent_name, principal_id)
        if not directory.is_dir():
            return None
        for path in directory.glob("run-*.json"):
            record = await asyncio.to_thread(self._read_record, path, RunRecord.from_json)
            if record is not None and record.run_id == run_id:
                return record
        return None

    # -- approvals (HITL-02/04) ----------------------------------------------

    def _approval_path(self, agent_name: str, principal_id: str, approval_id: str) -> Path:
        directory = self._session_dir(agent_name, principal_id)
        safe = re.sub(r"[^0-9a-zA-Z_-]", "_", approval_id)
        return directory / f"approval-{safe}.json"

    async def create_approval(
        self,
        *,
        agent_name: str,
        principal_id: str,
        session_id: str,
        run_id: str,
        approval_id: str,
        config_generation: int,
        server_name: str,
        raw_tool_name: str,
        final_tool_name: str,
        args_hash: str,
        args_preview: str,
        checkpoint: dict[str, Any],
        timeout_seconds: int,
        now: datetime | None = None,
    ) -> ApprovalRecord:
        if not self._ready:
            raise BackendUnavailableError("file storage not ready")
        now = now or utcnow()
        record = ApprovalRecord(
            agent_name=agent_name,
            principal_id=principal_id,
            session_id=session_id,
            run_id=run_id,
            approval_id=approval_id,
            config_generation=config_generation,
            server_name=server_name,
            raw_tool_name=raw_tool_name,
            final_tool_name=final_tool_name,
            args_hash=args_hash,
            args_preview=args_preview,
            checkpoint=checkpoint,
            timeout_seconds=timeout_seconds,
            created_at=now,
            expires_at=now + __import__("datetime").timedelta(seconds=timeout_seconds),
        )
        path = self._approval_path(agent_name, principal_id, approval_id)
        async with self._lock:
            await asyncio.to_thread(self._atomic_write, path, record.to_json())
        return record

    async def get_approval(
        self, *, agent_name: str, principal_id: str, approval_id: str
    ) -> ApprovalRecord | None:
        path = self._approval_path(agent_name, principal_id, approval_id)
        return await asyncio.to_thread(self._read_record, path, ApprovalRecord.from_json)

    async def list_approvals(
        self, *, agent_name: str, principal_id: str, session_id: str
    ) -> list[ApprovalRecord]:
        directory = self._session_dir(agent_name, principal_id)
        records: list[ApprovalRecord] = []

        def _scan() -> list[ApprovalRecord]:
            out: list[ApprovalRecord] = []
            if not directory.is_dir():
                return out
            for path in directory.glob("approval-*.json"):
                record = self._read_record(path, ApprovalRecord.from_json)
                if record is not None and record.session_id == session_id:
                    out.append(record)
            return out

        records = await asyncio.to_thread(_scan)
        records.sort(key=lambda r: r.created_at)
        return records

    async def list_all_approvals(self, *, agent_name: str) -> list[ApprovalRecord]:
        root = self._base / agent_name
        if not root.is_dir():
            return []
        out: list[ApprovalRecord] = []
        for directory in root.glob("*/approval-*.json"):
            record = await asyncio.to_thread(self._read_record, directory, ApprovalRecord.from_json)
            if record is not None:
                out.append(record)
        out.sort(key=lambda r: r.created_at)
        return out

    async def decide_approval(
        self,
        *,
        agent_name: str,
        principal_id: str,
        approval_id: str,
        decision: str,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> ApprovalRecord | None:
        now = now or utcnow()
        path = self._approval_path(agent_name, principal_id, approval_id)
        async with self._lock:

            def _decide() -> ApprovalRecord | None:
                record = self._read_record(path, ApprovalRecord.from_json)
                if record is None or not record.pending:
                    return None
                if now > record.expires_at and decision != "timed_out":
                    return None
                record.status = decision
                record.reason = reason
                record.decided_at = now
                record.revision += 1
                self._atomic_write(path, record.to_json())
                return record

            return await asyncio.to_thread(_decide)

    async def expire_approvals(self, *, now: datetime | None = None) -> list[ApprovalRecord]:
        now = now or utcnow()
        expired: list[ApprovalRecord] = []
        async with self._lock:
            base = self._base
            directories = [d for d in base.iterdir() if d.is_dir()] if base.is_dir() else []

            def _expire(directory: Path) -> list[ApprovalRecord]:
                out: list[ApprovalRecord] = []
                for path in directory.rglob("approval-*.json"):
                    record = self._read_record(path, ApprovalRecord.from_json)
                    if record is not None and record.pending and now > record.expires_at:
                        record.status = "timed_out"
                        record.decided_at = now
                        record.revision += 1
                        self._atomic_write(path, record.to_json())
                        out.append(record)
                return out

            for directory in directories:
                expired.extend(await asyncio.to_thread(_expire, directory))
        return expired
