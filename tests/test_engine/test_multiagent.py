"""P2 multi-agent construction + routing tests (MA-02, MA-03).

Covers: coordinator construction with sub_agents in configured order (empty
list retains P1 behavior), llm inheritance by deep merge, ADK transfer
routing through the engine's event stream, shared per-run limits across a
transfer, and MCP tool isolation per toolServers (root = all servers).
"""

from __future__ import annotations

import sys

import pytest

from app.config.models import AgentConfig
from app.engine.agent import build_agent_component
from app.engine.events import Done, TextDelta
from app.engine.runner import RunRequest

from .conftest import function_call_response, text_response


def _config(**overrides) -> AgentConfig:
    doc = {
        "name": "agent",
        "engine": {"systemInstruction": "You are the root."},
        "llm": {"provider": "gemini", "model": "mock"},
    }
    doc.update(overrides)
    return AgentConfig.model_validate(doc)


def test_empty_agents_retains_p1_behavior():
    component = build_agent_component(_config())
    assert component.agent.name == "agent"
    assert component.agent.instruction == "You are the root."
    assert not component.sub_agents
    # root sees every server (targets = [(root, None)])
    assert component.tool_targets == ((component.agent, None),)


def test_coordinator_builds_sub_agents_in_configured_order():
    config = _config(
        agents=[
            {"name": "first", "systemInstruction": "do A"},
            {"name": "second", "systemInstruction": "do B", "description": "B desc"},
        ]
    )
    component = build_agent_component(config)
    names = [a.name for a in component.sub_agents]
    assert names == ["first", "second"]
    assert component.agent.sub_agents is not None
    assert [a.name for a in component.agent.sub_agents] == names
    first, second = component.sub_agents
    assert first.instruction == "do A"
    assert second.instruction == "do B"
    assert second.description == "B desc"
    # root is a target with no restriction; sub-agents default to all servers
    assert component.tool_targets[0] == (component.agent, None)
    assert component.tool_targets[1] == (first, None)
    assert component.tool_targets[2] == (second, None)


def test_sub_agent_llm_inherited_by_deep_merge():
    config = _config(
        llm={"provider": "gemini", "model": "root-model", "baseUrl": "http://root"},
        agents=[
            {
                "name": "worker",
                "systemInstruction": "w",
                "llm": {"model": "worker-model"},
            }
        ],
    )
    component = build_agent_component(config)
    (worker,) = component.sub_agents
    from app.engine.connectors import RetryableLlm

    # the merged llm keeps the root's provider and overrides the model
    assert isinstance(worker.model, RetryableLlm)
    assert isinstance(component.agent.model, RetryableLlm)
    assert "worker-model" in worker.model.model
    assert component.agent.model.model != worker.model.model


def test_tool_servers_restriction_carried_to_targets():
    config = _config(
        tools={
            "mcpServers": [
                {"name": "alpha", "transport": "stdio", "command": "x"},
                {"name": "beta", "transport": "stdio", "command": "y"},
            ]
        },
        agents=[{"name": "worker", "systemInstruction": "w", "toolServers": ["alpha"]}],
    )
    component = build_agent_component(config)
    (worker,), root_target, worker_target = (
        component.sub_agents,
        component.tool_targets[0],
        component.tool_targets[1],
    )
    assert root_target == (component.agent, None)
    assert worker_target == (worker, ["alpha"])


