"""VectorStore on pgvector in the agent's database (README §2, ADR-0131, ADR-0132).

Every statement names (collection, tenant_id, user_id). A chunk goes with its attachment
record (a foreign key, ON DELETE CASCADE; owner decision S4), or, in a knowledge base collection,
with its KB document record (`kb_column`, phase 08): the store returns either as the chunk's
`attachment_id`, its document. Similarity is computed only against vectors of the query's
dimension, so the embedding model can change without a migration (ADR-0058's reasoning)."""

import re
from collections.abc import Sequence

from app.db import Database
from app.rag.collections import kind_of
from app.rag.types import Chunk, Owner, ScoredChunk
from app.text.arabic import normalize_for_search

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _vector(values: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


def _documents(column: str, document_ids: Sequence[str] | None) -> tuple[str, dict]:
    if document_ids is None:
        return "", {}
    return f" AND {column} = ANY(%(documents)s::uuid[])", {"documents": list(document_ids)}


_TABLE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")
_COLUMN = re.compile(r"^[a-z_][a-z0-9_]*$")


class PgVectorStore:
    def __init__(self, db: Database, *, table: str = "agent.rag_chunks", kb_column: str = "kb_document_id"):
        """`table`: the agent's `agent.rag_chunks`, or another service's own chunk table, so two
        backends never share rows (ADR-0211). `kb_column`: the column naming a knowledge base
        chunk's document, `kb_document_id` in the agent's table (phase 08); a table that keeps every
        document in `attachment_id` names that."""
        if not _TABLE.match(table):
            raise ValueError(f"invalid chunk table {table!r}")
        if not _COLUMN.match(kb_column):
            raise ValueError(f"invalid document column {kb_column!r}")
        self._db = db
        self._table = table
        self._kb_column = kb_column

    def _column(self, collection: str) -> str:
        """The column naming a chunk's document in this collection."""
        return self._kb_column if kind_of(collection) == "kb" else "attachment_id"

    def _fields(self, collection: str) -> str:
        return (
            f"id::text AS id, {self._column(collection)}::text AS attachment_id, collection, page, section, ord, text"
        )

    def _scope(self, collections: str | Sequence[str]) -> tuple[str, list[str]]:
        """The collections one search covers, all of one kind (one document column), and that column's
        collection to name it by."""
        names = [collections] if isinstance(collections, str) else list(dict.fromkeys(collections))
        if not names:
            raise ValueError("a search needs at least one collection")
        if len({self._column(name) for name in names}) > 1:
            raise ValueError("one search covers collections of one kind")
        return names[0], names

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
        column = self._column(collection)
        await conn.execute(
            f"DELETE FROM {self._table} WHERE collection = %s AND tenant_id = %s AND user_id = %s "
            f"AND {column} = %s",
            (collection, owner.tenant_id, owner.user_id, document_id),
        )
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            await conn.execute(
                f"INSERT INTO {self._table} (collection, {column}, user_id, tenant_id, page, section, "
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
        collection: str | Sequence[str],
        embedding: Sequence[float],
        *,
        limit: int,
        document_ids: Sequence[str] | None = None,
    ) -> list[ScoredChunk]:
        """`collection`: one collection, or several of one kind searched as one (cross-KB retrieval)."""
        collection, names = self._scope(collection)
        documents, extra = _documents(self._column(collection), document_ids)
        params = {
            "vector": _vector(embedding),
            "collections": names,
            "tenant_id": owner.tenant_id,
            "user_id": owner.user_id,
            "dimensions": len(embedding),
            "limit": limit,
            **extra,
        }
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                f"SELECT {self._fields(collection)}, 1 - (embedding <=> %(vector)s::vector) AS score "
                f"FROM {self._table} "
                "WHERE collection = ANY(%(collections)s) AND tenant_id = %(tenant_id)s AND user_id = %(user_id)s "
                f"AND vector_dims(embedding) = %(dimensions)s{documents} "
                "ORDER BY embedding <=> %(vector)s::vector, ord LIMIT %(limit)s",
                params,
            )
            rows = await cursor.fetchall()
        return [
            ScoredChunk(
                **{k: r[k] for k in ("id", "attachment_id", "collection", "page", "section", "ord", "text")},
                score=float(r["score"]),
                vector_rank=rank,
            )
            for rank, r in enumerate(rows, start=1)
        ]

    async def text_search(
        self,
        owner: Owner,
        collection: str | Sequence[str],
        terms: Sequence[str],
        *,
        limit: int,
        document_ids: Sequence[str] | None = None,
    ) -> list[ScoredChunk]:
        """`collection`: one collection, or several of one kind searched as one (cross-KB retrieval)."""
        collection, names = self._scope(collection)
        # Terms are normalized words (\w only), so joining them with | is a valid tsquery.
        words = sorted({t for t in terms if t and t.isalnum()})
        if not words:
            return []
        documents, extra = _documents(self._column(collection), document_ids)
        params = {
            "query": " | ".join(words),
            "collections": names,
            "tenant_id": owner.tenant_id,
            "user_id": owner.user_id,
            "limit": limit,
            **extra,
        }
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                f"SELECT {self._fields(collection)}, ts_rank_cd(tsv, to_tsquery('simple', %(query)s)) AS score "
                f"FROM {self._table} "
                "WHERE collection = ANY(%(collections)s) AND tenant_id = %(tenant_id)s AND user_id = %(user_id)s "
                f"AND tsv @@ to_tsquery('simple', %(query)s){documents} "
                "ORDER BY score DESC, ord LIMIT %(limit)s",
                params,
            )
            rows = await cursor.fetchall()
        return [
            ScoredChunk(
                **{k: r[k] for k in ("id", "attachment_id", "collection", "page", "section", "ord", "text")},
                score=float(r["score"]),
                text_rank=rank,
            )
            for rank, r in enumerate(rows, start=1)
        ]

    async def purge(self, owner: Owner, collection: str) -> int:
        """Deletes every chunk of the owner's collection (a deleted knowledge base, I1); returns how many."""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                f"DELETE FROM {self._table} WHERE collection = %s AND tenant_id = %s AND user_id = %s",
                (collection, owner.tenant_id, owner.user_id),
            )
            return cursor.rowcount

    async def fetch(self, owner: Owner, collection: str, ids: Sequence[str]) -> dict[str, str]:
        wanted = [i for i in dict.fromkeys(ids) if _UUID.match(i)]
        if not wanted:
            return {}
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                f"SELECT id::text AS id, text FROM {self._table} "
                "WHERE collection = %s AND tenant_id = %s AND user_id = %s AND id = ANY(%s::uuid[])",
                (collection, owner.tenant_id, owner.user_id, wanted),
            )
            rows = await cursor.fetchall()
        return {r["id"]: r["text"] for r in rows}
