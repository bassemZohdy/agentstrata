"""RAG connector implementations: real driver adapters (P4, §15).

- ``_ChromaStore`` — full chromadb client implementation (upsert/search/
  get/delete/health) behind a lazy collection.
- ``_PgvectorStore`` — full psycopg implementation: a ``rag_chunks`` table
  with a pgvector ``embedding`` column, atomic upsert, ``<->`` cosine
  search, scoped deletes, and a live health probe.

``build_connector_store`` FAILS CLOSED when the configured store type's
driver is not installed (no silent degradation to the memory substitute —
that substitute exists for tests and offline dev only, per the ACC-01
deviation). The gemini/openai embedding adapters are likewise driver-
guarded and fail closed at construction.
"""

from __future__ import annotations

from typing import Any

from ..config.resolver import ConfigError

# The pgvector schema is created lazily on first use; the table keeps the
# RAG-02 chunk identity (agent/principal/doc/chunk/model/hash) and the
# document registry (metadata/count/hash) in one place.
_PGVECTOR_DDL = """
CREATE TABLE IF NOT EXISTS rag_chunks (
    agent_name      TEXT NOT NULL,
    principal_id    TEXT NOT NULL,
    document_id     TEXT NOT NULL,
    chunk_index     INT  NOT NULL,
    text            TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    embedding       vector(1536) NOT NULL,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    chunk_count     INT  NOT NULL DEFAULT 0,
    doc_hash        TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (agent_name, principal_id, document_id, chunk_index, embedding_model)
)
"""

_PGVECTOR_UPSERT = """
INSERT INTO rag_chunks
    (agent_name, principal_id, document_id, chunk_index, text, content_hash,
     embedding_model, embedding, metadata, chunk_count, doc_hash, updated_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector, %s::jsonb, %s, %s, now())
ON CONFLICT (agent_name, principal_id, document_id, chunk_index, embedding_model)
DO UPDATE SET text = EXCLUDED.text,
              content_hash = EXCLUDED.content_hash,
              embedding = EXCLUDED.embedding,
              metadata = EXCLUDED.metadata,
              chunk_count = EXCLUDED.chunk_count,
              doc_hash = EXCLUDED.doc_hash,
              updated_at = now()
"""

# Cosine distance (pgvector <->) maps to similarity 1 - distance.
_PGVECTOR_SEARCH = """
SELECT document_id, chunk_index, text, content_hash,
       1 - (embedding <-> %s::vector) AS score
  FROM rag_chunks
 WHERE agent_name = %s AND principal_id = %s AND embedding_model = %s
 ORDER BY embedding <-> %s::vector
 LIMIT %s
"""

_PGVECTOR_GET_DOCUMENT = """
SELECT document_id, chunk_count, doc_hash, metadata, embedding_model,
       created_at, updated_at
  FROM rag_chunks
 WHERE agent_name = %s AND principal_id = %s AND document_id = %s
 LIMIT 1
"""

_PGVECTOR_DELETE_DOCUMENT = """
DELETE FROM rag_chunks
 WHERE agent_name = %s AND principal_id = %s AND document_id = %s
"""

_PGVECTOR_DELETE_PRINCIPAL = """
DELETE FROM rag_chunks
 WHERE agent_name = %s AND principal_id = %s
"""


class GeminiEmbedding:
    """gemini embedding via google-genai (driver required at construction)."""

    def __init__(self, cfg: Any) -> None:
        try:
            from google import genai  # noqa: F401  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ConfigError(
                "rag.embedding.provider gemini requires the google-genai package "
                "(present in the shipped image); install it to use this provider"
            ) from exc
        self.model = cfg.model
        self._api_key_env = cfg.apiKeyEnv
        self._api_key_file = cfg.apiKeyFile
        self._client: Any = None

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self._client is None:
            from google import genai  # type: ignore[import-untyped]

            self._client = genai.Client(api_key=self._resolve_key())
        result = await self._client.aio.models.embed_content(model=self.model, contents=texts)
        return [e.values for e in result.embeddings]

    def _resolve_key(self) -> str:
        if self._api_key_file:
            with open(self._api_key_file, encoding="utf-8") as fh:
                return fh.read().strip()
        if self._api_key_env:
            import os

            return os.environ.get(self._api_key_env, "")
        return ""


