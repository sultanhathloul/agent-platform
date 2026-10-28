"""Reports and their versions (reports PR, I5, ADR-0373, ADR-0384): the rows in Postgres, the pages in
MinIO. Every read names the owner (tenant and user) and the conversation's owner is the report's: no
request reads another user's report, whatever id it sends. A new version is numbered under a row lock,
so two writers never take one number; its page is written first, its row after, so a row always names a
page that exists.

MinIO is reached through the minio client (urllib3) in a worker thread, as for uploads (attachments/
objects.py), with an urllib3 timeout per connect and read and the caller's deadline around each call
(rule 12c, ADR-0384): past the deadline the caller stops waiting, and the thread ends at its own
read timeout."""

import asyncio
import hashlib
import io
from datetime import datetime
from typing import Any

import urllib3
from minio import Minio
from minio.error import S3Error
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from app.db import Database
from app.resilience import deadline

# The urllib3 bounds of one MinIO call; the caller's deadline is shorter (report.yaml storage.store_s).
CONNECT_S = 3.0
READ_S = 10.0


class ReportUnavailable(RuntimeError):
    """The page store or the database could not answer in time."""


class VersionInfo(BaseModel):
    version: int
    title: str
    size: int
    sha256: str
    locale: str
    created_at: datetime
    figures: int = 0
    unverified: int = 0


class ReportRecord(BaseModel):
    id: str
    conversation_id: str
    title: str
    latest_version: int
    created_at: datetime
    updated_at: datetime
    versions: list[VersionInfo] = []


class VersionRecord(VersionInfo):
    report_id: str
    conversation_id: str
    object_key: str
    citations: list[dict[str, Any]] = []
    verified: list[str] = []


