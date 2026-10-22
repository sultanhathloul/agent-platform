"""mcp-rag-ref (I10, ADR-0211): the phase 04 document pipeline over MCP Streamable HTTP, official SDK.

Tools, each taking the scope fields `tenant`, `user` and `collections`, which the platform always
injects from the verified principal (I6) and this server always enforces (G6):
- `register {document_id, name, mime, kind, size, object_key, collection}`: records a stored upload
  in one of the scope's collections and indexes it in the background; idempotent by id.
- `status {document_id}` → `{found, status, reason, pages, warnings}`.
- `search {query, k}` → `{hits: [{ref, collection, document_id, file_name, page, section, text,
  score}]}` over the indexed documents of the scope's collections: hybrid retrieval (vector and
  full text, reciprocal rank fusion), the phase 04 retriever.
- `fetch {refs}` → `{texts: {ref: text}}`, the references inside the scope only.

It keeps its documents and chunks in a schema of its own (`rag_ref`) under a database role of its
own, and reads uploads from MinIO with a read-only user of its own (ADR-0178). It never parses a
document in its own process: each file is extracted and chunked in a child process with CPU, memory
and time limits (app/rag/ingest/sandbox.py, ADR-0176). It applies no policy (ADR-0158): chunks are
stored as extracted, and the platform checks what it returns at runtime. Every request but the
health check needs the bearer token of MCP_RAG_TOKEN when it is set."""

import asyncio
import contextlib
import hmac
import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

import uvicorn
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from app.attachments.objects import ObjectStore, StorageUnavailable
from app.context.tokens import default_counter
from app.db import Database
from app.models.config import load_models_config
from app.models.embeddings import EmbeddingGateway
from app.models.gateway import ModelUnavailable
from app.rag.collections import kind_of
from app.rag.config import DocsConfig, Rerank, load_docs_config
from app.rag.extract import ExtractionFailed, detect_kind
from app.rag.ingest.sandbox import MemoryBudgetExceeded, ParserDefect, check_memory_budget, run_sandboxed
from app.rag.rerank import Reranker
from app.rag.retrieve import HybridRetriever
from app.rag.vectorstore import PgVectorStore
from app.settings import Settings
from mcp_ref.rag.store import Documents, owner_of

log = logging.getLogger("mcp-rag-ref")
MIGRATIONS = Path(__file__).resolve().parent / "migrations"
WORKERS = 2

Tenant = Annotated[str, Field(min_length=1, max_length=128)]
User = Annotated[str, Field(min_length=1, max_length=128)]
Collections = Annotated[list[str], Field(max_length=64)]


class Registered(BaseModel):
    registered: bool
    status: str


class Warning(BaseModel):
    code: Literal["pages_without_text"]
    count: int


class Status(BaseModel):
    found: bool
    status: str | None = None
    reason: str | None = None
    pages: int | None = None
    warnings: list[Warning] = []


class Hit(BaseModel):
    ref: str
    collection: str
    document_id: str
    file_name: str
    page: int | None
    section: str | None
    text: str
    score: float


class Found(BaseModel):
    hits: list[Hit]


class Fetched(BaseModel):
    texts: dict[str, str]