class OpenAIEmbedding:
    """openai embedding via the openai SDK (driver required at construction)."""

    def __init__(self, cfg: Any) -> None:
        try:
            import openai  # type: ignore[import-not-found]  # noqa: F401
        except ImportError as exc:
            raise ConfigError(
                "rag.embedding.provider openai requires the openai package; "
                "install it to use this provider"
            ) from exc
        self.model = cfg.model
        self._api_key_env = cfg.apiKeyEnv
        self._api_key_file = cfg.apiKeyFile
        self._client: Any = None

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self._client is None:
            import openai  # type: ignore[import-not-found]

            self._client = openai.AsyncOpenAI(api_key=self._resolve_key())
        result = await self._client.embeddings.create(model=self.model, input=texts)
        return [d.embedding for d in result.data]

    def _resolve_key(self) -> str:
        if self._api_key_file:
            with open(self._api_key_file, encoding="utf-8") as fh:
                return fh.read().strip()
        if self._api_key_env:
            import os

            return os.environ.get(self._api_key_env, "")
        return ""


def build_connector_store(cfg: Any) -> Any:
    """Construct the configured store; FAILS CLOSED when the driver is
    missing (no silent degradation — the memory substitute is for tests and
    offline dev only, per the ACC-01 deviation)."""

    store_type = cfg.store.type.value
    if store_type == "pgvector":
        try:
            import psycopg  # type: ignore[import-not-found]  # noqa: F401

            return _PgvectorStore(cfg)
        except ImportError as exc:
            raise ConfigError(
                "rag.store.type pgvector requires the psycopg package "
                "(present in the shipped image); install it to use this store"
            ) from exc
    try:
        import chromadb  # type: ignore[import-not-found]  # noqa: F401

        return _ChromaStore(cfg)
    except ImportError as exc:
        raise ConfigError(
            "rag.store.type chroma requires the chromadb package, which is NOT "
            "shipped in the runtime image; install chromadb into the image or "
            "switch to rag.store.type pgvector"
        ) from exc


