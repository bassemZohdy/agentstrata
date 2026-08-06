"""Tiny stdio MCP server exposing echo + count tools (for spike tests)."""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("spike-echo-server")


@mcp.tool()
def echo(text: str) -> str:
    """Echoes the input text back."""
    return f"echo:{text}"


@mcp.tool()
def count(text: str) -> int:
    """Counts the characters in the input text."""
    return len(text)


if __name__ == "__main__":
    mcp.run(transport="stdio")