@dataclass
class Pipeline:
    db: Database
    documents: Documents
    vectors: PgVectorStore
    retriever: HybridRetriever
    embeddings: EmbeddingGateway
    objects: ObjectStore
    config: DocsConfig
    queue: asyncio.Queue

    async def index(self, document_id: str) -> None:
        record = await self.documents.start_attempt(document_id)
        if record is None:
            return
        try:
            data = await self.objects.get(record.object_key)
            if len(data) > self.config.limits.max_file_bytes:
                raise ExtractionFailed("file_too_large")
            kind = detect_kind(record.name, data, self.config)
            if kind is None or kind != record.kind:
                raise ExtractionFailed("unsupported_type")
            # Parsed in a child process with limits, from a thread: never in this process (ADR-0176).
            parsed = await asyncio.to_thread(run_sandboxed, kind, data, self.config)
            batch = self.config.ingest.embed_batch
            vectors: list[list[float]] = []
            for start in range(0, len(parsed.chunks), batch):
                texts = [c.text for c in parsed.chunks[start : start + batch]]
                vectors.extend(await self.embeddings.embed_many(texts))
            warnings = (
                [{"code": "pages_without_text", "count": parsed.pages_without_text}]
                if parsed.pages_without_text
                else []
            )
            async with self.db.connection() as conn, conn.transaction():
                await self.vectors.replace_document(
                    record.owner, record.collection, record.id, parsed.chunks, vectors, conn=conn
                )
                await self.documents.mark_indexed(record.id, pages=parsed.pages, warnings=warnings, conn=conn)
        except ExtractionFailed as failure:
            await self.documents.mark_failed(record.id, failure.reason, pages=failure.pages)
        except ParserDefect:
            await self.documents.mark_failed(record.id, "ingest_error")
        except (StorageUnavailable, ModelUnavailable) as exc:
            reason = "storage_unavailable" if isinstance(exc, StorageUnavailable) else "embedding_unavailable"
            if record.attempts >= self.config.ingest.max_attempts:
                await self.documents.mark_failed(record.id, reason)
            else:
                await asyncio.sleep(2)
                await self.queue.put(record.id)

    async def work(self) -> None:
        while True:
            document_id = await self.queue.get()
            try:
                await self.index(document_id)
            except Exception as exc:  # one file never stops the worker; its type only (S5)
                log.warning("indexing failed (%s)", type(exc).__name__)
                with contextlib.suppress(Exception):
                    await self.documents.mark_failed(document_id, "ingest_error")
            finally:
                self.queue.task_done()


def build_pipeline(settings: Settings, database_url: str) -> Pipeline:
    config = load_docs_config(settings.config_dir)
    rerank = Rerank(
        enabled=settings.rerank_enabled,
        candidates=settings.rerank_candidates,
        max_chars=settings.rerank_max_chars,
    )
    config = config.model_copy(update={"retrieval": config.retrieval.model_copy(update={"rerank": rerank})})
    models = load_models_config(settings.config_dir)
    db = Database(database_url, schema="rag_ref", migrations_dir=MIGRATIONS, max_size=8)
    vectors = PgVectorStore(db, table="rag_ref.chunks")
    embeddings = EmbeddingGateway(settings, models)
    retriever = HybridRetriever(
        vectors, embeddings, Reranker(settings, models), config.retrieval, default_counter()
    )
    objects = ObjectStore(
        settings.minio_endpoint,
        settings.minio_access_key,
        settings.minio_secret_key,
        settings.uploads_bucket,
        secure=settings.minio_secure,
    )
    return Pipeline(db, Documents(db), vectors, retriever, embeddings, objects, config, asyncio.Queue())


def _allowed(collections: list[str]) -> list[str]:
    return [c for c in dict.fromkeys(collections) if kind_of(c) is not None]