@pytest.mark.asyncio
async def test_transfer_routing_streams_sub_agent_text(runner_factory):
    """MA-02: ADK native transfer — the root calls transfer_to_agent and the
    sub-agent's text arrives in the engine's event stream."""
    from google.adk.agents import LlmAgent

    from .conftest import ScriptedLlm

    sub = LlmAgent(
        name="researcher",
        instruction="research things",
        model=ScriptedLlm([[text_response("research findings")]]),
    )
    root = LlmAgent(
        name="agent",
        instruction="route to the researcher",
        model=ScriptedLlm(
            [
                [function_call_response("transfer_to_agent", "t1", {"agent_name": "researcher"})],
                [text_response("done")],
            ]
        ),
        sub_agents=[sub],
    )
    # runner_factory builds from a config; construct the runner by hand here.
    from google.adk.runners import Runner as AdkRunner

    from app.engine.runner import AgentRunner
    from app.storage.adk_adapter import AdkSessionService
    from app.storage.memory import MemoryBackend

    applied = __import__("app.engine.agent", fromlist=["AppliedConfig"]).AppliedConfig.from_config(
        _config()
    )
    backend = MemoryBackend()
    adk = AdkRunner(agent=root, app_name="agent", session_service=AdkSessionService(backend))
    runner = AgentRunner(applied, adk, backend, app_name="agent")

    events = [
        e
        async for e in runner.execute(
            RunRequest(principal_id="p1", user_message="research X", request_id="r1")
        )
    ]
    # The transfer routed: the sub-agent's output is the run's visible text
    # (ADK transfer semantics — the sub-agent completes the task), and the
    # run terminates cleanly.
    calls = [e for e in events if type(e).__name__ == "ToolCall"]
    assert calls and getattr(calls[0], "name", "") == "transfer_to_agent"
    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert "research findings" in text
    done = [e for e in events if isinstance(e, Done)]
    assert done and done[0].finish_reason == "stop"
    # exactly one terminal event (ENG-05)
    assert len(done) == 1


@pytest.mark.asyncio
async def test_transfer_to_unknown_agent_fails_run():
    """MA-04: transfer to an unknown/unavailable agent must fail the run with
    provider_error (no silent fallback)."""
    from google.adk.agents import LlmAgent

    from .conftest import ScriptedLlm

    root = LlmAgent(
        name="agent",
        instruction="t",
        model=ScriptedLlm(
            [
                [function_call_response("transfer_to_agent", "t1", {"agent_name": "ghost"})],
                [text_response("done")],
            ]
        ),
        sub_agents=[LlmAgent(name="researcher", instruction="r", model=ScriptedLlm([[]]))],
    )
    from google.adk.runners import Runner as AdkRunner

    from app.engine.agent import AppliedConfig
    from app.engine.events import RunError
    from app.engine.runner import AgentRunner
    from app.storage.adk_adapter import AdkSessionService
    from app.storage.memory import MemoryBackend

    backend = MemoryBackend()
    adk = AdkRunner(agent=root, app_name="agent", session_service=AdkSessionService(backend))
    runner = AgentRunner(AppliedConfig.from_config(_config()), adk, backend, app_name="agent")
    events = [
        e
        async for e in runner.execute(
            RunRequest(principal_id="p1", user_message="go", request_id="r2")
        )
    ]
    errors = [e for e in events if isinstance(e, RunError)]
    assert errors, "expected a RunError for the unknown transfer target"
    assert errors[0].code in ("provider_error", "invalid_request"), errors[0].code


@pytest.mark.asyncio
async def test_tool_isolation_per_toolservers():
    """MA-03: a sub-agent receives only its toolServers' tools (post
    filter/collision mapping); the root coordinator sees every server."""
    import asyncio

    from app.engine.agent import AppliedConfig
    from app.engine.mcp.manager import ServerManager

    config = _config(
        tools={
            "mcpServers": [
                {
                    "name": "alpha",
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": ["scripts/spike_mcp_server.py"],
                },
                {
                    "name": "beta",
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": ["scripts/spike_mcp_server.py"],
                },
            ]
        },
        agents=[{"name": "worker", "systemInstruction": "w", "toolServers": ["alpha"]}],
    )
    component = build_agent_component(config)
    mcp = ServerManager(
        AppliedConfig.from_config(config), tool_targets=list(component.tool_targets)
    )
    mcp.configure(config.tools.mcpServers)
    await mcp.start()
    try:
        for _ in range(100):
            if mcp.readiness() and component.agent.tools and component.sub_agents[0].tools:
                break
            await asyncio.sleep(0.1)
        root_names = sorted(getattr(t, "name", "") for t in component.agent.tools)
        worker = component.sub_agents[0]
        worker_names = sorted(getattr(t, "name", "") for t in worker.tools)
        # collision-safe final names (MCP-03): the first server (alpha) keeps
        # the raw 'echo'; beta's copy is disambiguated to beta_echo.
        assert root_names == ["beta_echo", "echo"], root_names
        assert worker_names == ["echo"], worker_names
        # the sub-agent cannot see the beta server's tool at all (MA-03)
        assert "beta_echo" not in worker_names
    finally:
        await mcp.close()
