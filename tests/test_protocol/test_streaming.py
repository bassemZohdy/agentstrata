"""API-08a tests: bounded output queue + <=1 s run cancellation on client
disconnect or a full queue for ``slowConsumerSeconds``.

Drives the private ``_stream`` generator directly with a fake runner and a fake
request, so the backpressure/cancellation paths can be exercised without real
TCP semantics (the in-memory ASGI transport does not reproduce send backpressure).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.engine.events import Done, RunError, TextDelta
from app.engine.runner import RunRequest
from app.protocol.routes.chat import _stream

from .conftest import make_config


class _FakeRequest:
    """Stand-in for Starlette ``Request`` exposing only ``is_disconnected``."""

    def __init__(self, disconnected: bool = False) -> None:
        self._disconnected = disconnected

    async def is_disconnected(self) -> bool:
        return self._disconnected


def _runner(events: list, *, slow_before: int | None = None, delay: float = 0.0):
    """Return a fake runner whose ``execute`` yields ``events``.

    ``slow_before`` inserts an ``asyncio.sleep`` before the event at that index,
    so a run can still be in flight when a disconnect is reported.
    """

    async def execute(_request):
        for i, event in enumerate(events):
            if slow_before is not None and i == slow_before:
                await asyncio.sleep(10)
            if delay:
                await asyncio.sleep(delay)
            yield event

    return SimpleNamespace(execute=execute)


def _request() -> RunRequest:
    return RunRequest(principal_id="anon", user_message="hi", agent_name="agent")


async def _drain(gen) -> str:
    out: list[str] = []
    async for chunk in gen:
        out.append(chunk)
    return "".join(out)


class TestNormalStreaming:
    async def test_emits_text_usage_done(self):
        config = make_config()
        runner = _runner(
            [TextDelta(text="hello"), Done(usage={"input_tokens": 3, "output_tokens": 4})]
        )
        body = await _drain(
            _stream(runner, _request(), _FakeRequest(), "rid", config, None, {}, "anon", "agent")
        )
        assert "hello" in body
        assert "chat.completion.chunk" in body
        assert '"usage"' in body
        assert "data: [DONE]\n\n" in body
        # no cancellation on a clean run
        assert "x_agent_event" not in body

    async def test_run_error_emits_error_chunk(self):
        config = make_config()
        runner = _runner(
            [
                TextDelta(text="partial"),
                RunError(code="provider_error", message="boom"),
                Done(finish_reason="error", x_agent_status="provider_error"),
            ]
        )
        body = await _drain(
            _stream(runner, _request(), _FakeRequest(), "rid", config, None, {}, "anon", "agent")
        )
        assert '"code": "provider_error"' in body
        assert "data: [DONE]\n\n" in body


class TestSlowConsumerBackpressure:
    async def test_full_queue_for_slow_seconds_cancels_and_emits_error_event(self):
        # queue holds 2; slowConsumerSeconds = 1. The fake runner floods events
        # far faster than the consumer drains, so the queue stays full > 1 s.
        config = make_config(server={"streamQueueEvents": 2, "slowConsumerSeconds": 1})
        flood: list[object] = [TextDelta(text="x") for _ in range(50)]
        flood.append(Done(usage={"input_tokens": 1, "output_tokens": 2}))
        runner = _runner(flood)

        gen = _stream(runner, _request(), _FakeRequest(), "rid", config, None, {}, "anon", "agent")
        # Pull one chunk to start the run, then stop reading long enough that
        # the producer's bounded put times out (slowConsumerSeconds).
        first = await gen.__anext__()
        assert "chat.completion.chunk" in first
        await asyncio.sleep(1.4)  # > slowConsumerSeconds -> producer cancels the run

        body = first + await _drain(gen)

        # API-08a: one x_agent_event error chunk then [DONE]; status stays 200
        # (the route wrapper owns status) and no nonstandard finish reason.
        assert "x_agent_event" in body
        assert '"type": "error"' in body
        assert '"code": "agent_timeout"' in body
        assert "data: [DONE]\n\n" in body
        # A cancelled stream must not emit its usage chunk.
        assert '"usage"' not in body


class TestClientDisconnect:
    async def test_disconnect_requests_cancellation_within_1s(self):
        config = make_config()
        # Run is still in flight (10 s sleep before Done) when disconnect fires.
        runner = _runner([TextDelta(text="hi"), Done()], slow_before=1)
        start = asyncio.get_running_loop().time()
        body = await _drain(
            _stream(
                runner,
                _request(),
                _FakeRequest(disconnected=True),
                "rid",
                config,
                None,
                {},
                "anon",
                "agent",
            )
        )
        elapsed = asyncio.get_running_loop().time() - start
        # Cancellation must be requested within ~1 s, not after the 10 s run.
        assert elapsed < 1.5, f"disconnect cancellation took {elapsed:.2f}s"
        assert "x_agent_event" in body
        assert '"code": "agent_timeout"' in body
        assert "data: [DONE]\n\n" in body


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
