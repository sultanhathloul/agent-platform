"""Builds the document side of the platform from config/docs.yaml (C6, owner decision C3): every
DocumentBackend by name (I1), the internal pipeline's ingestion and worker, and the backend uploads
are registered with: `backend` of config/docs.yaml, overridden by `DOCS_BACKEND` in .env (I11).
The core calls this and holds only generic DocumentBackends; no docs-named configuration or type
reaches it."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from app.attachments.objects import ObjectStore
from app.attachments.store import AttachmentStore
from app.context.tokens import TokenCounter
from app.db import Database
from app.models.config import ModelsConfig
from app.models.embeddings import EmbeddingGateway
from app.rag.backend import DocumentBackend
from app.rag.config import DocsConfig, Rerank, load_docs_config
from app.rag.ingest.pipeline import IngestPipeline
from app.rag.ingest.queue import IngestQueue
from app.rag.ingest.worker import IngestWorker
from app.rag.internal import InternalDocumentBackend
from app.rag.mcp import MCPDocumentBackend
from app.rag.rerank import Reranker
from app.rag.retrieve import HybridRetriever
from app.rag.vectorstore import PgVectorStore
from app.settings import Settings


@dataclass(frozen=True)
class Documents:
    backend: DocumentBackend  # the one uploads are registered with
    config: DocsConfig
    # The internal backend's ingestion: the ingest service runs the worker; tests run the
    # pipeline in-process.
    pipeline: IngestPipeline | None = None
    worker: IngestWorker | None = None
    backends: Mapping[str, DocumentBackend] = field(default_factory=dict)  # every backend, by name


def build_documents(
    settings: Settings,
    *,
    db: Database,
    redis,
    embeddings: EmbeddingGateway,
    models_config: ModelsConfig,
    counter: TokenCounter,
    attachments: AttachmentStore,
    objects: ObjectStore,
    mcp=None,
    extra_backends: Mapping[str, DocumentBackend] | None = None,
) -> Documents:
    """`mcp`: the platform's MCP client; when config/mcp.yaml lists the server `mcp_server` names,
    the `mcp` backend reaches it (I11). `extra_backends`: more backends by name (tests)."""
    config = load_docs_config(settings.config_dir)
    selected = settings.docs_backend or config.backend
    # The reranker is configured per environment, from .env, not config/docs.yaml (finding 3): the
    # development default is off, and a GPU deployment turns it on with 16 candidates on 1070 chars.
    rerank = Rerank(
        enabled=settings.rerank_enabled,
        candidates=settings.rerank_candidates,
        max_chars=settings.rerank_max_chars,
    )
    config = config.model_copy(update={"retrieval": config.retrieval.model_copy(update={"rerank": rerank})})
    vectors = PgVectorStore(db)
    queue = IngestQueue(redis, namespace=settings.redis_namespace)
    retriever = HybridRetriever(
        vectors, embeddings, Reranker(settings, models_config), config.retrieval, counter
    )
    pipeline = IngestPipeline(
        db=db,
        attachments=attachments,
        objects=objects,
        vectors=vectors,
        embeddings=embeddings,
        config=config,
        counter=counter,
    )
    backend = InternalDocumentBackend(
        attachments=attachments, vectors=vectors, retriever=retriever, queue=queue
    )
    worker = IngestWorker(pipeline, attachments, config, queue) if redis is not None else None
    backends: dict[str, DocumentBackend] = {"internal": backend}
    if mcp is not None and config.mcp_server is not None and config.mcp_server in mcp.servers:
        backends["mcp"] = MCPDocumentBackend(mcp, config.mcp_server)
    backends |= dict(extra_backends or {})
    if selected not in backends:
        raise ValueError(f"unknown document backend {selected!r}; known: {sorted(backends)}")
    return Documents(
        backend=backends[selected], config=config, pipeline=pipeline, worker=worker, backends=backends
    )
