"""Deterministic in-process mock runner for the release performance gates.

REQUIREMENTS.md §6 (NFR-00/NFR-02): "Release performance gates run against
the Linux amd64 image ... and the deterministic mock AgentRunner", and
NFR-02 measures "request receipt through validation/session work to
serialization of a deterministic in-process mock result". Under
``AGENT_MOCK_MODEL`` the components build this runner instead of the ADK
runner: the session work (admission, run record, terminal commit) and the
event stream shape are REAL, the model/flow internals are the deterministic
in-process mock. The hook is inert unless the env is set; normal
deployments never construct it.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from ..storage.model import utcnow
from .events import AgentEvent, Done, Iteration, TextDelta
from .runner import RunRequest


class MockAgentRunner:
    """Deterministic in-process runner (NFR-02 spec definition)."""

    def __init__(self, backend: Any, app_name: str = "agent") -> None:
        self._backend = backend
        self._app_name = app_name

    async def execute(self, request: RunRequest) -> AsyncGenerator[AgentEvent, None]:
        # Session work is real: admit (get/create the session + run record),
        # then the deterministic in-process mock result, then the terminal
        # commit — the same storage path a real run exercises.
        # The session work mirrors the real runner's ENG-03 step 5: a
        # stateless request gets a freshly generated session record.
        if request.session_id is None:
            record = await self._backend.create_session(
                agent_name=self._app_name,
                principal_id=request.principal_id,
            )
            sid = record.session_id
        else:
            sid = request.session_id
            existing = await self._backend.get_session(
                agent_name=self._app_name,
                principal_id=request.principal_id,
                session_id=sid,
            )
            if existing is None:
                await self._backend.create_session(
                    agent_name=self._app_name,
                    principal_id=request.principal_id,
                    session_id=sid,
                )
        run_id = f"mock-{request.request_id or 'run'}"
        await self._backend.create_run(
            agent_name=self._app_name,
            principal_id=request.principal_id,
            session_id=sid,
            run_id=run_id,
            run_input={"user_message": request.user_message, "request_id": request.request_id},
            now=utcnow(),
        )
        yield Iteration(index=0)
        yield TextDelta(text="ok")
        yield Done(finish_reason="stop")
        await self._backend.update_run(
            agent_name=self._app_name,
            principal_id=request.principal_id,
            session_id=sid,
            run_id=run_id,
            status="succeeded",
            outcome={"text": "ok"},
            usage={"input_tokens": 0, "output_tokens": 0},
            now=utcnow(),
        )
