"""MCPDocumentBackend (phase 05, I11, ADR-0212): the DocumentBackend protocol over a RAG server of
config/mcp.yaml, through the platform's MCP client as transport: the allowlist and the pinned
schemas, the server's auth, timeouts, the byte cap, the breaker, and content-free `mcp.call`
spans (G8).

The server's tools (`register`, `status`, `search`, `fetch`) are declared to no capability, so no
model can call them around `docs_search`, which stays the docs capability's only tool and its one
guard point: scope injection, PRE_TOOL and POST_TOOL run there (ADR-0160, ADR-0158). Here the
injected fields (tenant, user, collections) are filled from the DocumentScope the broker built,
or, for `register`, from the stored upload; arguments are checked against the pinned schema
before anything is sent. What the server returns is untrusted data, checked by the runtime path
like any backend's (ADR-0195). The server must enforce the scope itself; the contract suite proves
it does (G6)."""

import json
import logging
from collections.abc import Sequence
from typing import Any

from jsonschema import Draft202012Validator

from app.mcp.client import UNAVAILABLE, MCPClient
from app.mcp.tools import public_schema
from app.observability.otel import tracer
from app.rag.backend import DocumentScope, DocumentStatus, SourceHit, StoredFile
from app.rag.retrieve import section_hash
from app.tools.spec import ToolFailure

log = logging.getLogger(__name__)


class MCPDocumentBackend:
    name = "mcp"

    def __init__(self, client: MCPClient, server_id: str):
        if server_id not in client.servers:
            raise ValueError(f"config/mcp.yaml lists no server {server_id!r} for the document backend")
        self._server = client.servers[server_id]

    async def _call(self, tool_name: str, arguments: dict[str, Any], scope: dict[str, Any]) -> dict[str, Any]:
        tool = self._server.entry.tool(tool_name)
        schema = self._server.input_schema(tool.name)
        if schema is None:
            raise ToolFailure(UNAVAILABLE)
        if any(Draft202012Validator(public_schema(schema, set(tool.injected))).iter_errors(arguments)):
            raise ValueError(f"arguments of {tool_name} do not match its pinned schema")
        values = {
            "principal.tenant_id": scope["tenant_id"],
            "principal.user_id": scope["user_id"],
            "scope.collections": list(scope["collections"]),
        }
        injected = {field: values[source] for field, source in tool.injected.items()}
        result = await self._server.call(tool, {**arguments, **injected})
        if result.is_error or not isinstance(result.data, dict):
            raise ToolFailure("tool_error")
        return result.data

    @staticmethod
    def _scope(scope: DocumentScope) -> dict[str, Any]:
        return {"tenant_id": scope.tenant_id, "user_id": scope.user_id, "collections": scope.collections}

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
            {"tenant_id": stored.tenant_id, "user_id": stored.user_id, "collections": (stored.collection,)},
        )

    async def status(self, document_id: str, scope: DocumentScope) -> DocumentStatus | None:
        if not scope.collections:
            return None
        try:
            data = await self._call("status", {"document_id": document_id}, self._scope(scope))
        except ToolFailure as failure:
            # The server is down or refused: the file reads as pending until it answers again.
            log.warning("mcp document backend: status failed (%s)", failure.code)
            return None
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
            data = await self._call("search", {"query": query, "k": k}, self._scope(scope))
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
        data = await self._call("fetch", {"refs": wanted}, self._scope(scope))
        texts = data.get("texts") or {}
        return {ref: text for ref, text in texts.items() if ref in wanted and isinstance(text, str)}
