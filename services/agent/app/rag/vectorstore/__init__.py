"""The VectorStore interface and its pgvector implementation (README §2, ADR-0131)."""

from app.rag.vectorstore.base import VectorStore
from app.rag.vectorstore.pgvector import PgVectorStore

__all__ = ["PgVectorStore", "VectorStore"]
