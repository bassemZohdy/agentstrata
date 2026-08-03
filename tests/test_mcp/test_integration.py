"""MCP ServerManager integration test against a real stdio MCP server
(MCP-01 connect/discover, MCP-02 readiness, MCP-05 close, §18: against the
official MCP SDK — not a hand-written fake)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.config.models import AgentConfig
from app.engine.agent import AppliedConfig
from app.engine.mcp.manager import ServerManager, ServerState

SERVER_SCRIPT = str(Path(__file__).resolve().parents[2] / "scripts" / "spike_mcp_server.py")


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
        for _ in range(100):
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
        for _ in range(20):
            if manager.readiness():
                break
            await asyncio_sleep(0.1)
        assert not manager.readiness()
        handle = manager.handle("bad")
        assert handle is not None and handle.state == ServerState.DISCONNECTED
        assert handle.last_error
        await manager.close()


async def asyncio_sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)
