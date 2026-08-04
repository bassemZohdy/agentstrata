"""P3 approval + run endpoints (HITL-03).

- ``POST /v1/approvals/{approval_id}`` — CAS decision (HITL-04): approve
  resumes the paused run from its checkpoint exactly once; a repeated
  decision returns the stored outcome; a conflicting decision returns 409.
- ``GET /v1/approvals?session_id=`` — owner-scoped pending approvals with
  public metadata only (hash + redacted preview; never the checkpoint).
- ``GET /v1/runs/{run_id}`` / ``DELETE /v1/runs/{run_id}`` — owner-scoped
  run state + idempotent cancellation (a pending approval is cancelled
  with the run).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..errors import PublicErrorResponse


def _public_approval(record: Any) -> dict[str, Any]:
    """HITL-02: the public surface is the hash + redacted preview only."""
    return {
        "approval_id": record.approval_id,
        "status": record.status,
        "session_id": record.session_id,
        "run_id": record.run_id,
        "tool": record.final_tool_name,
        "server": record.server_name,
        "args_hash": record.args_hash,
        "args_preview": record.args_preview,
        "created_at": record.created_at.isoformat(),
        "expires_at": record.expires_at.isoformat(),
        "decided_at": record.decided_at.isoformat() if record.decided_at else None,
        "reason": record.reason,
    }


def register(app: Any, config: Any, components: dict[str, Any]) -> None:
    agent_name = config.name
    backend = components["backend"]
    router = APIRouter(prefix="/v1")

    @router.get("/approvals")
    async def list_approvals(request: Request, session_id: str):
        # API-09: no enumeration — the caller only sees their own session.
        principal = getattr(request.state, "principal", "anonymous")
        records = await backend.list_approvals(
            agent_name=agent_name, principal_id=principal, session_id=session_id
        )
        return JSONResponse(
            {
                "object": "approval.list",
                "approvals": [_public_approval(r) for r in records if r.pending],
            }
        )

    @router.post("/approvals/{approval_id}")
    async def decide_approval(request: Request, approval_id: str):
        # HITL-04: decision CAS — approve resumes the paused run exactly
        # once; repeats return the stored outcome; conflicts return 409.
        principal = getattr(request.state, "principal", "anonymous")
        try:
            body = await request.json()
        except Exception:
            body = {}
        decision = body.get("decision")
        if decision not in ("approve", "deny"):
            raise PublicErrorResponse("invalid_decision", "decision must be approve or deny", 400)
        record = await backend.get_approval(
            agent_name=agent_name, principal_id=principal, approval_id=approval_id
        )
        if record is None:
            raise PublicErrorResponse("approval_not_found", "unknown approval id", 404)
        # HITL-03: the public decision vocabulary is approve|deny; the
        # engine's CAS status vocabulary is approved|denied (past tense).
        internal = "approved" if decision == "approve" else "denied"
        if not record.pending:
            # already decided: same decision -> stored outcome, else 409
            if record.status == internal:
                return JSONResponse(_public_approval(record))
            raise PublicErrorResponse(
                "approval_conflict",
                "this approval was already decided",
                409,
            )
        if record.status == "timed_out":
            raise PublicErrorResponse("approval_expired", "this approval timed out", 410)
        outcome = await components["runner"].resume_approval(
            approval_id=approval_id,
            principal_id=principal,
            decision=internal,
            reason=body.get("reason"),
        )
        if outcome is None:
            # lost the CAS race (someone else decided first) — re-read
            latest = await backend.get_approval(
                agent_name=agent_name, principal_id=principal, approval_id=approval_id
            )
            if latest is not None and latest.status == decision:
                return JSONResponse(_public_approval(latest))
            raise PublicErrorResponse("approval_conflict", "this approval was already decided", 409)
        result = _public_approval(
            await backend.get_approval(
                agent_name=agent_name, principal_id=principal, approval_id=approval_id
            )
            or record
        )
        result["outcome"] = outcome.get("status")
        from ...engine.events import TextDelta

        result["result_text"] = "".join(
            e.text for e in outcome.get("events", []) if isinstance(e, TextDelta)
        )
        return JSONResponse(result)

    @router.get("/runs/{run_id}")
    async def get_run(request: Request, run_id: str):
        principal = getattr(request.state, "principal", "anonymous")
        record = await backend.find_run(
            agent_name=agent_name, principal_id=principal, run_id=run_id
        )
        if record is None:
            raise PublicErrorResponse("run_not_found", "unknown run id", 404)
        return JSONResponse(json.loads(record.to_json()))

    @router.delete("/runs/{run_id}")
    async def delete_run(request: Request, run_id: str):
        # idempotent cancellation; a pending approval dies with the run.
        principal = getattr(request.state, "principal", "anonymous")
        record = await backend.find_run(
            agent_name=agent_name, principal_id=principal, run_id=run_id
        )
        if record is None:
            raise PublicErrorResponse("run_not_found", "unknown run id", 404)
        # a pending approval dies with the run (idempotent cancellation)
        if record.session_id:
            approvals = await backend.list_approvals(
                agent_name=agent_name, principal_id=principal, session_id=record.session_id
            )
            for approval in approvals:
                if approval.pending and approval.run_id == run_id:
                    await backend.decide_approval(
                        agent_name=agent_name,
                        principal_id=principal,
                        approval_id=approval.approval_id,
                        decision="cancelled",
                        reason="run deleted",
                    )
        if record.status != "cancelled" and record.session_id:
            await backend.update_run(
                agent_name=agent_name,
                principal_id=principal,
                session_id=record.session_id,
                run_id=run_id,
                status="cancelled",
            )
        return JSONResponse({"object": "run.deleted", "run_id": run_id, "status": "cancelled"})

    app.include_router(router)
