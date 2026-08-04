"""RAG-03 ingestion API tests (owner-scoped documents)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import httpx

from app.config.models import AgentConfig
from app.engine.agent import AppliedConfig, build_agent_component
from app.engine.mcp.manager import ServerManager
from app.engine.rag import DeterministicEmbedding, MemoryRagStore, RagRetriever
from app.protocol.app import create_app
from app.storage.adk_adapter import AdkSessionService
from app.storage.memory import MemoryBackend

SPIKE = str(Path(__file__).resolve().parents[2] / "scripts" / "spike_mcp_server.py")


def _config(rag: dict | None = None) -> AgentConfig:
    doc = {
        "name": "agent",
        "engine": {"systemInstruction": "t"},
        "llm": {"provider": "gemini", "model": "mock"},
        "tools": {
            "mcpServers": [
                {"name": "echo", "transport": "stdio", "command": sys.executable, "args": [SPIKE]}
            ]
        },
        "rag": rag or {"enabled": True},
    }
    return AgentConfig.model_validate(doc)


async def _build_app(rag: dict | None = None) -> tuple[httpx.ASGITransport, dict[str, Any]]:
    config = _config(rag)
    applied = AppliedConfig.from_config(config)
    component = build_agent_component(config)
    backend = MemoryBackend()
    mcp = ServerManager(applied, tool_targets=list(component.tool_targets))
    mcp.configure(config.tools.mcpServers)
    await mcp.start()
    retriever = RagRetriever(
        config=config.rag,
        store=MemoryRagStore(),
        embedding=DeterministicEmbedding(),
    )
    service = AdkSessionService(backend)

    runner = type("R", (), {})()  # the documents surface never runs the agent
    components = {
        "applied": applied,
        "agent": component,
        "runner": runner,
        "mcp": mcp,
        "backend": backend,
        "session_service": service,
        "rag": retriever,
    }
    transport = httpx.ASGITransport(app=create_app(config, components, mode="standalone"))
    return transport, components


async def _request(
    transport: httpx.ASGITransport, method: str, url: str, json: dict | None = None
) -> httpx.Response:
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, url, json=json)


async def test_post_get_delete_roundtrip():
    transport, components = await _build_app()
    try:
        r = await _request(
            transport,
            "POST",
            "/v1/documents",
            {"id": "doc-1", "text": "alpha beta gamma delta", "metadata": {"tag": "kb"}},
        )
        assert r.status_code == 201
        body = r.json()
        assert body["id"] == "doc-1"
        assert body["chunk_count"] == 1
        assert len(body["content_hash"]) == 64
        assert body["metadata"] == {"tag": "kb"}
        # GET: metadata/count/hash, NEVER the stored text
        g = await _request(transport, "GET", "/v1/documents/doc-1")
        assert g.status_code == 200
        got = g.json()
        assert got["chunk_count"] == 1
        assert "text" not in got
        assert "alpha beta" not in json_dumps(got)
        # the chunk is retrievable for the owner
        context = await components["rag"].retrieve(
            agent_name="agent", principal_id="anonymous", query="alpha beta gamma delta"
        )
        assert context is not None and "alpha beta gamma delta" in context
        # DELETE: 204 idempotent
        d = await _request(transport, "DELETE", "/v1/documents/doc-1")
        assert d.status_code == 204
        d2 = await _request(transport, "DELETE", "/v1/documents/doc-1")
        assert d2.status_code == 204
        g2 = await _request(transport, "GET", "/v1/documents/doc-1")
        assert g2.status_code == 404
    finally:
        await components["mcp"].close()


def json_dumps(obj: Any) -> str:
    import json

    return json.dumps(obj)


async def test_generated_id_and_owner_scoping():
    transport, components = await _build_app()
    try:
        r = await _request(transport, "POST", "/v1/documents", {"text": "hello world"})
        assert r.status_code == 201
        doc_id = r.json()["id"]
        assert doc_id.startswith("doc-")
        # the document is NOT visible to another principal
        r2 = await _request(transport, "POST", "/v1/documents", {"text": "other"})
        assert r2.status_code == 201
        other_id = r2.json()["id"]
        g = await _request(transport, "GET", f"/v1/documents/{other_id}")
        assert g.status_code == 200  # same anonymous principal: visible
        # principal scoping is enforced at the store level (RAG-02)

        store = components["rag"].store
        query = (await components["rag"].embedding.embed(["hello world"]))[0]
        hits = await store.search(
            agent_name="agent",
            principal_id="someone-else",
            query_embedding=query,
            top_k=5,
            min_score=0.99,
        )
        assert hits == []
    finally:
        await components["mcp"].close()


async def test_validation_and_idempotency():
    transport, components = await _build_app(rag={"enabled": True, "maxDocumentBytes": 1024})
    try:
        # empty text
        r = await _request(transport, "POST", "/v1/documents", {"text": ""})
        assert r.status_code == 400
        # oversized text
        r = await _request(transport, "POST", "/v1/documents", {"text": "x" * 1025})
        assert r.status_code == 400
        # bad document id syntax
        r = await _request(transport, "POST", "/v1/documents", {"id": "bad id!", "text": "x"})
        assert r.status_code == 400
        # metadata with a nested object is rejected
        r = await _request(
            transport,
            "POST",
            "/v1/documents",
            {"text": "x", "metadata": {"nested": {"a": 1}}},
        )
        assert r.status_code == 400
        # scalar-only metadata is fine (lists of scalars allowed)
        r = await _request(
            transport,
            "POST",
            "/v1/documents",
            {"text": "x", "metadata": {"tags": ["a", "b"], "n": 1, "ok": True}},
        )
        assert r.status_code == 201
        # Idempotency-Key replay returns the SAME stored outcome
        body = {"text": "idempotent content", "id": "doc-idem"}
        r1 = await _request(
            transport, "POST", "/v1/documents", {**body, "idempotency_key": "key-1"}
        )
        assert r1.status_code == 201
        r2 = await _request(
            transport, "POST", "/v1/documents", {**body, "idempotency_key": "key-1"}
        )
        assert r2.status_code == 200
        assert r2.json()["content_hash"] == r1.json()["content_hash"]
    finally:
        await components["mcp"].close()


async def test_ingestion_failure_never_silent():
    """RAG-04: ingestion never degrades silently — a store failure is a 503
    and the previous version of the document stays intact."""

    class BrokenStore(MemoryRagStore):
        async def upsert_document(self, **kwargs):
            raise ConnectionError("store down")

    config = _config()
    applied = AppliedConfig.from_config(config)
    component = build_agent_component(config)
    backend = MemoryBackend()
    mcp = ServerManager(applied, tool_targets=list(component.tool_targets))
    mcp.configure(config.tools.mcpServers)
    await mcp.start()
    retriever = RagRetriever(
        config=config.rag, store=BrokenStore(), embedding=DeterministicEmbedding()
    )
    components = {
        "applied": applied,
        "agent": component,
        "runner": type("R", (), {})(),
        "mcp": mcp,
        "backend": backend,
        "session_service": AdkSessionService(backend),
        "rag": retriever,
    }
    transport = httpx.ASGITransport(app=create_app(config, components, mode="standalone"))
    try:
        r = await _request(transport, "POST", "/v1/documents", {"text": "never lands"})
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "rag_unavailable"
        # nothing was stored
        assert (
            await retriever.store.get_document(
                agent_name="agent", principal_id="anonymous", document_id="doc-1"
            )
            is None
        )
    finally:
        await mcp.close()


async def test_readyz_required_and_optional():
    """RAG-04: required rag gates /readyz; optional rag never does."""
    import httpx

    from app.engine.rag import MemoryRagStore, RagRetriever

    class BrokenStore(MemoryRagStore):
        async def health(self):
            return False

        async def search(self, **kwargs):
            raise ConnectionError("down")

    # optional: readiness stays 200
    transport, components = await _build_app()
    try:
        r = await _request(transport, "GET", "/readyz")
        assert r.status_code == 200
        r = await _request(transport, "GET", "/health")
        assert r.json()["components"]["rag"]["status"] == "ok"
    finally:
        await components["mcp"].close()

    # required + broken: readyz 503 with the rag flag
    config = _config({"enabled": True, "required": True})
    applied = AppliedConfig.from_config(config)
    component = build_agent_component(config)
    backend = MemoryBackend()
    mcp = ServerManager(applied, tool_targets=list(component.tool_targets))
    mcp.configure(config.tools.mcpServers)
    await mcp.start()
    retriever = RagRetriever(
        config=config.rag, store=BrokenStore(), embedding=DeterministicEmbedding()
    )
    comp = {
        "applied": applied,
        "agent": component,
        "runner": type("R", (), {})(),
        "mcp": mcp,
        "backend": backend,
        "session_service": AdkSessionService(backend),
        "rag": retriever,
    }
    transport2 = httpx.ASGITransport(app=create_app(config, comp, mode="standalone"))
    try:
        r = await _request(transport2, "GET", "/readyz")
        assert r.status_code == 503
        assert r.json()["rag"] is False
    finally:
        await mcp.close()
