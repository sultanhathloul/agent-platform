"""VectorStore on pgvector in the agent's database (README §2, ADR-0131, ADR-0132).

Every statement names (collection, tenant_id, user_id). A chunk goes with its attachment
record (a foreign key, ON DELETE CASCADE; owner decision S4). Similarity is computed only
against vectors of the query's dimension, so the embedding model can change without a
migration (ADR-0058's reasoning)."""

import re
from collections.abc import Sequence

from app.db import Database
from app.rag.types import Chunk, Owner, ScoredChunk
from app.text.arabic import normalize_for_search

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_FIELDS = "id::text AS id, attachment_id::text AS attachment_id, page, section, ord, text"


def _vector(values: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


def _documents(document_ids: Sequence[str] | None) -> tuple[str, dict]:
    if document_ids is None:
        return "", {}
    return " AND attachment_id = ANY(%(documents)s::uuid[])", {"documents": list(document_ids)}


class PgVectorStore:
    def __init__(self, db: Database):
        self._db = db

    async def replace_document(
        self,
        owner: Owner,
        collection: str,
        document_id: str,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Sequence[float]],
        *,
        conn=None,
    ) -> int:
        if len(chunks) != len(embeddings):
            raise ValueError(f"{len(chunks)} chunks but {len(embeddings)} embeddings")
        if conn is None:
            async with self._db.connection() as own, own.transaction():
                return await self._replace(own, owner, collection, document_id, chunks, embeddings)
        return await self._replace(conn, owner, collection, document_id, chunks, embeddings)

    async def _replace(self, conn, owner, collection, document_id, chunks, embeddings) -> int:
        await conn.execute(
            "DELETE FROM agent.rag_chunks WHERE collection = %s AND tenant_id = %s AND user_id = %s "
            "AND attachment_id = %s",
            (collection, owner.tenant_id, owner.user_id, document_id),
        )
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            await conn.execute(
                "INSERT INTO agent.rag_chunks (collection, attachment_id, user_id, tenant_id, page, section, "
                "ord, text, text_norm, embedding) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector)",
                (
                    collection,
                    document_id,
                    owner.user_id,
                    owner.tenant_id,
                    chunk.page,
                    chunk.section,
                    chunk.ord,
                    chunk.text,
                    normalize_for_search(chunk.text),
                    _vector(embedding),
                ),
            )
        return len(chunks)

    async def vector_search(
        self,
        owner: Owner,
        collection: str,
        embedding: Sequence[float],
        *,
        limit: int,
        document_ids: Sequence[str] | None = None,
    ) -> list[ScoredChunk]:
        documents, extra = _documents(document_ids)
        params = {
            "vector": _vector(embedding),
            "collection": collection,
            "tenant_id": owner.tenant_id,
            "user_id": owner.user_id,
            "dimensions": len(embedding),
            "limit": limit,
            **extra,
        }
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                f"SELECT {_FIELDS}, 1 - (embedding <=> %(vector)s::vector) AS score FROM agent.rag_chunks "
                "WHERE collection = %(collection)s AND tenant_id = %(tenant_id)s AND user_id = %(user_id)s "
                f"AND vector_dims(embedding) = %(dimensions)s{documents} "
                "ORDER BY embedding <=> %(vector)s::vector, ord LIMIT %(limit)s",
                params,
            )
            rows = await cursor.fetchall()
        return [
            ScoredChunk(
                **{k: r[k] for k in ("id", "attachment_id", "page", "section", "ord", "text")},
                score=float(r["score"]),
                vector_rank=rank,
            )
            for rank, r in enumerate(rows, start=1)
        ]

    async def text_search(
        self,
        owner: Owner,
        collection: str,
        terms: Sequence[str],
        *,
        limit: int,
        document_ids: Sequence[str] | None = None,
    ) -> list[ScoredChunk]:
        # Terms are normalized words (\w only), so joining them with | is a valid tsquery.
        words = sorted({t for t in terms if t and t.isalnum()})
        if not words:
            return []
        documents, extra = _documents(document_ids)
        params = {
            "query": " | ".join(words),
            "collection": collection,
            "tenant_id": owner.tenant_id,
            "user_id": owner.user_id,
            "limit": limit,
            **extra,
        }
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                f"SELECT {_FIELDS}, ts_rank_cd(tsv, to_tsquery('simple', %(query)s)) AS score "
                "FROM agent.rag_chunks "
                "WHERE collection = %(collection)s AND tenant_id = %(tenant_id)s AND user_id = %(user_id)s "
                f"AND tsv @@ to_tsquery('simple', %(query)s){documents} "
                "ORDER BY score DESC, ord LIMIT %(limit)s",
                params,
            )
            rows = await cursor.fetchall()
        return [
            ScoredChunk(
                **{k: r[k] for k in ("id", "attachment_id", "page", "section", "ord", "text")},
                score=float(r["score"]),
                text_rank=rank,
            )
            for rank, r in enumerate(rows, start=1)
        ]

    async def fetch(self, owner: Owner, collection: str, ids: Sequence[str]) -> dict[str, str]:
        wanted = [i for i in dict.fromkeys(ids) if _UUID.match(i)]
        if not wanted:
            return {}
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT id::text AS id, text FROM agent.rag_chunks "
                "WHERE collection = %s AND tenant_id = %s AND user_id = %s AND id = ANY(%s::uuid[])",
                (collection, owner.tenant_id, owner.user_id, wanted),
            )
            rows = await cursor.fetchall()
        return {r["id"]: r["text"] for r in rows}
