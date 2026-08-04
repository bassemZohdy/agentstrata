"""MCP ServerManager integration test against a real stdio MCP server
(MCP-01 connect/discover, MCP-02 readiness, MCP-05 close, §18: against the
official MCP SDK — not a hand-written fake)."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from app.config.models import AgentConfig
from app.engine.agent import AppliedConfig
from app.engine.mcp.manager import ServerManager, ServerState

SERVER_SCRIPT = str(Path(__file__).resolve().parents[2] / "scripts" / "spike_mcp_server.py")

# Readiness wait window (seconds). The native default is 10 s; slow emulated
# platforms (arm64 via QEMU: the stdio initialize handshake alone can exceed
# 10 s) raise it through AGENT_TEST_MCP_CONNECT_SECONDS — set by
# scripts/run-image-acceptance.sh. The loop breaks as soon as readiness
# flips, so a larger window costs nothing on fast platforms.
MCP_CONNECT_WINDOW = float(os.environ.get("AGENT_TEST_MCP_CONNECT_SECONDS", "10"))


def _config_with_servers(servers: list[dict]) -> AppliedConfig:
    config = AgentConfig.model_validate(
        {
            "name": "agent",
            "engine": {"systemInstruction": "t"},
            "llm": {"provider": "gemini", "model": "mock"},
            "tools": {"mcpServers": servers},
        }
    )
    return AppliedConfig.from_config(config)


class TestStdioIntegration:
    @pytest.mark.asyncio
    async def test_connect_discover_readiness_close(self):
        applied = _config_with_servers(
            [
                {
                    "name": "echo",
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [SERVER_SCRIPT],
                    "required": True,
                }
            ]
        )
        manager = ServerManager(applied)
        manager.configure(applied.config.tools.mcpServers)
        await manager.start()

        # readiness gates until the required server connects (MCP-02)
        deadline = time.monotonic() + MCP_CONNECT_WINDOW
        while time.monotonic() < deadline:
            if manager.readiness():
                break
            await asyncio_sleep(0.1)
        assert manager.readiness()

        handle = manager.handle("echo")
        assert handle is not None
        assert handle.state == ServerState.CONNECTED
        assert "echo" in handle.final_names
        assert handle.tools

        health = manager.health()
        assert health[0]["name"] == "echo"
        assert health[0]["status"] == "connected"

        await manager.close()

    @pytest.mark.asyncio
    async def test_required_server_blocks_readiness_until_up(self):
        # a bogus command keeps readiness false (MCP-02)
        applied = _config_with_servers(
            [
                {
                    "name": "bad",
                    "transport": "stdio",
                    "command": "/nonexistent/binary",
                    "required": True,
                }
            ]
        )
        manager = ServerManager(applied)
        manager.configure(applied.config.tools.mcpServers)
        await manager.start()
        # The bogus command keeps readiness false (MCP-02). ADK retries the
        # failed connect once internally (retry_on_errors), so the handle may
        # stay CONNECTING past a fixed 2 s window under load; poll within the
        # same configurable window as the connect test.
        deadline = time.monotonic() + MCP_CONNECT_WINDOW
        while time.monotonic() < deadline:
            if manager.readiness():
                break
            await asyncio_sleep(0.1)
        assert not manager.readiness()
        while time.monotonic() < deadline:
            handle = manager.handle("bad")
            if handle is not None and handle.state != ServerState.CONNECTING:
                break
            await asyncio_sleep(0.1)
        handle = manager.handle("bad")
        assert handle is not None and handle.state == ServerState.DISCONNECTED
        assert handle.last_error
        await manager.close()


async def asyncio_sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)
