"""STACK-02 spike: McpToolset connection/cancellation lifecycle (mcp 1.29.0).

Proves the MCP toolset lifecycle (MCP-01 connect/disconnect, MCP-05 close)
works through public seams on the locked dependency combination. Run in the
scratch venv where mcp is pinned <2 (the combination google-adk declares):

    uv pip install --python /tmp/mcpspike "google-adk[mcp]==2.6.1"
    /tmp/mcpspike/Scripts/python scripts/spike_mcp_lifecycle.py
"""

import asyncio
import sys
from pathlib import Path

from google.adk.tools import McpToolset
from mcp import StdioServerParameters

SERVER = str(Path(__file__).parent / "spike_mcp_server.py")


async def main() -> None:
    toolset = McpToolset(
        connection_params=StdioServerParameters(
            command=sys.executable,
            args=[SERVER],
        )
    )

    # connect + discover (MCP-01): spawns the server, initializes the session,
    # and filters tools.
    tools = await toolset.get_tools()
    print("discovered tools:", [t.name for t in tools])
    assert any(t.name == "echo" for t in tools), "echo tool missing"

    # disconnect/close (MCP-05 ref-counted close).
    await toolset.close()
    print("LIFECYCLE-SPIKE-OK (stdio connect/discover/close)")


if __name__ == "__main__":
    asyncio.run(main())
