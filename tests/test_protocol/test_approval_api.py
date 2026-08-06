"""P3 client contract tests (HITL-03: approval_required + 202 + stateful-only)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
from google.adk.models import BaseLlm

from app.config.models import AgentConfig
from app.engine.agent import AppliedConfig, build_agent_component
from app.engine.events import ApprovalRequired, Done, TextDelta
from app.engine.mcp.manager import ServerManager
from app.engine.runner import AgentRunner
from app.protocol.app import create_app
from app.protocol.routes.chat import _stream
from app.storage.adk_adapter import AdkSessionService
from app.storage.memory import MemoryBackend

SPIKE = str(Path(__file__).resolve().parents[2] / "scripts" / "spike_mcp_server.py")


class CallerLlm(BaseLlm):
    model: str = "mock"
    turn: int = 0

    async def generate_content_async(self, llm_request, stream: bool = False):
        from google.adk.models.llm_response import LlmResponse
        from google.genai import types

        turn = self.turn
        self.turn += 1
        if turn == 0:
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            function_call=types.FunctionCall(
                                id="c1", name="echo", args={"text": "hi"}
                            )
                        )
                    ],
                )
            )
        else:
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="done")]))


def _approval_config() -> AgentConfig:
    return AgentConfig.model_validate(
        {
            "name": "agent",
            "engine": {"systemInstruction": "t"},
            "llm": {"provider": "gemini", "model": "mock"},
            "tools": {
                "mcpServers": [
                    {
                        "name": "echo",
                        "transport": "stdio",
                        "command": sys.executable,
                        "args": [SPIKE],
                    }
                ]
            },
            "approval": {"enabled": True, "tools": ["echo/*"], "timeoutSeconds": 300},
        }
    )


def _approval_acp_config() -> AgentConfig:
    """Like _approval_config() but with the ACP surface enabled (R-06)."""
    doc = _approval_config().model_dump(by_alias=True, mode="json")
    doc.setdefault("server", {})["protocols"] = {"acp": True}
    return AgentConfig.model_validate(doc)


async def _build_app(
    config: AgentConfig | None = None,
) -> tuple[httpx.ASGITransport, dict]:
    config = config or _approval_config()
    applied = AppliedConfig.from_config(config)
    component = build_agent_component(config)
    backend = MemoryBackend()
    mcp = ServerManager(applied, tool_targets=list(component.tool_targets))
    mcp.configure(config.tools.mcpServers)
    await mcp.start()
    window = float(__import__("os").environ.get("AGENT_TEST_MCP_CONNECT_SECONDS", "30"))
    deadline = __import__("time").monotonic() + window
    while __import__("time").monotonic() < deadline:
        if mcp.readiness() and component.agent.tools:
            break
        await asyncio.sleep(0.1)
    component.agent.model = CallerLlm()
    service = AdkSessionService(backend)
    runner = AgentRunner(
        applied,
        __import__("google.adk.runners", fromlist=["Runner"]).Runner(
            agent=component.agent, app_name="agent", session_service=service
        ),
        backend,
        app_name="agent",
        mcp=mcp,
    )
    components = {
        "applied": applied,
        "agent": component,
        "runner": runner,
        "mcp": mcp,
        "backend": backend,
        "session_service": service,
    }
    return httpx.ASGITransport(app=create_app(config, components, mode="standalone")), components


async def _request(
    transport: httpx.ASGITransport, method: str, url: str, json: dict | None = None
) -> httpx.Response:
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, url, json=json)


async def test_stateless_request_rejected_when_approval_enabled():
    transport, components = await _build_app()
    try:
        r = await _request(
            transport,
            "POST",
            "/v1/chat/completions",
            {"model": "mock", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "approval_session_required"
    finally:
        await components["mcp"].close()


async def test_non_streaming_returns_202_with_approval():
    transport, components = await _build_app()
    try:
        r = await _request(
            transport,
            "POST",
            "/v1/chat/completions",
            {
                "model": "mock",
                "session_id": "sess-approval-1",
                "messages": [{"role": "user", "content": "go"}],
            },
        )
        assert r.status_code == 202
        body = r.json()
        assert body["object"] == "run.pending_approval"
        assert body["approval_id"].startswith("appr-")
        assert body["tool"] == "echo"
        assert body["expires_at"]
        # the durable record exists (HITL-02)
        approval = await components["backend"].get_approval(
            agent_name="agent", principal_id="anonymous", approval_id=body["approval_id"]
        )
        assert approval is not None and approval.pending
    finally:
        await components["mcp"].close()


async def test_streaming_emits_approval_required_then_done():
    """HITL-03: SSE emits approval_required then [DONE] (the run detaches —
    the sole API-08a exception; it is not cancelled)."""
    config = _approval_config()

    class _FakeRequest:
        async def is_disconnected(self) -> bool:
            return False

    async def _fake_execute(events, _request):
        for event in events:
            yield event

    async def _drain(gen):
        out = []
        async for chunk in gen:
            out.append(chunk)
        return "".join(out)

    events = [
        ApprovalRequired(
            approval_id="appr-test",
            tool_name="echo",
            preview="{'text': '<redacted>'}",
            expires_at="2026-08-04T12:05:00+00:00",
        ),
        TextDelta(text="unexpected"),
        Done(finish_reason="stop"),
    ]
    runner = SimpleNamespace(execute=lambda r: _fake_execute(events, r))
    body = await _drain(
        _stream(
            runner,
            cast(Any, SimpleNamespace(session_id=None)),
            cast(Any, _FakeRequest()),
            "rid1",
            config,
            None,
            {},
            "p1",
            "agent",
        )
    )
    assert '"approval_required"' in body
    assert '"approval_id": "appr-test"' in body
    assert "data: [DONE]" in body
    # no finish chunk and no assistant text after the detach
    assert '"finish_reason"' not in body
    assert '"content": "unexpected"' not in body


async def test_approve_endpoint_resumes_and_completes():
    transport, components = await _build_app()
    try:
        r = await _request(
            transport,
            "POST",
            "/v1/chat/completions",
            {
                "model": "mock",
                "session_id": "sess-approval-2",
                "messages": [{"role": "user", "content": "go"}],
            },
        )
        assert r.status_code == 202
        approval_id = r.json()["approval_id"]
        run_id = r.json()["run_id"]
        d = await _request(
            transport,
            "POST",
            f"/v1/approvals/{approval_id}",
            {"decision": "approve", "reason": "ok"},
        )
        assert d.status_code == 200
        body = d.json()
        assert body["outcome"] == "approved"
        assert body["result_text"] == "done"
        assert body["status"] == "approved"
        # repeat decision -> stored outcome (HITL-04, no 409)
        d2 = await _request(
            transport, "POST", f"/v1/approvals/{approval_id}", {"decision": "approve"}
        )
        assert d2.status_code == 200
        # conflict -> 409 (HITL-04)
        d3 = await _request(transport, "POST", f"/v1/approvals/{approval_id}", {"decision": "deny"})
        assert d3.status_code == 409
        # the run is terminal and visible via GET /v1/runs/{id}
        g = await _request(transport, "GET", f"/v1/runs/{run_id}")
        assert g.status_code == 200
        assert g.json()["run_id"] == run_id
    finally:
        await components["mcp"].close()


async def test_deny_endpoint_returns_denied():
    transport, components = await _build_app()
    try:
        r = await _request(
            transport,
            "POST",
            "/v1/chat/completions",
            {
                "model": "mock",
                "session_id": "sess-approval-3",
                "messages": [{"role": "user", "content": "go"}],
            },
        )
        assert r.status_code == 202
        approval_id = r.json()["approval_id"]
        d = await _request(
            transport,
            "POST",
            f"/v1/approvals/{approval_id}",
            {"decision": "deny", "reason": "not now"},
        )
        assert d.status_code == 200
        assert d.json()["outcome"] == "denied"
        # the tool never ran: the denied run produced no tool result text
        assert d.json()["result_text"] == ""
        # the pending list is empty for the session now
        lst = await _request(
            transport,
            "GET",
            "/v1/approvals?session_id=sess-approval-3",
        )
        assert lst.status_code == 200
        assert lst.json()["approvals"] == []
    finally:
        await components["mcp"].close()


async def test_delete_run_cancels_pending_approval():
    transport, components = await _build_app()
    try:
        r = await _request(
            transport,
            "POST",
            "/v1/chat/completions",
            {
                "model": "mock",
                "session_id": "sess-approval-4",
                "messages": [{"role": "user", "content": "go"}],
            },
        )
        assert r.status_code == 202
        approval_id = r.json()["approval_id"]
        run_id = r.json()["run_id"]
        d = await _request(transport, "DELETE", f"/v1/runs/{run_id}")
        assert d.status_code == 200
        rec = await components["backend"].get_approval(
            agent_name="agent", principal_id="anonymous", approval_id=approval_id
        )
        assert rec is not None and rec.status == "cancelled"
        # a late decision on the cancelled approval -> 409/410
        late = await _request(
            transport, "POST", f"/v1/approvals/{approval_id}", {"decision": "approve"}
        )
        assert late.status_code == 409
        # deleting again is idempotent
        d2 = await _request(transport, "DELETE", f"/v1/runs/{run_id}")
        assert d2.status_code == 200
    finally:
        await components["mcp"].close()


# -- R-06: ACP surface parity (approval-gated runs) ----------------------------


async def test_acp_stateless_rejected_when_approval_enabled():
    """R-06: POST /acp/runs applies the HITL-01 stateful guard like chat."""
    transport, components = await _build_app(_approval_acp_config())
    try:
        r = await _request(
            transport,
            "POST",
            "/acp/runs",
            {"message": {"role": "user", "content": "hi"}},
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "approval_session_required"
    finally:
        await components["mcp"].close()


async def test_acp_non_streaming_returns_202_with_approval():
    """R-06: an approval-paused ACP run returns the annex-shaped 202
    pending-approval response instead of a 500."""
    transport, components = await _build_app(_approval_acp_config())
    try:
        r = await _request(
            transport,
            "POST",
            "/acp/runs",
            {
                "message": {"role": "user", "content": "go"},
                "session_id": "sess-acp-1",
            },
        )
        assert r.status_code == 202
        body = r.json()
        assert body["object"] == "run.pending_approval"
        assert body["approval_id"].startswith("appr-")
        assert body["tool"] == "echo"
        assert body["session_id"] == "sess-acp-1"
        assert body["expires_at"]
        # the durable record exists (HITL-02)
        approval = await components["backend"].get_approval(
            agent_name="agent", principal_id="anonymous", approval_id=body["approval_id"]
        )
        assert approval is not None and approval.pending
    finally:
        await components["mcp"].close()


async def test_acp_streaming_emits_approval_required_then_done():
    """R-06: the ACP streaming surface detaches with approval_required then
    [DONE] (the same HITL-03 semantics as chat)."""
    transport, components = await _build_app(_approval_acp_config())
    try:
        async with (
            httpx.AsyncClient(transport=transport, base_url="http://test") as client,
            client.stream(
                "POST",
                "/acp/runs",
                json={
                    "message": {"role": "user", "content": "go"},
                    "session_id": "sess-acp-2",
                    "stream": True,
                },
            ) as resp,
        ):
            assert resp.status_code == 200
            chunks = [chunk.decode() async for chunk in resp.aiter_raw()]
        body = "".join(chunks)
        assert '"approval_required"' in body
        assert '"approval_id"' in body
        assert "data: [DONE]" in body
    finally:
        await components["mcp"].close()