class _ChromaStore:
    """chroma store: full client implementation, lazy collection."""

    def __init__(self, cfg: Any) -> None:
        self._cfg = cfg
        self._client: Any = None
        self._collection: Any = None

    def _ensure(self) -> Any:
        if self._collection is None:
            import chromadb  # type: ignore[import-not-found]

            settings = None
            kwargs: dict[str, Any] = {}
            connection = self._cfg.store.connectionStringEnv or self._cfg.store.connectionStringFile
            if connection:
                try:
                    import chromadb.config  # type: ignore[import-not-found]

                    settings = chromadb.config.Settings(
                        chroma_server_http_host=connection, is_persistent=False
                    )
                    kwargs["settings"] = settings
                except Exception:  # noqa: BLE001
                    kwargs["host"] = connection
            self._client = chromadb.Client(**kwargs)
            self._collection = self._client.get_or_create_collection(
                name=self._cfg.store.collection
            )
        return self._collection

    @staticmethod
    def _where(
        agent_name: str,
        principal_id: str,
        document_id: str | None = None,
        embedding_model: str | None = None,
    ) -> dict:
        clauses: list[dict[str, Any]] = [
            {"agent": agent_name},
            {"principal": principal_id},
        ]
        if document_id is not None:
            clauses.append({"document_id": document_id})
        if embedding_model is not None:
            # R-16: never score stale vectors from another embedding model.
            clauses.append({"model": embedding_model})
        return {"$and": clauses}

    async def upsert_document(self, **kwargs: Any) -> Any:
        import asyncio

        return await asyncio.to_thread(self._upsert, kwargs)

    def _upsert(self, kwargs: dict[str, Any]) -> Any:
        from .rag import DocumentRecord, utcnow

        collection = self._ensure()
        ids: list[str] = []
        embeddings: list[list[float]] = []
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []
        for chunk_index, text, chunk_hash, embedding in kwargs["chunks"]:
            ids.append(f"{kwargs['document_id']}:{chunk_index}:{chunk_hash}")
            embeddings.append(embedding)
            documents.append(text)
            metadatas.append(
                {
                    "agent": kwargs["agent_name"],
                    "principal": kwargs["principal_id"],
                    "document_id": kwargs["document_id"],
                    "chunk": chunk_index,
                    "hash": chunk_hash,
                    "model": kwargs["embedding_model"],
                    "doc_hash": kwargs["content_hash"],
                    "chunk_count": len(kwargs["chunks"]),
                }
            )
        collection.upsert(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)
        return DocumentRecord(
            document_id=kwargs["document_id"],
            agent_name=kwargs["agent_name"],
            principal_id=kwargs["principal_id"],
            chunk_count=len(ids),
            content_hash=kwargs["content_hash"],
            metadata=kwargs.get("metadata") or {},
            embedding_model=kwargs["embedding_model"],
            created_at=utcnow().isoformat(),
            updated_at=utcnow().isoformat(),
        )

    async def get_document(self, **kwargs: Any) -> Any:
        import asyncio

        return await asyncio.to_thread(self._get_document, kwargs)

    def _get_document(self, kwargs: dict[str, Any]) -> Any:
        from .rag import DocumentRecord

        collection = self._ensure()
        result = collection.get(
            where=self._where(kwargs["agent_name"], kwargs["principal_id"], kwargs["document_id"]),
            include=["metadatas"],
        )
        ids = result.get("ids") or []
        metadatas = result.get("metadatas") or []
        if not ids or not metadatas or not metadatas[0]:
            return None
        meta = metadatas[0]
        return DocumentRecord(
            document_id=kwargs["document_id"],
            agent_name=kwargs["agent_name"],
            principal_id=kwargs["principal_id"],
            chunk_count=int(meta.get("chunk_count", len(ids))),
            content_hash=meta.get("doc_hash", ""),
            metadata=kwargs.get("metadata") or {},
            embedding_model=meta.get("model", ""),
            created_at="",
            updated_at="",
        )

    async def search(self, **kwargs: Any) -> list[Any]:
        import asyncio

        return await asyncio.to_thread(self._search, kwargs)

    def _search(self, kwargs: dict[str, Any]) -> list[Any]:
        from .rag import ChunkHit

        collection = self._ensure()
        result = collection.query(
            query_embeddings=[kwargs["query_embedding"]],
            n_results=kwargs["top_k"],
            where=self._where(
                kwargs["agent_name"],
                kwargs["principal_id"],
                embedding_model=kwargs.get("embedding_model"),
            ),
        )
        hits: list[Any] = []
        ids = (result.get("ids") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        for i, doc_id in enumerate(ids):
            meta = (metadatas[i] if i < len(metadatas) else {}) or {}
            score = 1.0 - (distances[i] if i < len(distances) else 0.0)
            if score < kwargs["min_score"]:
                continue
            parts = doc_id.split(":")
            hits.append(
                ChunkHit(
                    document_id=parts[0] if parts else "",
                    chunk_index=int(meta.get("chunk", 0) or 0),
                    text=documents[i] if i < len(documents) else "",
                    score=score,
                    content_hash=meta.get("hash", ""),
                )
            )
        hits.sort(key=lambda h: (-h.score, h.stable_id))
        return hits[: kwargs["top_k"]]

    async def delete_document(self, **kwargs: Any) -> int:
        import asyncio

        return await asyncio.to_thread(self._delete_document, kwargs)

    def _delete_document(self, kwargs: dict[str, Any]) -> int:
        collection = self._ensure()
        result = collection.get(
            where=self._where(kwargs["agent_name"], kwargs["principal_id"], kwargs["document_id"]),
            include=[],
        )
        ids = result.get("ids") or []
        if ids:
            collection.delete(ids=ids)
        return len(ids)

    async def delete_principal(self, **kwargs: Any) -> int:
        import asyncio

        return await asyncio.to_thread(self._delete_principal, kwargs)

    def _delete_principal(self, kwargs: dict[str, Any]) -> int:
        collection = self._ensure()
        result = collection.get(
            where=self._where(kwargs["agent_name"], kwargs["principal_id"]),
            include=[],
        )
        ids = result.get("ids") or []
        if ids:
            collection.delete(ids=ids)
        return len(ids)

    async def health(self) -> bool:
        import asyncio

        return await asyncio.to_thread(self._health)

    def _health(self) -> bool:
        try:
            self._ensure()
            return True
        except Exception:  # noqa: BLE001
            return False


class _PgvectorStore:
    """pgvector store: real psycopg implementation, lazy connection."""

    def __init__(self, cfg: Any) -> None:
        self._cfg = cfg
        self._conn: Any = None

    def _connect(self) -> Any:
        import psycopg  # type: ignore[import-not-found]

        if self._conn is None or self._conn.closed:
            connection_string = (
                self._cfg.store.connectionStringEnv or self._cfg.store.connectionStringFile or ""
            )
            self._conn = psycopg.connect(connection_string, connect_timeout=5)
            with self._conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cur.execute(_PGVECTOR_DDL)
            self._conn.commit()
        return self._conn

    async def upsert_document(self, **kwargs: Any) -> Any:
        import asyncio

        return await asyncio.to_thread(self._upsert, kwargs)

    def _upsert(self, kwargs: dict[str, Any]) -> Any:
        from .rag import DocumentRecord, utcnow

        conn = self._connect()
        count = 0
        with conn.cursor() as cur:
            for chunk_index, text, chunk_hash, embedding in kwargs["chunks"]:
                cur.execute(
                    _PGVECTOR_UPSERT,
                    (
                        kwargs["agent_name"],
                        kwargs["principal_id"],
                        kwargs["document_id"],
                        chunk_index,
                        text,
                        chunk_hash,
                        kwargs["embedding_model"],
                        _vector_literal(embedding),
                        _json_literal(kwargs.get("metadata") or {}),
                        len(kwargs["chunks"]),
                        kwargs["content_hash"],
                    ),
                )
                count += 1
        conn.commit()
        return DocumentRecord(
            document_id=kwargs["document_id"],
            agent_name=kwargs["agent_name"],
            principal_id=kwargs["principal_id"],
            chunk_count=count,
            content_hash=kwargs["content_hash"],
            metadata=kwargs.get("metadata") or {},
            embedding_model=kwargs["embedding_model"],
            created_at=utcnow().isoformat(),
            updated_at=utcnow().isoformat(),
        )

    async def get_document(self, **kwargs: Any) -> Any:
        import asyncio

        return await asyncio.to_thread(self._get_document, kwargs)

    def _get_document(self, kwargs: dict[str, Any]) -> Any:
        from .rag import DocumentRecord

        conn = self._connect()
        with conn.cursor() as cur:
            cur.execute(
                _PGVECTOR_GET_DOCUMENT,
                (kwargs["agent_name"], kwargs["principal_id"], kwargs["document_id"]),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return DocumentRecord(
            document_id=row[0],
            agent_name=kwargs["agent_name"],
            principal_id=kwargs["principal_id"],
            chunk_count=int(row[1]),
            content_hash=row[2],
            metadata=row[3] if isinstance(row[3], dict) else {},
            embedding_model=row[4],
            created_at=str(row[5]),
            updated_at=str(row[6]),
        )

    async def search(self, **kwargs: Any) -> list[Any]:
        import asyncio

        return await asyncio.to_thread(self._search, kwargs)

    def _search(self, kwargs: dict[str, Any]) -> list[Any]:
        from .rag import ChunkHit

        conn = self._connect()
        query_literal = _vector_literal(kwargs["query_embedding"])
        with conn.cursor() as cur:
            cur.execute(
                _PGVECTOR_SEARCH,
                (
                    query_literal,
                    kwargs["agent_name"],
                    kwargs["principal_id"],
                    kwargs.get("embedding_model"),
                    query_literal,
                    int(kwargs["top_k"]),
                ),
            )
            rows = cur.fetchall()
        hits = [
            ChunkHit(
                document_id=row[0],
                chunk_index=int(row[1]),
                text=row[2],
                score=float(row[4]),
                content_hash=row[3],
            )
            for row in rows
            if float(row[4]) >= kwargs["min_score"]
        ]
        hits.sort(key=lambda h: (-h.score, h.stable_id))
        return hits

    async def delete_document(self, **kwargs: Any) -> int:
        import asyncio

        return await asyncio.to_thread(self._delete_document, kwargs)

    def _delete_document(self, kwargs: dict[str, Any]) -> int:
        conn = self._connect()
        with conn.cursor() as cur:
            cur.execute(
                _PGVECTOR_DELETE_DOCUMENT,
                (kwargs["agent_name"], kwargs["principal_id"], kwargs["document_id"]),
            )
            count = cur.rowcount
        conn.commit()
        return count

    async def delete_principal(self, **kwargs: Any) -> int:
        import asyncio

        return await asyncio.to_thread(self._delete_principal, kwargs)

    def _delete_principal(self, kwargs: dict[str, Any]) -> int:
        conn = self._connect()
        with conn.cursor() as cur:
            cur.execute(
                _PGVECTOR_DELETE_PRINCIPAL,
                (kwargs["agent_name"], kwargs["principal_id"]),
            )
            count = cur.rowcount
        conn.commit()
        return count

    async def health(self) -> bool:
        import asyncio

        return await asyncio.to_thread(self._health)

    def _health(self) -> bool:
        try:
            conn = self._connect()
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                return cur.fetchone() is not None
        except Exception:  # noqa: BLE001
            return False


def _vector_literal(values: list[float]) -> str:
    """pgvector array literal: '[0.1,0.2,...]' (bounded precision)."""
    body = ",".join(f"{v:.6f}" for v in values)
    return f"[{body}]"


def _json_literal(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, sort_keys=True)
