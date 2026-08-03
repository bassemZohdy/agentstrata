"""NFR-06 verification: the OpenAI-compatible surface works with the official
`openai` Python SDK client against the mock engine (no live network)."""

from __future__ import annotations

from typing import cast

import httpx
import pytest
from openai import OpenAI

from .test_api import _client


@pytest.fixture()
def sdk_client():
    http = _client()
    http.__enter__()
    yield OpenAI(
        api_key="test",
        base_url="http://testserver/v1",
        http_client=cast(httpx.Client, http),
    )
    http.__exit__(None, None, None)


class TestOpenAiSdk:
    def test_chat_completions_non_streaming(self, sdk_client):
        response = sdk_client.chat.completions.create(
            model="mock",
            messages=[{"role": "user", "content": "hi"}],
        )
        assert response.object == "chat.completion"
        assert response.choices[0].message.role == "assistant"
        assert "hello from mock" in (response.choices[0].message.content or "")
        assert response.usage.total_tokens is not None

    def test_chat_completions_streaming(self, sdk_client):
        chunks = list(
            sdk_client.chat.completions.create(
                model="mock",
                messages=[{"role": "user", "content": "hi"}],
                stream=True,
            )
        )
        assert chunks
        text = "".join(c.choices[0].delta.content or "" for c in chunks if c.choices)
        assert "hello from mock" in text

    def test_models_list(self, sdk_client):
        models = sdk_client.models.list()
        ids = [m.id for m in models.data]
        assert "mock" in ids