def build_mcp(pipeline: Pipeline, *, host: str = "0.0.0.0", port: int = 8000) -> FastMCP:
    mcp = FastMCP("mcp-rag-ref", host=host, port=port, log_level="WARNING")
    documents = pipeline.documents

    @mcp.tool()
    async def register(
        document_id: Annotated[str, Field(min_length=36, max_length=36)],
        name: Annotated[str, Field(min_length=1, max_length=255)],
        mime: Annotated[str, Field(min_length=1, max_length=255)],
        kind: Literal["pdf", "docx", "xlsx", "txt", "md"],
        size: Annotated[int, Field(ge=0)],
        object_key: Annotated[str, Field(min_length=1, max_length=512)],
        collection: Annotated[str, Field(min_length=1, max_length=200)],
        tenant: Tenant,
        user: User,
        collections: Collections,
    ) -> Registered:
        """Records a stored upload in one of the scope's collections and indexes it."""
        if collection not in _allowed(collections):
            return Registered(registered=False, status="outside_scope")
        uuid.UUID(document_id)
        owner = owner_of(tenant, user, collection)
        await documents.register(
            {
                "id": document_id,
                "tenant_id": owner.tenant_id,
                "user_id": owner.user_id,
                "collection": collection,
                "name": name,
                "mime": mime,
                "kind": kind,
                "size": size,
                "object_key": object_key,
            }
        )
        await pipeline.queue.put(document_id)
        return Registered(registered=True, status="pending")

    @mcp.tool()
    async def status(
        document_id: Annotated[str, Field(min_length=1, max_length=64)],
        tenant: Tenant,
        user: User,
        collections: Collections,
    ) -> Status:
        """A document's indexing status, inside the scope only."""
        record = await documents.get(document_id, tenant, user, _allowed(collections))
        if record is None:
            return Status(found=False)
        return Status(
            found=True,
            status=record.status,
            reason=record.reason,
            pages=record.pages,
            warnings=record.warnings,
        )

    @mcp.tool()
    async def search(
        query: Annotated[str, Field(min_length=1, max_length=8000)],
        tenant: Tenant,
        user: User,
        collections: Collections,
        k: Annotated[int, Field(ge=1, le=20)] = 8,
    ) -> Found:
        """The best chunks of the scope's indexed documents for a query."""
        hits: list[Hit] = []
        for collection in _allowed(collections):
            owner = owner_of(tenant, user, collection)
            names = await documents.indexed_in(owner, collection)
            if not names:
                continue
            found = await pipeline.retriever.retrieve(
                owner, collection, query, document_ids=list(names), top_k=k
            )
            hits += [
                Hit(
                    ref=h.id,
                    collection=collection,
                    document_id=h.attachment_id,
                    file_name=names[h.attachment_id],
                    page=h.page,
                    section=h.section,
                    text=h.text,
                    score=h.score,
                )
                for h in found
                if h.attachment_id in names
            ]
        return Found(hits=sorted(hits, key=lambda h: -h.score)[:k])

    @mcp.tool()
    async def fetch(
        refs: Annotated[list[str], Field(max_length=64)],
        tenant: Tenant,
        user: User,
        collections: Collections,
    ) -> Fetched:
        """The text of each chunk reference inside the scope; others are left out."""
        texts: dict[str, str] = {}
        for collection in _allowed(collections):
            texts |= await pipeline.vectors.fetch(owner_of(tenant, user, collection), collection, refs)
        return Fetched(texts=texts)

    return mcp


class BearerGate:
    """Every request but /health needs `Authorization: Bearer <token>` when a token is set."""

    def __init__(self, app, token: str):
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and self.token and scope.get("path") != "/health":
            header = dict(scope.get("headers") or []).get(b"authorization", b"").decode("latin-1")
            if not hmac.compare_digest(header, f"Bearer {self.token}"):
                response = JSONResponse({"error": "unauthorized"}, status_code=401)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def build_app(pipeline: Pipeline, *, token: str, host: str = "0.0.0.0", port: int = 8000):
    mcp = build_mcp(pipeline, host=host, port=port)
    inner = mcp.streamable_http_app()
    workers: list[asyncio.Task] = []

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        await pipeline.db.open()
        for document_id in await pipeline.documents.pending_ids():
            await pipeline.queue.put(document_id)
        workers.extend(asyncio.create_task(pipeline.work()) for _ in range(WORKERS))
        async with mcp.session_manager.run():
            yield
        for worker in workers:
            worker.cancel()
        await pipeline.db.close()

    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    app = Starlette(routes=[Route("/health", health), Mount("/", app=inner)], lifespan=lifespan)
    return BearerGate(app, token)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings()
    port = int(os.environ.get("PORT", "8000"))
    pipeline = build_pipeline(settings, settings.database_url)
    try:
        log.info(check_memory_budget(WORKERS, pipeline.config))  # I7, ADR-0252
    except MemoryBudgetExceeded as exc:
        log.error("%s", exc)
        raise SystemExit(1) from None
    app = build_app(pipeline, token=os.environ.get("MCP_RAG_TOKEN", ""), port=port)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
