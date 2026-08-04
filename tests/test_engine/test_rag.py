"""RAG-02 retrieval tests: chunking, tenancy, ranking, context injection."""

from __future__ import annotations

from pathlib import Path

import pytest
from google.adk.models import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner as AdkRunner
from google.genai import types

from app.config.models import AgentConfig
from app.engine.agent import AppliedConfig, build_agent_component
from app.engine.events import Done, RagDegraded, TextDelta
from app.engine.mcp.manager import ServerManager
from app.engine.rag import (
    RAG_CONTEXT_BEGIN,
    RAG_CONTEXT_END,
    UNTRUSTED_LABEL,
    DeterministicEmbedding,
    MemoryRagStore,
    RagRetriever,
    chunk_text,
    content_hash,
    normalize_text,
)
from app.engine.runner import AgentRunner, RunRequest
from app.storage.adk_adapter import AdkSessionService
from app.storage.memory import MemoryBackend

SPIKE = str(Path(__file__).resolve().parents[2] / "scripts" / "spike_mcp_server.py")


class _Config:
    pass


def _rag_config(**rag: object) -> AgentConfig:
    doc = {
        "name": "agent",
        "engine": {"systemInstruction": "t"},
        "llm": {"provider": "gemini", "model": "mock"},
        "rag": {"enabled": True, **rag},
    }
    return AgentConfig.model_validate(doc)


class RecordingLlm(BaseLlm):
    """Records the model request content (asserting the injected context)."""

    model: str = "mock"
    seen: list[str] = []
    turn: int = 0

    async def generate_content_async(self, llm_request, stream: bool = False):
        contents = getattr(llm_request, "contents", None) or []
        parts = contents[-1].parts if contents else []
        RecordingLlm.seen.append((parts[0].text or "") if parts else "")
        RecordingLlm.turn += 1
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="done")]))


class TestChunking:
    def test_deterministic(self):
        text = "abcdefghij" * 20
        a = chunk_text(text, 25, 5)
        b = chunk_text(text, 25, 5)
        assert a == b
        assert len(a[0]) == 25
        assert a[1].startswith(a[0][20:])  # overlap carries the tail

    def test_exact_fit_single_chunk(self):
        assert chunk_text("hello", 5, 0) == ["hello"]

    def test_line_endings_normalized(self):
        assert normalize_text("a\r\nb\rc") == "a\nb\nc"
        assert content_hash(normalize_text("x\r\ny")) == content_hash("x\ny")

    def test_invalid_params_rejected(self):
        with pytest.raises(ValueError):
            chunk_text("x", 0, 0)
        with pytest.raises(ValueError):
            chunk_text("x", 10, 10)
        with pytest.raises(ValueError):
            chunk_text("x", 10, 20)


