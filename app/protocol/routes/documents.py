"""RAG-03: owner-scoped document ingestion API (P4).

- ``POST /v1/documents`` — body ``{"id"?, "text", "metadata"?}`` with
  ``Idempotency-Key`` support; 201 with id/chunk count/content hash.
- ``GET /v1/documents/{id}`` — metadata/count/hash only; the stored text
  is never returned by default.
- ``DELETE /v1/documents/{id}`` — 204, idempotent; removes every scoped
  chunk (RAG-05).

Registered only when ``rag.enabled``; otherwise the paths are ordinary
404s (API-00). Ingestion never degrades silently (RAG-04): an embedding
or store failure surfaces as a 5xx, and the previous version of the
document stays intact (atomic upsert).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ...storage.model import validate_session_id
from ..errors import PublicErrorResponse

MAX_METADATA_BYTES = 64 * 1024


def _validate_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PublicErrorResponse("invalid_metadata", "metadata must be a JSON object", 400)
    encoded = json.dumps(value, sort_keys=True).encode("utf-8")
    if len(encoded) > MAX_METADATA_BYTES:
        raise PublicErrorResponse("invalid_metadata", "metadata exceeds 64 KiB", 400)
    for key, item in value.items():
        if not isinstance(key, str):
            raise PublicErrorResponse("invalid_metadata", "metadata keys must be strings", 400)
        if isinstance(item, (dict, list)):
            if isinstance(item, list) and all(
                isinstance(e, (str, int, float, bool)) or e is None for e in item
            ):
                continue
            raise PublicErrorResponse(
                "invalid_metadata",
                "metadata values must be scalars or lists of scalars",
                400,
            )
        if not (item is None or isinstance(item, (str, int, float, bool))):
            raise PublicErrorResponse("invalid_metadata", "metadata values must be scalars", 400)
    return value


def _canonical_idempotency_key(key: Any) -> str | None:
    import hashlib

    if not key:
        return None
    text = str(key).strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None


def register(app: Any, config: Any, components: dict[str, Any]) -> None:
    agent_name = config.name
    rag: Any = components.get("rag")
    if rag is None:
        return  # rag disabled: ordinary 404s (API-00)
    router = APIRouter(prefix="/v1")

    @router.post("/documents")
    async def create_document(request: Request):
        # API-06a: canonical Idempotency-Key replay over the backend.
        principal = getattr(request.state, "principal", "anonymous")
        try:
            body = await request.json()
        except Exception as exc:  # noqa: BLE001
            raise PublicErrorResponse("invalid_request", "invalid JSON body", 400) from exc
        if not isinstance(body, dict):
            raise PublicErrorResponse("invalid_request", "body must be an object", 400)
        idem_key = _canonical_idempotency_key(body.get("idempotency_key"))
        if idem_key:
            replay = await components["backend"].get_idempotency(
                agent_name=agent_name,
                principal_id=principal,
                session_id="__documents__",
                key=idem_key,
            )
            if replay is not None and replay.status == "completed":
                return JSONResponse(status_code=200, content=replay.outcome)
            # API-06a: admit the key before the work (the replay only fires
            # once the work completed; a racing duplicate sees the admitted
            # record and waits on the same outcome).
            await components["backend"].create_idempotency(
                agent_name=agent_name,
                principal_id=principal,
                session_id="__documents__",
                key=idem_key,
                ttl_seconds=86400,
            )
        text = body.get("text")
        if not isinstance(text, str) or not text:
            raise PublicErrorResponse("invalid_request", "text must be non-empty", 400)
        max_bytes = config.rag.maxDocumentBytes
        if len(text.encode("utf-8")) > max_bytes:
            raise PublicErrorResponse(
                "document_too_large",
                f"text exceeds maxDocumentBytes ({max_bytes})",
                400,
            )
        document_id = body.get("id") or f"doc-{uuid.uuid4().hex[:12]}"
        if not isinstance(document_id, str) or not validate_session_id(document_id):
            raise PublicErrorResponse("invalid_document_id", "invalid document id", 400)
        metadata = _validate_metadata(body.get("metadata", {}))
        try:
            record = await rag.ingest(
                agent_name=agent_name,
                principal_id=principal,
                document_id=document_id,
                text=text,
                metadata=metadata,
            )
        except Exception as exc:  # noqa: BLE001 - RAG-04: ingestion never degrades
            raise PublicErrorResponse(
                "rag_unavailable",
                "document ingestion failed (store or embedding unavailable)",
                503,
            ) from exc
        payload = {
            "object": "document",
            "id": record.document_id,
            "chunk_count": record.chunk_count,
            "content_hash": record.content_hash,
            "metadata": record.metadata,
        }
        if idem_key:
            await components["backend"].finish_idempotency(
                agent_name=agent_name,
                principal_id=principal,
                session_id="__documents__",
                key=idem_key,
                status="completed",
                outcome=payload,
            )
        return JSONResponse(status_code=201, content=payload)

    @router.get("/documents/{document_id}")
    async def get_document(request: Request, document_id: str):
        principal = getattr(request.state, "principal", "anonymous")
        if not validate_session_id(document_id):
            raise PublicErrorResponse("invalid_document_id", "invalid document id", 400)
        record = await rag.store.get_document(
            agent_name=agent_name, principal_id=principal, document_id=document_id
        )
        if record is None:
            raise PublicErrorResponse("document_not_found", "unknown document id", 404)
        # RAG-03: metadata/count/hash only — never the stored text.
        return JSONResponse(
            {
                "object": "document",
                "id": record.document_id,
                "chunk_count": record.chunk_count,
                "content_hash": record.content_hash,
                "metadata": record.metadata,
                "embedding_model": record.embedding_model,
                "created_at": record.created_at,
                "updated_at": record.updated_at,
            }
        )

    @router.delete("/documents/{document_id}")
    async def delete_document(request: Request, document_id: str):
        principal = getattr(request.state, "principal", "anonymous")
        if not validate_session_id(document_id):
            raise PublicErrorResponse("invalid_document_id", "invalid document id", 400)
        await rag.delete_document(
            agent_name=agent_name, principal_id=principal, document_id=document_id
        )
        # RAG-03/05: idempotent 204 — deleting a missing document is a no-op.
        return JSONResponse(status_code=204, content={})

    app.include_router(router)
