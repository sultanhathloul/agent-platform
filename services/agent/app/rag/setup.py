"""Builds the document side of the platform from config/docs.yaml (C6, owner decision C3): the
DocumentBackend `backend` names, and for the internal backend its ingestion pipeline and worker.
The core calls this and holds only the generic DocumentBackend; no docs-named configuration or
type reaches it."""

from dataclasses import dataclass

from app.attachments.objects import ObjectStore
from app.attachments.store import AttachmentStore
from app.context.tokens import TokenCounter
from app.db import Database
from app.models.config import ModelsConfig
from app.models.embeddings import EmbeddingGateway
from app.rag.backend import DocumentBackend
from app.rag.config import DocsConfig, load_docs_config
from app.rag.ingest.pipeline import IngestPipeline
from app.rag.ingest.queue import IngestQueue
from app.rag.ingest.worker import IngestWorker
from app.rag.internal import InternalDocumentBackend
from app.rag.rerank import Reranker
from app.rag.retrieve import HybridRetriever
from app.rag.vectorstore import PgVectorStore
from app.settings import Settings


@dataclass(frozen=True)
class Documents:
    backend: DocumentBackend
    config: DocsConfig
    # The internal backend's ingestion: the ingest service runs the worker; tests run the
    # pipeline in-process.
    pipeline: IngestPipeline | None = None
    worker: IngestWorker | None = None


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
) -> Documents:
    config = load_docs_config(settings.config_dir)
    if config.backend != "internal":
        raise ValueError(f"unknown document backend {config.backend!r}")
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
    return Documents(backend=backend, config=config, pipeline=pipeline, worker=worker)
