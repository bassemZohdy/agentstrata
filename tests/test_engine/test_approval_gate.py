"""P3 approval gate tests (HITL-02: durable checkpoint before side effects)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from google.adk.models import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner as AdkRunner
from google.genai import types

from app.config.models import AgentConfig
from app.engine.agent import AppliedConfig, build_agent_component
from app.engine.events import ApprovalRequired, Done, TextDelta, ToolResult
from app.engine.mcp.manager import ServerManager
from app.engine.runner import AgentRunner, RunRequest
from app.storage.adk_adapter import AdkSessionService
from app.storage.memory import MemoryBackend

SPIKE = str(Path(__file__).resolve().parents[2] / "scripts" / "spike_mcp_server.py")


class CallerLlm(BaseLlm):
    """Calls the echo tool once, then closes with text."""

    model: str = "mock"

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False):
        yield LlmResponse(
            content=types.Content(
                role="model",
                parts=[
                    types.Part(
                        function_call=types.FunctionCall(id="c1", name="echo", args={"text": "hi"})
                    )
                ],
            )
        )
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="done")]))


def _config(approval: dict | None = None) -> AgentConfig:
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
            "approval": approval or {"enabled": False},
        }
    )


@pytest.mark.asyncio
async def test_matched_tool_pauses_run_without_executing():
    """HITL-02: with approval enabled for echo/*, the run pauses BEFORE the
    tool executes: ApprovalRequired flows, no ToolResult, the approval record
    carries the protected checkpoint, and the run record is awaiting_approval."""
    config = _config(approval={"enabled": True, "tools": ["echo/*"], "timeoutSeconds": 300})
    applied = AppliedConfig.from_config(config)
    component = build_agent_component(config)
    backend = MemoryBackend()
    mcp = ServerManager(applied, tool_targets=list(component.tool_targets))
    mcp.configure(config.tools.mcpServers)
    await mcp.start()
    service = AdkSessionService(backend)
    runner = AgentRunner(
        applied,
        AdkRunner(agent=component.agent, app_name="agent", session_service=service),
        backend,
        app_name="agent",
        mcp=mcp,
    )
    # use the caller model instead of the keyless gemini connector
    component.agent.model = CallerLlm()
    # wait for the MCP connect so the tool is attached
    window = float(__import__("os").environ.get("AGENT_TEST_MCP_CONNECT_SECONDS", "30"))
    deadline = __import__("time").monotonic() + window
    while __import__("time").monotonic() < deadline:
        if mcp.readiness() and component.agent.tools:
            break
        await asyncio.sleep(0.1)
        await asyncio.sleep(0.1)

    req = RunRequest(principal_id="p1", user_message="go", request_id="r-appr-1")
    events = [e async for e in runner.execute(req)]

    approvals_required = [e for e in events if isinstance(e, ApprovalRequired)]
    assert approvals_required, "expected the run to pause for approval"
    assert not any(isinstance(e, ToolResult) for e in events), "tool must not execute"
    assert not any(isinstance(e, Done) for e in events), "no terminal event while paused"
    assert not any(isinstance(e, TextDelta) for e in events), "no assistant text while paused"

    # the durable record exists with the protected checkpoint
    approval = await backend.get_approval(
        agent_name="agent", principal_id="p1", approval_id=approvals_required[0].approval_id
    )
    assert approval is not None and approval.pending
    assert approval.server_name == "echo" and approval.raw_tool_name == "echo"
    assert approval.checkpoint["tool_call_id"] == "c1"
    assert approval.checkpoint["args"] == {"text": "hi"}
    # public metadata is the hash + preview only
    assert len(approval.args_hash) == 64
    assert '"text": "hi"' in approval.args_preview

    # the run record is awaiting_approval (non-terminal, durable)
    runs = await backend.list_runs(
        agent_name="agent", principal_id="p1", session_id=req.session_id or ""
    )
    assert runs and runs[-1].status == "awaiting_approval"
    await mcp.close()


@pytest.mark.asyncio
async def test_unmatched_tool_executes_normally():
    """With approval enabled but the tool NOT matching, the run proceeds."""
    config = _config(approval={"enabled": True, "tools": ["other/*"], "timeoutSeconds": 300})
    applied = AppliedConfig.from_config(config)
    component = build_agent_component(config)
    backend = MemoryBackend()
    mcp = ServerManager(applied, tool_targets=list(component.tool_targets))
    mcp.configure(config.tools.mcpServers)
    await mcp.start()
    service = AdkSessionService(backend)
    runner = AgentRunner(
        applied,
        AdkRunner(agent=component.agent, app_name="agent", session_service=service),
        backend,
        app_name="agent",
        mcp=mcp,
    )
    component.agent.model = CallerLlm()
    window = float(__import__("os").environ.get("AGENT_TEST_MCP_CONNECT_SECONDS", "30"))
    deadline = __import__("time").monotonic() + window
    while __import__("time").monotonic() < deadline:
        if mcp.readiness() and component.agent.tools:
            break
        await asyncio.sleep(0.1)
        await asyncio.sleep(0.1)

    events = [
        e
        async for e in runner.execute(
            RunRequest(principal_id="p1", user_message="go", request_id="r-appr-2")
        )
    ]
    assert not any(isinstance(e, ApprovalRequired) for e in events)
    done = [e for e in events if isinstance(e, Done)]
    assert done and done[0].finish_reason == "stop"
    await mcp.close()


@pytest.mark.asyncio
async def test_approval_disabled_never_gates():
    config = _config()  # approval disabled by default
    applied = AppliedConfig.from_config(config)
    component = build_agent_component(config)
    backend = MemoryBackend()
    mcp = ServerManager(applied, tool_targets=list(component.tool_targets))
    mcp.configure(config.tools.mcpServers)
    await mcp.start()
    service = AdkSessionService(backend)
    runner = AgentRunner(
        applied,
        AdkRunner(agent=component.agent, app_name="agent", session_service=service),
        backend,
        app_name="agent",
        mcp=mcp,
    )
    component.agent.model = CallerLlm()
    window = float(__import__("os").environ.get("AGENT_TEST_MCP_CONNECT_SECONDS", "30"))
    deadline = __import__("time").monotonic() + window
    while __import__("time").monotonic() < deadline:
        if mcp.readiness() and component.agent.tools:
            break
        await asyncio.sleep(0.1)
        await asyncio.sleep(0.1)

    events = [
        e
        async for e in runner.execute(
            RunRequest(principal_id="p1", user_message="go", request_id="r-appr-3")
        )
    ]
    assert not any(isinstance(e, ApprovalRequired) for e in events)
    await mcp.close()


@pytest.mark.asyncio
async def test_resume_approves_and_continues():
    """HITL-04: after approval, the run resumes exactly once from the
    checkpoint, the original tool-call ID is reused, and the conversation
    continues to a terminal event."""
    config = _config(approval={"enabled": True, "tools": ["echo/*"], "timeoutSeconds": 300})
    applied = AppliedConfig.from_config(config)
    component = build_agent_component(config)
    backend = MemoryBackend()
    mcp = ServerManager(applied, tool_targets=list(component.tool_targets))
    mcp.configure(config.tools.mcpServers)
    await mcp.start()
    service = AdkSessionService(backend)
    runner = AgentRunner(
        applied,
        AdkRunner(agent=component.agent, app_name="agent", session_service=service),
        backend,
        app_name="agent",
        mcp=mcp,
    )
    component.agent.model = CallerLlm()
    window = float(__import__("os").environ.get("AGENT_TEST_MCP_CONNECT_SECONDS", "30"))
    deadline = __import__("time").monotonic() + window
    while __import__("time").monotonic() < deadline:
        if mcp.readiness() and component.agent.tools:
            break
        await asyncio.sleep(0.1)

    req = RunRequest(principal_id="p1", user_message="go", request_id="r-appr-4")
    events = [e async for e in runner.execute(req)]
    paused = [e for e in events if isinstance(e, ApprovalRequired)]
    assert paused, "run must pause for approval"

    outcome = await runner.resume_approval(
        approval_id=paused[0].approval_id, principal_id="p1", decision="approved", reason="ok"
    )
    assert outcome is not None and outcome["status"] == "approved"
    resumed = outcome["events"]
    # the conversation continued to a terminal event
    done = [e for e in resumed if isinstance(e, Done)]
    assert done and done[0].finish_reason == "stop"
    text = "".join(e.text for e in resumed if isinstance(e, TextDelta))
    assert "done" in text

    # a second resume loses the race (first decision won, HITL-04)
    again = await runner.resume_approval(
        approval_id=paused[0].approval_id, principal_id="p1", decision="denied"
    )
    assert again is None
    await mcp.close()


@pytest.mark.asyncio
async def test_resume_deny_returns_denied():
    config = _config(approval={"enabled": True, "tools": ["echo/*"], "timeoutSeconds": 300})
    applied = AppliedConfig.from_config(config)
    component = build_agent_component(config)
    backend = MemoryBackend()
    mcp = ServerManager(applied, tool_targets=list(component.tool_targets))
    mcp.configure(config.tools.mcpServers)
    await mcp.start()
    service = AdkSessionService(backend)
    runner = AgentRunner(
        applied,
        AdkRunner(agent=component.agent, app_name="agent", session_service=service),
        backend,
        app_name="agent",
        mcp=mcp,
    )
    component.agent.model = CallerLlm()
    window = float(__import__("os").environ.get("AGENT_TEST_MCP_CONNECT_SECONDS", "30"))
    deadline = __import__("time").monotonic() + window
    while __import__("time").monotonic() < deadline:
        if mcp.readiness() and component.agent.tools:
            break
        await asyncio.sleep(0.1)

    req = RunRequest(principal_id="p1", user_message="go", request_id="r-appr-5")
    events = [e async for e in runner.execute(req)]
    paused = [e for e in events if isinstance(e, ApprovalRequired)]
    assert paused

    outcome = await runner.resume_approval(
        approval_id=paused[0].approval_id, principal_id="p1", decision="denied", reason="no"
    )
    assert outcome is not None and outcome["status"] == "denied"
    # the tool never executed
    approval = await backend.get_approval(
        agent_name="agent", principal_id="p1", approval_id=paused[0].approval_id
    )
    assert approval is not None and approval.status == "denied"
    await mcp.close()


async def _build_paused_runner(
    approval: dict, generation: int = 1
) -> tuple[AgentRunner, MemoryBackend, ServerManager, str]:
    """Build a runner whose first run pauses at the approval gate."""
    config = _config(approval=approval)
    applied = AppliedConfig.from_config(config, generation=generation)
    component = build_agent_component(config)
    backend = MemoryBackend()
    mcp = ServerManager(applied, tool_targets=list(component.tool_targets))
    mcp.configure(config.tools.mcpServers)
    await mcp.start()
    import os
    import time

    window = float(os.environ.get("AGENT_TEST_MCP_CONNECT_SECONDS", "30"))
    deadline = time.monotonic() + window
    while time.monotonic() < deadline:
        if mcp.readiness() and component.agent.tools:
            break
        await asyncio.sleep(0.1)
    component.agent.model = CallerLlm()
    service = AdkSessionService(backend)
    runner = AgentRunner(
        applied,
        AdkRunner(agent=component.agent, app_name="agent", session_service=service),
        backend,
        app_name="agent",
        mcp=mcp,
    )
    request = RunRequest(
        principal_id="p1",
        user_message="go",
        request_id="r1",
        session_id="s1",
        agent_name="agent",
    )
    events = [e async for e in runner.execute(request)]
    paused = next(e for e in events if isinstance(e, ApprovalRequired))
    return runner, backend, mcp, paused.approval_id


async def test_stale_approval_never_executes_tool():
    """HITL-05: after a config change, the pending approval terminates
    stale_approval and the tool MUST NOT execute."""
    runner, backend, mcp, approval_id = await _build_paused_runner(
        {"enabled": True, "tools": ["echo/*"], "timeoutSeconds": 300}
    )
    try:
        # simulate a reload: the runner now runs a NEW generation
        runner._applied = AppliedConfig.from_config(
            _config({"enabled": True, "tools": ["echo/*"], "timeoutSeconds": 300}),
            generation=2,
        )
        outcome = await runner.resume_approval(
            approval_id=approval_id, principal_id="p1", decision="approved"
        )
        assert outcome is not None and outcome["status"] == "stale_approval"
        record = await backend.get_approval(
            agent_name="agent", principal_id="p1", approval_id=approval_id
        )
        assert record is not None and record.status == "stale_approval"
        # the resumed run never happened and the tool never ran
        existing = await backend.list_runs(agent_name="agent", principal_id="p1", session_id="s1")
        assert not any(r.input.get("request_id") == f"resume-{approval_id}" for r in existing)
    finally:
        await mcp.close()


async def test_timeout_deny_finishes_run():
    """HITL-05: onTimeout deny — the reconciler sweep finishes the run
    denied; the tool never executes."""
    from datetime import timedelta

    from app.storage.model import utcnow

    runner, backend, mcp, approval_id = await _build_paused_runner(
        {"enabled": True, "tools": ["echo/*"], "timeoutSeconds": 5}
    )
    try:
        record = await backend.get_approval(
            agent_name="agent", principal_id="p1", approval_id=approval_id
        )
        assert record is not None
        # push the approval past its expiry (the sweep decides)
        counters = await runner.reconcile_pending(now=utcnow() + timedelta(seconds=60))
        assert counters["timed_out"] >= 1
        assert counters["deny"] >= 1
        final = await backend.get_approval(
            agent_name="agent", principal_id="p1", approval_id=approval_id
        )
        assert final is not None and final.status == "timed_out"
        run = await backend.find_run(agent_name="agent", principal_id="p1", run_id=record.run_id)
        assert run is not None and run.terminal
        assert run.outcome.get("approval") == "denied"
    finally:
        await mcp.close()


async def test_timeout_allow_executes_and_continues():
    """HITL-05: onTimeout allow — the reconciler resumes the paused run
    (the tool executes; the stale/cancellation checks still apply)."""
    from datetime import timedelta

    from app.storage.model import utcnow

    runner, backend, mcp, approval_id = await _build_paused_runner(
        {"enabled": True, "tools": ["echo/*"], "timeoutSeconds": 5, "onTimeout": "allow"}
    )
    try:
        counters = await runner.reconcile_pending(now=utcnow() + timedelta(seconds=60))
        assert counters["timed_out"] >= 1
        assert counters["allow"] >= 1
        # the resume run exists (exactly-once guard, matched by request_id)
        existing = await backend.list_runs(agent_name="agent", principal_id="p1", session_id="s1")
        resume = next(
            (r for r in existing if r.input.get("request_id") == f"resume-{approval_id}"),
            None,
        )
        assert resume is not None and resume.terminal
        # the approval is terminal (timed_out -> the resume was the allow)
        record = await backend.get_approval(
            agent_name="agent", principal_id="p1", approval_id=approval_id
        )
        assert record is not None and record.status == "timed_out"
        # a second reconcile does not re-execute (idempotent)
        counters2 = await runner.reconcile_pending(now=utcnow() + timedelta(seconds=120))
        assert counters2["allow"] == 0
    finally:
        await mcp.close()


async def test_decided_while_down_resumes_once():
    """HITL-05: an approval decided while the process was down (no resume
    record) is resumed exactly once by the reconciler."""
    runner, backend, mcp, approval_id = await _build_paused_runner(
        {"enabled": True, "tools": ["echo/*"], "timeoutSeconds": 300}
    )
    try:
        # decide WITHOUT resuming (as if a previous process died after the
        # decision was durable but before the resume ran)
        await backend.decide_approval(
            agent_name="agent",
            principal_id="p1",
            approval_id=approval_id,
            decision="approved",
            reason="decided while down",
        )
        counters = await runner.reconcile_pending()
        assert counters["resumed"] >= 1
        existing = await backend.list_runs(agent_name="agent", principal_id="p1", session_id="s1")
        resume = next(
            (r for r in existing if r.input.get("request_id") == f"resume-{approval_id}"),
            None,
        )
        assert resume is not None and resume.terminal
        # a second reconcile does not resume again
        counters2 = await runner.reconcile_pending()
        assert counters2["resumed"] == 0
    finally:
        await mcp.close()