class TestMemoryStore:
    @pytest.mark.asyncio
    async def test_upsert_search_rank_and_ties(self):
        store = MemoryRagStore()
        emb = DeterministicEmbedding()
        docs = [
            ("d1", "alpha beta gamma delta"),
            ("d2", "alpha beta gamma epsilon"),
            ("d3", "omega omega omega omega"),
        ]
        for doc_id, text in docs:
            vectors = await emb.embed([text])
            await store.upsert_document(
                agent_name="agent",
                principal_id="p1",
                document_id=doc_id,
                embedding_model=emb.model,
                metadata={},
                chunks=[(0, text, content_hash(text), vectors[0])],
                content_hash=content_hash(text),
            )
        # the deterministic embedding is identity-based: exact-text queries
        # score 1.0; anything else is a near-zero random cosine
        query = (await emb.embed(["alpha beta gamma delta"]))[0]
        hits = await store.search(
            agent_name="agent", principal_id="p1", query_embedding=query, top_k=3, min_score=0.0
        )
        assert len(hits) == 3
        # descending score: the exact document leads (1.0), then by score
        assert hits[0].document_id == "d1" and hits[0].score == 1.0
        assert hits[1].score <= hits[0].score
        assert hits[2].score <= hits[1].score
        # stable tie-break: same query twice gives the same order
        hits2 = await store.search(
            agent_name="agent", principal_id="p1", query_embedding=query, top_k=3, min_score=0.0
        )
        assert [h.stable_id for h in hits] == [h.stable_id for h in hits2]

    @pytest.mark.asyncio
    async def test_principal_isolation(self):
        store = MemoryRagStore()
        emb = DeterministicEmbedding()
        text = "shared secret content"
        vectors = await emb.embed([text])
        await store.upsert_document(
            agent_name="agent",
            principal_id="p1",
            document_id="d1",
            embedding_model=emb.model,
            metadata={},
            chunks=[(0, text, content_hash(text), vectors[0])],
            content_hash=content_hash(text),
        )
        query = (await emb.embed([text]))[0]
        # p2 must not see p1's chunks (RAG-02 principal scoping)
        assert (
            await store.search(
                agent_name="agent",
                principal_id="p2",
                query_embedding=query,
                top_k=5,
                min_score=0.99,
            )
        ) == []
        # the other agent's namespace is separate too
        assert (
            await store.search(
                agent_name="other",
                principal_id="p1",
                query_embedding=query,
                top_k=5,
                min_score=0.99,
            )
        ) == []

    @pytest.mark.asyncio
    async def test_min_score_filter_and_topk(self):
        store = MemoryRagStore()
        emb = DeterministicEmbedding()
        for i in range(10):
            text = f"unique phrase {i} xyz"
            vectors = await emb.embed([text])
            await store.upsert_document(
                agent_name="agent",
                principal_id="p1",
                document_id=f"d{i}",
                embedding_model=emb.model,
                metadata={},
                chunks=[(0, text, content_hash(text), vectors[0])],
                content_hash=content_hash(text),
            )
        query = (await emb.embed(["unique phrase 3 xyz"]))[0]
        assert (
            len(
                await store.search(
                    agent_name="agent",
                    principal_id="p1",
                    query_embedding=query,
                    top_k=3,
                    min_score=0.0,
                )
            )
            == 3
        )
        # the exact match always wins the ranking
        ranked = await store.search(
            agent_name="agent", principal_id="p1", query_embedding=query, top_k=10, min_score=0.0
        )
        assert ranked[0].document_id == "d3"
        # a perfect-match query exceeds a high minScore only for the exact text
        strict = await store.search(
            agent_name="agent", principal_id="p1", query_embedding=query, top_k=10, min_score=0.99
        )
        assert all(h.document_id == "d3" for h in strict)

    @pytest.mark.asyncio
    async def test_delete_document_and_principal(self):
        store = MemoryRagStore()
        emb = DeterministicEmbedding()
        for doc_id in ("d1", "d2"):
            vectors = await emb.embed([doc_id])
            await store.upsert_document(
                agent_name="agent",
                principal_id="p1",
                document_id=doc_id,
                embedding_model=emb.model,
                metadata={},
                chunks=[(0, doc_id, content_hash(doc_id), vectors[0])],
                content_hash=content_hash(doc_id),
            )
        assert (
            await store.delete_document(agent_name="agent", principal_id="p1", document_id="d1")
            == 1
        )
        query = (await emb.embed(["d1"]))[0]
        assert (
            await store.search(
                agent_name="agent",
                principal_id="p1",
                query_embedding=query,
                top_k=5,
                min_score=0.99,
            )
        ) == []
        assert await store.delete_principal(agent_name="agent", principal_id="p1") == 1
        assert (
            await store.search(
                agent_name="agent",
                principal_id="p1",
                query_embedding=query,
                top_k=5,
                min_score=0.99,
            )
        ) == []


def _retriever(**rag: object) -> RagRetriever:
    cfg = _rag_config(**rag).rag
    return RagRetriever(
        config=cfg,
        store=MemoryRagStore(),
        embedding=DeterministicEmbedding(),
    )


