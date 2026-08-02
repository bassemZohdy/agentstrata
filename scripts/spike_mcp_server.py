"""Tiny stdio MCP server exposing one echo tool (for spike tests)."""


from mcp.server.fastmcp import FastMCP

mcp = FastMCP("spike-echo-server")


@mcp.tool()
def echo(text: str) -> str:
    """Echoes the input text back."""
    return f"echo:{text}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
