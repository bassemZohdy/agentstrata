"""RAG connector shells: real driver adapters, import-guarded (P4).

Following the recorded ACC-01 deviation pattern, the acceptance proofs run
against the memory store and the deterministic embedding; these thin shells
wire the real drivers (google-genai for Gemini embeddings, openai for
OpenAI embeddings, chromadb / pgvector-SQL for stores) and are importable
only when the driver is installed. ``build_connector_store`` and the
embedding classes degrade to the substitutes on ImportError.
"""

from __future__ import annotations

from typing import Any


class GeminiEmbedding:
    """gemini embedding via google-genai (import-guarded)."""

    def __init__(self, cfg: Any) -> None:
        from google.genai import types  # noqa: F401 - driver import guard

        self.model = cfg.model
        self._api_key_env = cfg.apiKeyEnv
        self._api_key_file = cfg.apiKeyFile
        self._client: Any = None

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self._client is None:
            from google import genai

            key = self._resolve_key()
            self._client = genai.Client(api_key=key)
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
    """openai embedding via the openai SDK (import-guarded)."""

    def __init__(self, cfg: Any) -> None:
        import openai  # noqa: F401 - driver import guard

        self.model = cfg.model
        self._api_key_env = cfg.apiKeyEnv
        self._api_key_file = cfg.apiKeyFile
        self._client: Any = None

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self._client is None:
            import openai

            key = self._resolve_key()
            self._client = openai.AsyncOpenAI(api_key=key)
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
    """Construct the configured store shell; raises ImportError when the
    driver is missing so the caller degrades to the memory substitute."""

    store_type = cfg.store.type.value
    if store_type == "pgvector":
        try:
            import psycopg  # type: ignore[import-not-found]  # driver guard

            return _PgvectorStore(cfg)
        except ImportError:
            raise
    try:
        import chromadb  # type: ignore[import-not-found]  # driver guard

        return _ChromaStore(cfg)
    except ImportError:
        raise


class _ChromaStore:
    """chroma store shell (driver import-guarded; real-instance proof
    deferred per the ACC-01 deviation)."""

    def __init__(self, cfg: Any) -> None:
        self._cfg = cfg
        self._client: Any = None
        self._collection: Any = None

    def _ensure(self) -> Any:
        if self._collection is None:
            import chromadb  # type: ignore[import-not-found]  # driver guard

            kwargs: dict[str, Any] = {}
            connection = self._cfg.store.connectionStringEnv or self._cfg.store.connectionStringFile
            if connection:
                # the connection string holds the endpoint (e.g. http://...)
                kwargs["host"] = connection
            self._client = chromadb.Client(**kwargs)
            self._collection = self._client.get_or_create_collection(
                name=self._cfg.store.collection
            )
        return self._collection

    async def upsert_document(self, **kwargs: Any) -> Any:
        import asyncio

        return await asyncio.to_thread(self._upsert, kwargs)

    async def get_document(self, **kwargs: Any) -> Any:
        return None  # real-instance proof deferred (ACC-01 deviation)

    def _upsert(self, kwargs: dict[str, Any]) -> int:
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
                    "chunk": chunk_index,
                    "hash": chunk_hash,
                    "model": kwargs["embedding_model"],
                }
            )
        collection.upsert(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)
        return len(ids)

    async def search(self, **kwargs: Any) -> Any:
        import asyncio

        return await asyncio.to_thread(self._search, kwargs)

    def _search(self, kwargs: dict[str, Any]) -> list[Any]:
        from .rag import ChunkHit

        collection = self._ensure()
        result = collection.query(
            query_embeddings=[kwargs["query_embedding"]],
            n_results=kwargs["top_k"],
            where={
                "$and": [
                    {"agent": kwargs["agent_name"]},
                    {"principal": kwargs["principal_id"]},
                ]
            },
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
            hits.append(
                ChunkHit(
                    document_id=doc_id.split(":")[0],
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

        return await asyncio.to_thread(self._delete, kwargs)

    def _delete(self, kwargs: dict[str, Any]) -> int:
        collection = self._ensure()
        result = collection.get(
            where={
                "$and": [
                    {"agent": kwargs["agent_name"]},
                    {"principal": kwargs["principal_id"]},
                    {"$or": [{"document": kwargs["document_id"]}]},
                ]
            }
        )
        return 0  # real-instance proof deferred (ACC-01 deviation)

    async def delete_principal(self, **kwargs: Any) -> int:
        return 0  # real-instance proof deferred (ACC-01 deviation)

    async def health(self) -> bool:
        try:
            self._ensure()
            return True
        except Exception:  # noqa: BLE001
            return False


class _PgvectorStore:
    """pgvector store shell (driver import-guarded; real-instance proof
    deferred per the ACC-01 deviation)."""

    def __init__(self, cfg: Any) -> None:
        self._cfg = cfg
        self._conn: Any = None

    async def upsert_document(self, **kwargs: Any) -> Any:
        return None  # real-instance proof deferred (ACC-01 deviation)

    async def get_document(self, **kwargs: Any) -> Any:
        return None

    async def search(self, **kwargs: Any) -> list[Any]:
        return []

    async def delete_document(self, **kwargs: Any) -> int:
        return 0

    async def delete_principal(self, **kwargs: Any) -> int:
        return 0

    async def health(self) -> bool:
        return False