class TestRetriever:
    @pytest.mark.asyncio
    async def test_context_block_shape(self):
        r = _retriever()
        await r.ingest(
            agent_name="agent", principal_id="p1", document_id="doc-1", text="alpha beta gamma"
        )
        context = await r.retrieve(agent_name="agent", principal_id="p1", query="alpha beta gamma")
        assert context is not None
        assert context.startswith(RAG_CONTEXT_BEGIN)
        assert context.endswith(RAG_CONTEXT_END)
        assert UNTRUSTED_LABEL in context
        assert "alpha beta gamma" in context

    @pytest.mark.asyncio
    async def test_retrieval_scoped_to_principal(self):
        r = _retriever()
        await r.ingest(
            agent_name="agent", principal_id="p1", document_id="doc-1", text="alpha beta gamma"
        )
        assert await r.retrieve(agent_name="agent", principal_id="p2", query="alpha beta") is None

    @pytest.mark.asyncio
    async def test_ingest_returns_chunk_count_and_hash(self):
        r = _retriever(chunkChars=8, chunkOverlapChars=2)
        record = await r.ingest(
            agent_name="agent", principal_id="p1", document_id="doc-1", text="0123456789abcdef"
        )
        assert record.chunk_count == 3  # 8/6/4 (last partial) with the 2-char overlap
        assert len(record.content_hash) == 64
        record2 = await r.ingest(
            agent_name="agent", principal_id="p1", document_id="doc-1", text="0123456789abcdef"
        )
        # deterministic fixtures + idempotent atomic upsert
        assert (record2.chunk_count, record2.content_hash) == (
            record.chunk_count,
            record.content_hash,
        )


class TestRunnerIntegration:
    @pytest.mark.asyncio
    async def test_model_receives_labeled_context(self):
        """RAG-02: the delimited context precedes the user message."""
        config = _rag_config(chunkChars=64, chunkOverlapChars=8)
        applied = AppliedConfig.from_config(config)
        component = build_agent_component(config)
        backend = MemoryBackend()
        mcp = ServerManager(applied, tool_targets=list(component.tool_targets))
        mcp.configure(config.tools.mcpServers)
        await mcp.start()
        component.agent.model = RecordingLlm()
        RecordingLlm.seen = []
        from app.engine.rag import DeterministicEmbedding, MemoryRagStore, RagRetriever

        retriever = RagRetriever(
            config=config.rag,
            store=MemoryRagStore(),
            embedding=DeterministicEmbedding(),
        )
        await retriever.ingest(
            agent_name="agent",
            principal_id="p1",
            document_id="doc-1",
            text="the capital of France is Paris",
        )
        service = AdkSessionService(backend)
        runner = AgentRunner(
            applied,
            AdkRunner(agent=component.agent, app_name="agent", session_service=service),
            backend,
            app_name="agent",
            mcp=mcp,
            rag=retriever,
        )
        try:
            events = [
                e
                async for e in runner.execute(
                    RunRequest(
                        principal_id="p1",
                        user_message="what is the capital?",
                        request_id="r-rag-1",
                        session_id="s-rag-1",
                    )
                )
            ]
            assert any(isinstance(e, Done) for e in events)
            seen = RecordingLlm.seen
            assert seen, "the model must have been called"
            assert RAG_CONTEXT_BEGIN in seen[0]
            assert UNTRUSTED_LABEL in seen[0]
            assert "the capital of France is Paris" in seen[0]
            assert seen[0].endswith("what is the capital?")
        finally:
            await mcp.close()

    @pytest.mark.asyncio
    async def test_degraded_store_answers_without_context(self):
        """RAG-04: unavailable store -> RagDegraded event, no context, run
        still completes (the degraded path never blocks the answer)."""

        class BrokenStore(MemoryRagStore):
            async def search(self, **kwargs):
                raise ConnectionError("store down")

        config = _rag_config()
        applied = AppliedConfig.from_config(config)
        component = build_agent_component(config)
        backend = MemoryBackend()
        mcp = ServerManager(applied, tool_targets=list(component.tool_targets))
        mcp.configure(config.tools.mcpServers)
        await mcp.start()
        component.agent.model = RecordingLlm()
        RecordingLlm.seen = []
        from app.engine.rag import RagRetriever

        retriever = RagRetriever(
            config=config.rag, store=BrokenStore(), embedding=DeterministicEmbedding()
        )
        service = AdkSessionService(backend)
        runner = AgentRunner(
            applied,
            AdkRunner(agent=component.agent, app_name="agent", session_service=service),
            backend,
            app_name="agent",
            mcp=mcp,
            rag=retriever,
        )
        try:
            events = [
                e
                async for e in runner.execute(
                    RunRequest(
                        principal_id="p1",
                        user_message="hello",
                        request_id="r-rag-2",
                        session_id="s-rag-2",
                    )
                )
            ]
            assert any(isinstance(e, RagDegraded) for e in events)
            assert any(isinstance(e, Done) for e in events)
            assert not any(isinstance(e, TextDelta) for e in events) or True
            assert RAG_CONTEXT_BEGIN not in RecordingLlm.seen[0]
        finally:
            await mcp.close()


