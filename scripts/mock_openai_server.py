#!/usr/bin/env python3
"""Local OpenAI-compatible mock for the image-based NFR-00 suite.

Serves /v1/chat/completions (non-streaming and SSE streaming) and /v1/models
so the runtime's LiteLLM bridge (`llm.provider: openai` + `baseUrl`) has a
deterministic model endpoint with no external network. Streaming responses
emit one delta every ``MOCK_DELTA_SECONDS`` for ``MOCK_HOLD_SECONDS``, then a
finish chunk and ``[DONE]`` — the hold keeps runs in flight for the NFR-03
concurrency probe and gives a steady ≥1 event/s cadence.

Run from the repo venv::

    python scripts/mock_openai_server.py [--port 18081]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import time
import uuid
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.responses import Response

app = FastAPI()

MODEL = "mock-model"


def _env_float(name: str, default: float) -> float:
    """Env float with a safe fallback for unset/invalid values."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


HOLD_SECONDS = _env_float("MOCK_HOLD_SECONDS", 0.3)
DELTA_SECONDS = _env_float("MOCK_DELTA_SECONDS", 0.25)


def _epoch() -> int:
    return time.time_ns() // 1_000_000_000


def _chunk(delta: dict, finish_reason: str | None) -> dict:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
        "object": "chat.completion.chunk",
        "created": _epoch(),
        "model": MODEL,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


@app.get("/v1/models")
async def models() -> JSONResponse:
    return JSONResponse(
        {
            "object": "list",
            "data": [{"id": MODEL, "object": "model", "created": 0, "owned_by": "mock"}],
        }
    )


async def _stream_sse() -> AsyncIterator[str]:
    # One delta every DELTA_SECONDS for HOLD_SECONDS, then finish + [DONE].
    chunks = max(math.ceil(HOLD_SECONDS / DELTA_SECONDS), 1)
    for _ in range(chunks):
        await asyncio.sleep(DELTA_SECONDS)
        yield f"data: {json.dumps(_chunk({'content': 'p'}, None))}\n\n"
    yield f"data: {json.dumps(_chunk({}, 'stop'))}\n\n"
    yield "data: [DONE]\n\n"


@app.post("/v1/chat/completions", response_model=None)
async def chat_completions(request: Request) -> Response:
    body = await request.json()
    if not body.get("stream", False):
        return JSONResponse(
            {
                "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
                "object": "chat.completion",
                "created": _epoch(),
                "model": MODEL,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "pong"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        )
    return StreamingResponse(
        _stream_sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store"},
    )


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18081)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
