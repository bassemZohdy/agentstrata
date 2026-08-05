"""NFR-06 verification: the OpenAI-compatible surface works with the official
`openai` Python SDK client against the mock engine (no live network)."""

from __future__ import annotations

from typing import cast

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import OpenAI

from app.observability.otel import Observability
from app.protocol.app import create_app

from .conftest import build_components, make_config
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


@pytest.fixture()
def sdk_client_with_costs():
    """Like sdk_client but with costs.enabled so usage carries costUsd."""
    from app.config.models import AgentConfig

    config = make_config()
    doc = config.model_dump(by_alias=True, mode="json")
    doc["costs"] = {"enabled": True}
    config = AgentConfig.model_validate(doc)
    obs = Observability(config)
    components = build_components(config, obs)
    http = TestClient(create_app(config, components, mode="standalone"))
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

    def test_costUsd_extra_field_ignored_by_sdk(self, sdk_client_with_costs):
        """NFR-06: the extra usage.costUsd field must not break parsing in
        recent openai SDK versions — typed token fields decode normally and
        the extra is retained in model_extra (not part of the typed model),
        so a future SDK strict-mode change cannot silently break consumers.
        """
        response = sdk_client_with_costs.chat.completions.create(
            model="mock",
            messages=[{"role": "user", "content": "hi"}],
        )
        assert response.usage is not None
        assert response.usage.prompt_tokens == 0
        assert response.usage.total_tokens == 0
        assert response.usage.model_extra is not None
        assert response.usage.model_extra["costUsd"] == 0.0