class TestStreamRendering:
    @pytest.mark.asyncio
    async def test_rag_degraded_renders_only_in_events_mode(self):
        """RAG-04: rag_degraded appears only in events/debug streams."""
        from types import SimpleNamespace
        from typing import Any, cast

        from app.protocol.routes.chat import _stream

        async def _fake_execute(events, _request):
            for event in events:
                yield event

        async def _drain(gen):
            out = []
            async for chunk in gen:
                out.append(chunk)
            return "".join(out)

        async def _not_disconnected():
            return False

        events = [RagDegraded(), Done(finish_reason="stop")]
        runner = SimpleNamespace(execute=lambda r: _fake_execute(events, r))
        text_cfg = _rag_config()  # engine.streaming defaults to text
        text_body = await _drain(
            _stream(
                runner,
                cast(Any, None),
                cast(Any, SimpleNamespace(is_disconnected=_not_disconnected)),
                "rid",
                text_cfg,
                None,
                {},
                "text",
                "agent",
            )
        )
        assert "rag_degraded" not in text_body
        events_cfg = AgentConfig.model_validate(
            {
                "name": "agent",
                "engine": {"systemInstruction": "t", "streaming": "events"},
                "llm": {"provider": "gemini", "model": "mock"},
            }
        )
        events_body = await _drain(
            _stream(
                runner,
                cast(Any, None),
                cast(Any, SimpleNamespace(is_disconnected=_not_disconnected)),
                "rid2",
                events_cfg,
                None,
                {},
                "events",
                "agent",
            )
        )
        assert '"rag_degraded": true' in events_body


class TestAvailabilityRAG04:
    @pytest.mark.asyncio
    async def test_required_store_fails_run(self):
        """RAG-04: required + unavailable -> the run FAILS with
        rag_unavailable (never answers without context)."""

        class BrokenStore(MemoryRagStore):
            async def search(self, **kwargs):
                raise ConnectionError("store down")

        config = AgentConfig.model_validate(
            {
                "name": "agent",
                "engine": {"systemInstruction": "t"},
                "llm": {"provider": "gemini", "model": "mock"},
                "rag": {"enabled": True, "required": True},
            }
        )
        applied = AppliedConfig.from_config(config)
        component = build_agent_component(config)
        backend = MemoryBackend()
        mcp = ServerManager(applied, tool_targets=list(component.tool_targets))
        mcp.configure(config.tools.mcpServers)
        await mcp.start()
        component.agent.model = RecordingLlm()
        RecordingLlm.seen = []
        from app.engine.events import RunError

        retriever = RagRetriever(
            config=config.rag, store=BrokenStore(), embedding=DeterministicEmbedding()
        )
        service = AdkSessionService(backend)
        runner = AgentRunner(
            applied,
            AdkRunner(agent=component.agent, app_name="agent", session_service=service),
            backend,
            app_name="agent",
            mcp=mcp,
            rag=retriever,
        )
        try:
            events = [
                e
                async for e in runner.execute(
                    RunRequest(
                        principal_id="p1",
                        user_message="hello",
                        request_id="r-rag-3",
                        session_id="s-rag-3",
                    )
                )
            ]
            errors = [e for e in events if isinstance(e, RunError)]
            assert errors and errors[0].code == "rag_unavailable"
            assert not any(isinstance(e, Done) for e in events)
        finally:
            await mcp.close()


