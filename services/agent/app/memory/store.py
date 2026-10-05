"""Memory items and the per-user memory switch in Postgres (README §8, ADR-0058).

Every statement names (tenant_id, user_id): no method reads, lists, matches or
deletes an item outside the scope it is given."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.db import Database
from app.memory.config import MemoryConfig
from app.memory.types import MemoryHit, MemoryScope


@dataclass(frozen=True)
class MemoryItem:
    id: str
    kind: str
    text: str
    confidence: float
    created_at: datetime
    expires_at: datetime | None


def _vector(values: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(value)) for value in values) + "]"


class MemoryStore:
    def __init__(self, db: Database, config: MemoryConfig):
        self._db = db
        self._config = config

    async def enabled(self, scope: MemoryScope) -> bool:
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT enabled FROM agent.memory_settings WHERE tenant_id = %s AND user_id = %s",
                (scope.tenant_id, scope.user_id),
            )
            row = await cursor.fetchone()
        return row["enabled"] if row else self._config.enabled_by_default

    async def set_enabled(self, scope: MemoryScope, enabled: bool) -> bool:
        async with self._db.connection() as conn:
            await conn.execute(
                "INSERT INTO agent.memory_settings (tenant_id, user_id, enabled) VALUES (%s, %s, %s) "
                "ON CONFLICT (tenant_id, user_id) "
                "DO UPDATE SET enabled = EXCLUDED.enabled, updated_at = now()",
                (scope.tenant_id, scope.user_id, enabled),
            )
        return enabled

    async def add(
        self,
        scope: MemoryScope,
        *,
        kind: str,
        text: str,
        confidence: float,
        embedding: Sequence[float],
        source_turn: str,
    ) -> str:
        ttl = self._config.ttl_days
        expires_at = datetime.now(UTC) + timedelta(days=ttl) if ttl else None
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "INSERT INTO agent.memory_items "
                "(tenant_id, user_id, kind, text, embedding, confidence, source_turn, expires_at) "
                "VALUES (%s, %s, %s, %s, %s::vector, %s, %s, %s) RETURNING id",
                (
                    scope.tenant_id,
                    scope.user_id,
                    kind,
                    text,
                    _vector(embedding),
                    confidence,
                    source_turn,
                    expires_at,
                ),
            )
            row = await cursor.fetchone()
        return str(row["id"])

    async def list_items(self, scope: MemoryScope) -> list[MemoryItem]:
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT id, kind, text, confidence, created_at, expires_at FROM agent.memory_items "
                "WHERE tenant_id = %s AND user_id = %s AND (expires_at IS NULL OR expires_at > now()) "
                "ORDER BY created_at DESC",
                (scope.tenant_id, scope.user_id),
            )
            rows = await cursor.fetchall()
        return [
            MemoryItem(str(r["id"]), r["kind"], r["text"], r["confidence"], r["created_at"], r["expires_at"])
            for r in rows
        ]

    async def delete(self, scope: MemoryScope, item_id: str) -> bool:
        """False when no item with this id belongs to the scope, including another user's item."""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "DELETE FROM agent.memory_items WHERE id = %s AND tenant_id = %s AND user_id = %s",
                (item_id, scope.tenant_id, scope.user_id),
            )
            return cursor.rowcount == 1

    async def delete_from_turn(self, scope: MemoryScope, source_turn: str, *, conn) -> int:
        """Deletes the scope's items extracted from `source_turn`, on the caller's
        connection, so a retract removes them in its own transaction (ADR-0071)."""
        cursor = await conn.execute(
            "DELETE FROM agent.memory_items WHERE tenant_id = %s AND user_id = %s AND source_turn = %s",
            (scope.tenant_id, scope.user_id, source_turn),
        )
        return cursor.rowcount

    async def search(
        self, scope: MemoryScope, embedding: Sequence[float], *, limit: int, min_similarity: float
    ) -> list[MemoryHit]:
        """The scope's live items most similar to `embedding` (cosine), at least
        `min_similarity`, compared only with vectors of the same dimension."""
        params = {
            "vector": _vector(embedding),
            "tenant_id": scope.tenant_id,
            "user_id": scope.user_id,
            "dimensions": len(embedding),
            "min_similarity": min_similarity,
            "limit": limit,
        }
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT id, kind, text, similarity FROM ("
                "  SELECT id, kind, text, created_at, 1 - (embedding <=> %(vector)s::vector) AS similarity"
                "  FROM agent.memory_items"
                "  WHERE tenant_id = %(tenant_id)s AND user_id = %(user_id)s"
                "    AND (expires_at IS NULL OR expires_at > now())"
                "    AND vector_dims(embedding) = %(dimensions)s"
                ") AS scored WHERE similarity >= %(min_similarity)s "
                "ORDER BY similarity DESC, created_at DESC LIMIT %(limit)s",
                params,
            )
            rows = await cursor.fetchall()
        return [MemoryHit(str(r["id"]), r["kind"], r["text"], float(r["similarity"])) for r in rows]
