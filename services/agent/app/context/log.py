"""Recorded context packages behind GET /v1/debug/context/{turn_id} (README §7,
ADR-0059). Packages carry user content, so nothing is written unless
DEBUG_CONTEXT_ENABLED is true."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

from app.context.package import ContextPackage
from app.db import Database


@dataclass(frozen=True)
class RecordedPackage:
    node: str
    created_at: datetime
    package: dict[str, Any]


@dataclass(frozen=True)
class RecordedTurn:
    conversation_id: str
    packages: list[RecordedPackage]


class ContextLog:
    def __init__(self, db: Database, *, enabled: bool):
        self._db = db
        self.enabled = enabled

    async def record(self, *, turn_id: str, conversation_id: str, package: ContextPackage) -> None:
        """The exact package a model call received, in assembly order."""
        if not self.enabled or not conversation_id:
            return
        document = {**package.model_dump(mode="json"), "accounting": package.accounting()}
        async with self._db.connection() as conn:
            await conn.execute(
                "INSERT INTO agent.context_packages (turn_id, conversation_id, node, package) "
                "VALUES (%s, %s, %s, %s)",
                (turn_id, conversation_id, package.node, Jsonb(document)),
            )

    async def turn(self, turn_id: str) -> RecordedTurn | None:
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT conversation_id, node, package, created_at FROM agent.context_packages "
                "WHERE turn_id = %s ORDER BY id",
                (turn_id,),
            )
            rows = await cursor.fetchall()
        if not rows:
            return None
        return RecordedTurn(
            conversation_id=str(rows[0]["conversation_id"]),
            packages=[RecordedPackage(r["node"], r["created_at"], r["package"]) for r in rows],
        )