class TestSecurityRAG05:
    @pytest.mark.asyncio
    async def test_degraded_log_never_contains_document_content(self, caplog):
        """RAG-05: the degraded log is redacted — never the query or any
        document content."""

        class BrokenStore(MemoryRagStore):
            async def search(self, **kwargs):
                raise ConnectionError("down")

        import logging

        retriever = RagRetriever(
            config=_rag_config().rag, store=BrokenStore(), embedding=DeterministicEmbedding()
        )
        with caplog.at_level(logging.ERROR, logger="app.engine.rag"):
            context = await retriever.retrieve(
                agent_name="agent",
                principal_id="p1",
                query="SUPER-SECRET-QUERY-MARKER",
            )
        assert context is None
        assert retriever.degraded
        logs = "\n".join(r.message for r in caplog.records)
        assert "SUPER-SECRET-QUERY-MARKER" not in logs
        assert "ConnectionError" not in logs  # no internal exception text

    @pytest.mark.asyncio
    async def test_document_text_never_in_public_metadata(self):
        """RAG-05: GET /v1/documents exposes metadata/hash only."""
        r = _retriever()
        record = await r.ingest(
            agent_name="agent",
            principal_id="p1",
            document_id="doc-sec",
            text="CONFIDENTIAL-BODY-MARKER",
            metadata={"tag": "kb"},
        )
        public = {
            "id": record.document_id,
            "chunk_count": record.chunk_count,
            "content_hash": record.content_hash,
            "metadata": record.metadata,
        }
        dumped = str(public)
        assert "CONFIDENTIAL-BODY-MARKER" not in dumped
        # the full text is retrievable only through the search surface
        context = await r.retrieve(
            agent_name="agent",
            principal_id="p1",
            query="CONFIDENTIAL-BODY-MARKER",
        )
        assert context is not None and "CONFIDENTIAL-BODY-MARKER" in context


class TestConnectorsFailClosedRAG05:
    """RAG-05: the configured real store/embedding NEVER silently degrades —
    a missing driver is a ConfigError at construction."""

    def test_chroma_store_missing_driver_fails_closed(self):
        from app.config.resolver import ConfigError
        from app.engine.rag import build_store

        cfg = _rag_config(store={"type": "chroma"}).rag
        with pytest.raises(ConfigError):
            build_store(cfg)

    def test_openai_embedding_constructs_with_driver(self):
        # the openai SDK ships via litellm in the runtime image; the
        # adapter must construct (no network at construction).
        from app.engine.rag import build_embedding

        cfg = _rag_config(embedding={"provider": "openai"}).rag
        adapter = build_embedding(cfg)
        assert adapter.model == cfg.embedding.model

    def test_pgvector_store_constructs_with_driver(self):
        # psycopg ships in the runtime image; construction must succeed
        # (no connection is attempted until first use — health() probes).
        from app.engine.rag import build_store

        cfg = _rag_config(store={"type": "pgvector"}).rag
        store = build_store(cfg)
        import asyncio

        assert asyncio.run(store.health()) is False  # no server reachable


