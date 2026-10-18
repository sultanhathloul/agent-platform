"""MCPDocumentBackend (phase 05, I11, ADR-0212): the DocumentBackend protocol over a RAG server of
config/mcp.yaml. Every call goes through `broker.transport` (ADR-0230): the allowlist (the
server's `class: transport` operations), the pinned schemas, the injected fields, the server's
auth, timeouts, the byte cap, the breaker, and one content-free span and audit entry per call
(G8). This module holds no MCP client and no connection: only a transport function.

The server's operations (`register`, `status`, `search`, `fetch`) are transport, declared to no
capability, so no model can call them around `docs_search`, which stays the docs capability's
only tool and its one guard point: scope injection, PRE_TOOL and POST_TOOL run there (ADR-0160,
ADR-0158). The injected fields (tenant, user, collections) come from the DocumentScope the core
built, or, for `register`, from the stored upload. What the server returns is untrusted data,
checked by the runtime path like any backend's (ADR-0195). The server must enforce the scope
itself; the contract suite proves it does (G6)."""

import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from app.observability.otel import tracer
from app.rag.backend import DocumentScope, DocumentStatus, SourceHit, StoredFile
from app.rag.retrieve import section_hash

log = logging.getLogger(__name__)

# broker.transport: (server, operation, arguments, scope) -> the structured result.
Transport = Callable[[str, str, dict[str, Any], Any], Awaitable[dict[str, Any]]]


class MCPDocumentBackend:
    name = "mcp"

    def __init__(self, transport: Transport, server_id: str):
        self._transport = transport
        self._server_id = server_id

    async def _call(self, operation: str, arguments: dict[str, Any], scope: DocumentScope) -> dict[str, Any]:
        return await self._transport(self._server_id, operation, arguments, scope)

    async def register(self, stored: StoredFile) -> None:
        await self._call(
            "register",
            {
                "document_id": stored.id,
                "name": stored.name,
                "mime": stored.mime,
                "kind": stored.kind,
                "size": stored.size,
                "object_key": stored.object_key,
                "collection": stored.collection,
            },
            DocumentScope(
                tenant_id=stored.tenant_id, user_id=stored.user_id, collections=(stored.collection,)
            ),
        )

    async def status(self, document_id: str, scope: DocumentScope) -> DocumentStatus | None:
        """Raises ToolFailure when the server cannot answer: the caller shows the record's last
        known status (ADR-0233)."""
        if not scope.collections:
            return None
        data = await self._call("status", {"document_id": document_id}, scope)
        if not data.get("found"):
            return None
        return DocumentStatus.model_validate(
            {k: data.get(k) for k in ("status", "reason", "pages")} | {"warnings": data.get("warnings") or []}
        )

    async def search(self, query: str, scope: DocumentScope, k: int) -> list[SourceHit]:
        if not scope.collections:
            return []
        # The same content-free span as the internal backend's (ADR-0137): ids, pages, section
        # hashes and scores, never text, so retrieval is measured alike on both backends.
        with tracer.start_as_current_span("rag.retrieve") as span:
            span.set_attribute("rag.backend", self.name)
            span.set_attribute("rag.collections", len(scope.collections))
            data = await self._call("search", {"query": query, "k": k}, scope)
            allowed = set(scope.collections)
            hits = [SourceHit.model_validate(hit) for hit in data.get("hits") or []]
            # A hit outside the scope is never passed on, whatever the server returned (G6).
            top = [hit for hit in hits if hit.collection in allowed][:k]
            span.set_attribute("rag.returned", len(top))
            span.set_attribute(
                "rag.results",
                json.dumps(
                    [
                        {
                            "chunk_id": h.ref,
                            "attachment_id": h.document_id,
                            "page": h.page,
                            "section_sha": section_hash(h.section),
                            "score": round(h.score, 4),
                        }
                        for h in top
                    ]
                ),
            )
            return top

    async def fetch(self, refs: Sequence[str], scope: DocumentScope) -> dict[str, str]:
        wanted = list(dict.fromkeys(refs))
        if not wanted or not scope.collections:
            return {}
        data = await self._call("fetch", {"refs": wanted}, scope)
        texts = data.get("texts") or {}
        return {ref: text for ref, text in texts.items() if ref in wanted and isinstance(text, str)}
