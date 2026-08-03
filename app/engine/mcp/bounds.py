"""Bounded MCP transport parsing (REQUIREMENTS.md MCP-08, phased per v2.5).

The pre-parse ``maxTransportMessageBytes`` cap is enforced on Streamable
HTTP and legacy SSE via the httpx client seam (ADK's
``httpx_client_factory`` / ``http_client`` injection points, verified in the
M0 STACK-02 spike). The stdio pre-parse cap is deferred until a google-adk
release supports the mcp 2.x ``Transport`` seam (REQUIREMENTS.md v2.5).
"""

from __future__ import annotations

from typing import Any


class TransportMessageTooLarge(Exception):
    """An inbound MCP message exceeded maxTransportMessageBytes (MCP-08)."""


class _BoundedByteStream:
    """httpx.AsyncByteStream that raises once accumulated bytes exceed the
    cap, before the SDK buffers/decodes a full message (MCP-08)."""

    def __init__(self, inner: Any, max_message_bytes: int) -> None:
        self._inner = inner
        self._max = max_message_bytes
        self._count = 0

    async def __aiter__(self):
        async for chunk in self._inner:
            self._count += len(chunk)
            if self._count > self._max:
                raise TransportMessageTooLarge("message exceeded byte cap")
            yield chunk

    async def aclose(self) -> None:
        await self._inner.aclose()

    async def aread(self) -> bytes:
        data = await self._inner.aread()
        self._count += len(data)
        if self._count > self._max:
            raise TransportMessageTooLarge("message exceeded byte cap")
        return data


def bounded_httpx_client_factory(max_message_bytes: int, base_factory: Any) -> Any:
    """Wrap an httpx client factory so every inbound response body is read
    with a byte cap before the SDK buffers/decodes a full message (MCP-08).

    The wrapper installs an httpx ``AsyncByteStream`` that raises
    :class:`TransportMessageTooLarge` once the accumulated bytes exceed the
    cap — the connection is then torn down and a bounded error recorded.
    """

    # _BoundedByteStream is module-level (avoids nested-generator confusion).

    def factory(headers=None, timeout=None, auth=None):
        import httpx

        client = base_factory(headers=headers, timeout=timeout, auth=auth)
        original_send = client.send

        async def bounded_send(request: httpx.Request, *args, **kwargs):
            response = await original_send(request, *args, **kwargs)
            if response.stream is not None:
                response.stream = _BoundedByteStream(response.stream, max_message_bytes)
            return response

        client.send = bounded_send  # type: ignore[method-assign]
        return client

    return factory


def validate_tool_metadata(name: str, description: str, schema: Any) -> str | None:
    """MCP-08 metadata caps; returns an exclusion reason or None."""
    if len(name.encode("utf-8")) > 128:
        return "tool name exceeds 128 bytes"
    if len(description or "") > 4096:
        return "tool description exceeds 4096 code points"
    if schema is not None:
        import json

        try:
            size = len(json.dumps(schema).encode("utf-8"))
        except (TypeError, ValueError):
            size = 0
        if size > 65536:
            return "tool input schema exceeds 65536 bytes"
    return None
