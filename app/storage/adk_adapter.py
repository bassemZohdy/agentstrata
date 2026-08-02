"""ADK session-service adapter (REQUIREMENTS.md SES-09).

Implements ADK's ``BaseSessionService`` contract over the runtime
``StorageBackend`` so ADK events and the authoritative record share ONE
revisioned transaction path — an independent ADK in-memory history alongside
the configured backend is prohibited. The adapter persists every appended
event through the backend's revision CAS.
"""

from __future__ import annotations

import logging
from typing import Any

from google.adk.events import Event
from google.adk.sessions import Session
from google.adk.sessions.base_session_service import BaseSessionService, ListSessionsResponse

from .contract import StorageBackend
from .model import SessionRecord, utcnow

logger = logging.getLogger(__name__)


def _event_to_dict(event: Event) -> dict[str, Any]:
    return event.model_dump(exclude_none=True, by_alias=True)


def _dict_to_event(data: dict[str, Any]) -> Event:
    return Event.model_validate(data)


def _record_to_session(record: SessionRecord) -> Session:
    return Session(
        app_name=record.agent_name,
        user_id=record.principal_id,
        id=record.session_id,
        state={},
        events=[_dict_to_event(e) for e in record.events],
        # ADK 2.6.1 Session.last_update_time is a unix-epoch float.
        last_update_time=record.updated_at.timestamp(),
    )


class AdkSessionService(BaseSessionService):
    """ADK session service backed by the runtime storage backend (SES-09)."""

    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend
        self._user_state: dict[tuple[str, str], dict[str, Any]] = {}
        # (app, user, session) -> last persisted revision (SES-05 CAS anchor)
        self._revisions: dict[tuple[str, str, str], int] = {}

    async def create_session(
        self,
        *,
        app_name: str,
        user_id: str,
        state: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> Session:
        record = await self._backend.create_session(
            agent_name=app_name, principal_id=user_id, session_id=session_id
        )
        session = _record_to_session(record)
        if state:
            session.state.update(state)
        self._revisions[(app_name, user_id, session.id)] = record.revision
        return session

    async def get_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        config: Any = None,
    ) -> Session | None:
        record = await self._backend.get_session(
            agent_name=app_name, principal_id=user_id, session_id=session_id
        )
        if record is None:
            return None
        self._revisions[(app_name, user_id, session_id)] = record.revision
        return _record_to_session(record)

    async def list_sessions(
        self, *, app_name: str, user_id: str | None = None
    ) -> ListSessionsResponse:
        if user_id is None:
            return ListSessionsResponse(sessions=[])
        records = await self._backend.list_sessions(agent_name=app_name, principal_id=user_id)
        return ListSessionsResponse(sessions=[_record_to_session(r) for r in records])

    async def delete_session(self, *, app_name: str, user_id: str, session_id: str) -> None:
        await self._backend.delete_session(
            agent_name=app_name, principal_id=user_id, session_id=session_id
        )
        self._revisions.pop((app_name, user_id, session_id), None)

    async def append_event(self, session: Session, event: Event) -> Event:
        """Persist the event through the backend's revision CAS (SES-09).

        The base implementation updates the in-memory session object
        (state deltas included); this adapter additionally commits the event
        to the authoritative record as one revisioned transaction.
        """
        key = (session.app_name, session.user_id, session.id)
        expected = self._revisions.get(key)
        if expected is None:
            record = await self._backend.get_session(
                agent_name=session.app_name,
                principal_id=session.user_id,
                session_id=session.id,
            )
            expected = record.revision if record is not None else 1

        result = await super().append_event(session, event)

        record = await self._backend.mutate_session(
            agent_name=session.app_name,
            principal_id=session.user_id,
            session_id=session.id,
            expected_revision=expected,
            events=[_event_to_dict(event)],
            now=utcnow(),
        )
        self._revisions[key] = record.revision
        return result

    async def get_user_state(self, *, app_name: str, user_id: str) -> dict[str, Any]:
        return dict(self._user_state.get((app_name, user_id), {}))

    async def flush(self) -> None:
        # Backends write synchronously/atomically; nothing to buffer.
        return None