class PageStore:
    """The `artifacts` bucket, written and read by the agent's own MinIO user (ADR-0384)."""

    def __init__(self, endpoint: str, access_key: str, secret_key: str, bucket: str, *, secure: bool = False):
        http = urllib3.PoolManager(
            timeout=urllib3.Timeout(connect=CONNECT_S, read=READ_S),
            retries=urllib3.Retry(total=0),
            maxsize=10,
        )
        self._client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure, http_client=http)
        self.bucket = bucket

    async def put(self, key: str, page: bytes, timeout_s: float) -> None:
        def put() -> None:
            self._client.put_object(self.bucket, key, io.BytesIO(page), len(page), content_type="text/html; charset=utf-8")

        await self._bounded(put, timeout_s)

    async def get(self, key: str, timeout_s: float) -> bytes:
        def get() -> bytes:
            response = self._client.get_object(self.bucket, key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()

        return await self._bounded(get, timeout_s)

    @staticmethod
    async def _bounded(call, timeout_s: float):
        try:
            with deadline.within(timeout_s):
                return await asyncio.to_thread(call)
        except deadline.DeadlineExceeded as exc:
            if exc.inherited:
                raise
            raise ReportUnavailable("page store past its bound") from None
        except (S3Error, urllib3.exceptions.HTTPError, OSError) as exc:
            raise ReportUnavailable(type(exc).__name__) from None


def object_key(tenant_id: str, conversation_id: str, report_id: str, version: int) -> str:
    """I5: artifacts/<tenant>/<conversation>/<id>/v<n>.html (the bucket is `artifacts`)."""
    return f"{tenant_id}/{conversation_id}/{report_id}/v{version}.html"


_VERSION_COLUMNS = (
    "v.report_id::text AS report_id, r.conversation_id, v.version, v.title, v.object_key, v.size, v.sha256, "
    "v.locale, v.created_at, v.citations, v.verified, v.figures, v.unverified"
)


class ReportStore:
    def __init__(self, db: Database, pages: PageStore | None):
        self._db = db
        self.pages = pages

    async def latest_in(self, tenant_id: str, user_id: str, conversation_id: str) -> VersionRecord | None:
        """The conversation's latest report's latest version, the one a follow-up edits (D4)."""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                f"SELECT {_VERSION_COLUMNS} FROM agent.reports r JOIN agent.report_versions v "
                "ON v.report_id = r.id AND v.version = r.latest_version "
                "WHERE r.conversation_id = %s AND r.tenant_id = %s AND r.user_id = %s "
                "ORDER BY r.updated_at DESC LIMIT 1",
                (conversation_id, tenant_id, user_id),
            )
            row = await cursor.fetchone()
        return VersionRecord.model_validate(dict(row)) if row else None

    async def has_report(self, tenant_id: str, user_id: str, conversation_id: str) -> bool:
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT 1 FROM agent.reports WHERE conversation_id = %s AND tenant_id = %s AND user_id = %s "
                "AND latest_version > 0 LIMIT 1",
                (conversation_id, tenant_id, user_id),
            )
            return await cursor.fetchone() is not None

    async def save_version(
        self,
        *,
        report_id: str,
        new: bool,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        title: str,
        page: bytes,
        locale: str,
        turn_id: str,
        citations: list[dict],
        verified: list[str],
        figures: int,
        unverified: int,
        timeout_s: float,
    ) -> VersionRecord:
        """A new report (`new`) or the next version of one: the page first, then its row."""
        title = title[:300] or "-"
        async with self._db.connection() as conn, conn.transaction():
            if new:
                await conn.execute(
                    "INSERT INTO agent.reports (id, tenant_id, user_id, conversation_id, title) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (report_id, tenant_id, user_id, conversation_id, title),
                )
            cursor = await conn.execute(
                "SELECT latest_version FROM agent.reports WHERE id = %s AND tenant_id = %s AND user_id = %s "
                "AND conversation_id = %s FOR UPDATE",
                (report_id, tenant_id, user_id, conversation_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise ReportUnavailable("report not found for its owner")
            version = int(row["latest_version"]) + 1
            key = object_key(tenant_id, conversation_id, report_id, version)
            if self.pages is None:
                raise ReportUnavailable("no page store configured")
            await self.pages.put(key, page, timeout_s)
            await conn.execute(
                "INSERT INTO agent.report_versions (report_id, version, title, object_key, size, sha256, locale, "
                "turn_id, citations, verified, figures, unverified) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    report_id, version, title, key, len(page), hashlib.sha256(page).hexdigest(), locale,
                    turn_id, Jsonb(citations), Jsonb(verified), figures, unverified,
                ),
            )
            await conn.execute(
                "UPDATE agent.reports SET latest_version = %s, title = %s, updated_at = now() WHERE id = %s",
                (version, title, report_id),
            )
        record = await self.version(tenant_id, user_id, report_id, version)
        assert record is not None
        return record

    async def get(self, tenant_id: str, user_id: str, report_id: str) -> ReportRecord | None:
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT id::text AS id, conversation_id, title, latest_version, created_at, updated_at "
                "FROM agent.reports WHERE id = %s AND tenant_id = %s AND user_id = %s AND latest_version > 0",
                (report_id, tenant_id, user_id),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            cursor = await conn.execute(
                "SELECT version, title, size, sha256, locale, created_at, figures, unverified "
                "FROM agent.report_versions WHERE report_id = %s ORDER BY version",
                (report_id,),
            )
            versions = [VersionInfo.model_validate(dict(v)) for v in await cursor.fetchall()]
        return ReportRecord.model_validate({**dict(row), "versions": versions})

    async def in_conversation(self, tenant_id: str, user_id: str, conversation_id: str) -> list[ReportRecord]:
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT id::text AS id FROM agent.reports WHERE conversation_id = %s AND tenant_id = %s "
                "AND user_id = %s AND latest_version > 0 ORDER BY created_at",
                (conversation_id, tenant_id, user_id),
            )
            ids = [row["id"] for row in await cursor.fetchall()]
        found = [await self.get(tenant_id, user_id, report_id) for report_id in ids]
        return [r for r in found if r is not None]

    async def version(self, tenant_id: str, user_id: str, report_id: str, version: int) -> VersionRecord | None:
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                f"SELECT {_VERSION_COLUMNS} FROM agent.reports r JOIN agent.report_versions v ON v.report_id = r.id "
                "WHERE r.id = %s AND v.version = %s AND r.tenant_id = %s AND r.user_id = %s",
                (report_id, version, tenant_id, user_id),
            )
            row = await cursor.fetchone()
        return VersionRecord.model_validate(dict(row)) if row else None

    async def page(self, record: VersionRecord, timeout_s: float) -> bytes:
        if self.pages is None:
            raise ReportUnavailable("no page store configured")
        page = await self.pages.get(record.object_key, timeout_s)
        if hashlib.sha256(page).hexdigest() != record.sha256:
            raise ReportUnavailable("stored page does not match its record")
        return page