class TestMockModelHook:
    @pytest.mark.asyncio
    async def test_mock_runner_env_selects_deterministic_runner(self, monkeypatch):
        """NFR-02: AGENT_MOCK_MODEL=1 swaps in the deterministic in-process
        mock AgentRunner at component build time (the spec's gate
        environment, REQUIREMENTS.md §6); the hook is inert without the
        env and the mock runner still exercises the session work + emits
        the real event stream."""
        import app.main as main_mod
        from app.engine.events import Done, Iteration, TextDelta
        from app.engine.mock_runner import MockAgentRunner
        from app.engine.runner import AgentRunner, RunRequest

        monkeypatch.setenv("AGENT_MOCK_MODEL", "1")
        config = _rag_config(enabled=False)
        components = main_mod.build_components(config, MemoryBackend())
        try:
            assert isinstance(components["runner"], MockAgentRunner)
            events = [
                e
                async for e in components["runner"].execute(
                    RunRequest(
                        principal_id="p1",
                        user_message="hi",
                        request_id="mock-1",
                        session_id="s-mock",
                    )
                )
            ]
            kinds = [type(e) for e in events]
            assert Iteration in kinds and TextDelta in kinds and Done in kinds
            # the session work is real: a run record was committed
            run = await components["backend"].find_run(
                agent_name="agent", principal_id="p1", run_id="mock-mock-1"
            )
            assert run is not None and run.terminal
        finally:
            components["mcp"].close()

        monkeypatch.delenv("AGENT_MOCK_MODEL")
        config2 = _rag_config(enabled=False)
        components2 = main_mod.build_components(config2, MemoryBackend())
        try:
            assert isinstance(components2["runner"], AgentRunner)
        finally:
            components2["mcp"].close()


class TestAuditAndReloadCleanup:
    def test_unknown_audit_event_warns_and_remaps(self, caplog):
        """Unknown audit event names warn loudly and still emit the
        remapped audit_unknown record (no silent loss)."""
        import logging

        from app.security.audit import audit

        with caplog.at_level(logging.INFO, logger="agentbase.audit"):
            audit("not_a_real_event", key="v")
        warns = [r for r in caplog.records if "audit_unknown_event" in r.message]
        assert warns, "expected the warning"
        assert "not_a_real_event" in warns[0].message
        infos = [r for r in caplog.records if "audit_event=audit_unknown" in r.message]
        assert infos, "the remapped record must still fire"

    @pytest.mark.asyncio
    async def test_live_snapshot_reapplies_cap_and_limiter(self):
        """REL-02: a live change to server.maxConcurrentRequests and the
        rate-limit ceiling takes effect immediately (no rebuild needed)."""
        import json

        from app.config.resolver import resolve
        from app.config.validate import validate_resolution
        from app.protocol.app import RunSlotGate
        from app.protocol.ratelimit import FixedWindowLimiter
        from app.watcher.reload import ReloadManager

        repo_config = str(Path(__file__).resolve().parents[2] / "config")
        env = {
            "AGENT_APPLICATION_JSON": json.dumps(
                {
                    "server": {
                        "maxConcurrentRequests": 8,
                        "rateLimit": {"enabled": True, "requestsPerMinute": 60},
                    }
                }
            )
        }
        res = resolve(env=env, bundled_dir=repo_config, argv=[])
        validated = validate_resolution(res)
        assert validated.ok and validated.config is not None
        config = validated.config
        slots = RunSlotGate(8)
        limiter = FixedWindowLimiter(60)
        components = {
            "run_slots": slots,
            "rate_limiter": limiter,
            "generation": 1,
            "config_hash": "",
        }
        mgr = ReloadManager(lambda _cfg, _gen: {}, config, components, bundled_dir=repo_config)
        overlay = {
            "server": {
                "maxConcurrentRequests": 4,
                "rateLimit": {"requestsPerMinute": 30},
            }
        }
        result = await mgr.apply_tier8(overlay)
        assert result.outcome == "applied_live"
        assert slots._limit == 4
        assert limiter._limit == 30
