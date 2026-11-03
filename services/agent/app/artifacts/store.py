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

from app.artifacts import library
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
    general: int = 0


class ReportRecord(BaseModel):
    id: str
    conversation_id: str
    title: str
    latest_version: int
    created_at: datetime
    updated_at: datetime
    versions: list[VersionInfo] = []
    # A report made from another conversation's report (owner decision 3, ADR-0433): the version it began from.
    based_on: dict[str, Any] | None = None


class LibraryHit(BaseModel):
    """One report of the caller's library, as report_search and the web page's list return it: its latest
    matching version, never the page (owner decision 3, ADR-0432)."""

    report_id: str
    version: int
    latest_version: int
    title: str
    subject: str = ""
    conversation_id: str
    sections: list[dict[str, Any]] = []
    sources: list[str] = []
    source_kinds: list[str] = []
    created_at: datetime
    updated_at: datetime
    score: float = 0.0


class Reference(BaseModel):
    report_id: str
    version: int
    title: str
    conversation_id: str


class VersionRecord(VersionInfo):
    report_id: str
    conversation_id: str
    object_key: str
    citations: list[dict[str, Any]] = []
    # Each sourced figure's label, {canonical figure: {kind, text}} (ADR-0388), kept by a follow-up (D4).
    figure_labels: dict[str, dict[str, str]] = {}
    # The passages behind the citations, as source references without text (ADR-0402): an edit fetches them
    # again from the knowledge base, under its own turn's permissions.
    passages: list[dict[str, Any]] = []
    # The checked spec the page was rendered from (ADR-0406); None for a version written before it.
    spec: dict[str, Any] | None = None


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
    "v.locale, v.created_at, v.citations, v.figure_labels, v.figures, v.general, v.passages, v.spec"
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
        labels: dict[str, dict[str, str]],
        figures: int,
        general: int,
        timeout_s: float,
        passages: list[dict] | None = None,
        spec: dict | None = None,
        based_on: dict | None = None,
    ) -> VersionRecord:
        """A new report (`new`) or the next version of one: the page first, then its row and its library
        entry in the same transaction (ADR-0432). `based_on` ({report_id, version}): a new report made from
        another conversation's report, which stays as it was (ADR-0433)."""
        title = title[:300] or "-"
        async with self._db.connection() as conn, conn.transaction():
            if new:
                based = based_on or {}
                await conn.execute(
                    "INSERT INTO agent.reports (id, tenant_id, user_id, conversation_id, title, based_on_report, "
                    "based_on_version) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (report_id, tenant_id, user_id, conversation_id, title, based.get("report_id"),
                     based.get("version")),
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
                "turn_id, citations, figure_labels, figures, general, passages, spec) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    report_id, version, title, key, len(page), hashlib.sha256(page).hexdigest(), locale,
                    turn_id, Jsonb(citations), Jsonb(labels), figures, general, Jsonb(passages or []),
                    Jsonb(spec) if spec is not None else None,
                ),
            )
            await conn.execute(
                "UPDATE agent.reports SET latest_version = %s, title = %s, updated_at = now() WHERE id = %s",
                (version, title, report_id),
            )
            await _index(
                conn, report_id=report_id, version=version, tenant_id=tenant_id, user_id=user_id,
                conversation_id=conversation_id, title=title, spec=spec, citations=citations, general=general,
                created_at=None,
            )
        record = await self.version(tenant_id, user_id, report_id, version)
        assert record is not None
        return record

    async def get(self, tenant_id: str, user_id: str, report_id: str) -> ReportRecord | None:
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT id::text AS id, conversation_id, title, latest_version, created_at, updated_at, "
                "based_on_report, based_on_version "
                "FROM agent.reports WHERE id = %s AND tenant_id = %s AND user_id = %s AND latest_version > 0",
                (report_id, tenant_id, user_id),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            row = dict(row)
            based_report, based_version = row.pop("based_on_report"), row.pop("based_on_version")
            if based_report is not None:
                row["based_on"] = {"report_id": str(based_report), "version": based_version}
            cursor = await conn.execute(
                "SELECT version, title, size, sha256, locale, created_at, figures, general "
                "FROM agent.report_versions WHERE report_id = %s ORDER BY version",
                (report_id,),
            )
            versions = [VersionInfo.model_validate(dict(v)) for v in await cursor.fetchall()]
        return ReportRecord.model_validate({**row, "versions": versions})

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

    # ------------------------------------------------------------------ the library (ADR-0432, ADR-0433)

    async def in_conversation_latest(
        self, tenant_id: str, user_id: str, conversation_id: str, limit: int
    ) -> list[VersionRecord]:
        """The conversation's reports, each at its latest version, the most recently changed first: the
        index every turn's context lists (decision 1)."""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                f"SELECT {_VERSION_COLUMNS} FROM agent.reports r JOIN agent.report_versions v "
                "ON v.report_id = r.id AND v.version = r.latest_version "
                "WHERE r.conversation_id = %s AND r.tenant_id = %s AND r.user_id = %s "
                "ORDER BY r.updated_at DESC LIMIT %s",
                (conversation_id, tenant_id, user_id, limit),
            )
            rows = await cursor.fetchall()
        return [VersionRecord.model_validate(dict(row)) for row in rows]

    async def search(
        self,
        tenant_id: str,
        user_id: str,
        terms: list[str],
        *,
        limit: int,
        conversation_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        source: str | None = None,
        before: datetime | None = None,
    ) -> list[LibraryHit]:
        """The caller's reports, one hit per report at its best-ranked matching version (with no terms: its
        latest version, newest first). Owner in every WHERE; `conversation_id` keeps one conversation's;
        `source` is `general`, `file` or `kb:<slug>` (the page's filter); `before` pages by updated_at."""
        words = sorted({t for t in terms if t and t.isalnum()})
        params: dict[str, Any] = {"tenant_id": tenant_id, "user_id": user_id, "limit": limit}
        where = ["l.tenant_id = %(tenant_id)s", "l.user_id = %(user_id)s", "r.tenant_id = %(tenant_id)s",
                 "r.user_id = %(user_id)s"]
        if words:
            params["query"] = " | ".join(words)
            where.append("l.tsv @@ to_tsquery('simple', %(query)s)")
            rank = "ts_rank_cd(l.tsv, to_tsquery('simple', %(query)s))"
        else:
            where.append("l.version = r.latest_version")
            rank = "0.0::real"
        if conversation_id is not None:
            params["conversation_id"] = conversation_id
            where.append("l.conversation_id = %(conversation_id)s")
        if since is not None:
            params["since"] = since
            where.append("r.updated_at >= %(since)s")
        if until is not None:
            params["until"] = until
            where.append("r.updated_at < %(until)s")
        if before is not None:
            params["before"] = before
            where.append("r.updated_at < %(before)s")
        if source:
            params["source"] = source
            where.append("%(source)s = ANY(l.source_kinds)")
        sql = (
            "SELECT * FROM (SELECT DISTINCT ON (l.report_id) l.report_id::text AS report_id, l.version, "
            "r.latest_version, l.title, l.subject, l.conversation_id, l.sections, l.sources, l.source_kinds, "
            f"l.created_at, r.updated_at, {rank} AS score "
            "FROM agent.report_library l JOIN agent.reports r ON r.id = l.report_id "
            f"WHERE {' AND '.join(where)} "
            f"ORDER BY l.report_id, {rank + ' DESC, ' if words else ''}l.version DESC) hits "
            "ORDER BY score DESC, updated_at DESC LIMIT %(limit)s"
        )
        async with self._db.connection() as conn:
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()
        return [LibraryHit.model_validate({**dict(row), "score": float(row["score"])}) for row in rows]

    async def owned_version(self, tenant_id: str, user_id: str, report_id: str, version: int | None) -> VersionRecord | None:
        """One of the caller's report versions, in any of their conversations; None for any other id."""
        if version is None:
            report = await self.get(tenant_id, user_id, report_id)
            if report is None:
                return None
            version = report.latest_version
        return await self.version(tenant_id, user_id, report_id, version)

    async def refer(self, tenant_id: str, user_id: str, conversation_id: str, report_id: str, version: int) -> None:
        """The conversation read one of the caller's reports (ADR-0433); its own reports are not recorded."""
        async with self._db.connection() as conn:
            await conn.execute(
                "INSERT INTO agent.report_references (conversation_id, tenant_id, user_id, report_id, version) "
                "SELECT %s, %s, %s, r.id, %s FROM agent.reports r WHERE r.id = %s AND r.tenant_id = %s "
                "AND r.user_id = %s AND r.conversation_id <> %s "
                "ON CONFLICT (conversation_id, report_id) DO UPDATE SET version = EXCLUDED.version, "
                "referenced_at = now()",
                (conversation_id, tenant_id, user_id, version, report_id, tenant_id, user_id, conversation_id),
            )

    async def referenced(self, tenant_id: str, user_id: str, conversation_id: str) -> Reference | None:
        """The conversation's latest reference to another conversation's report of the caller's."""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT x.report_id::text AS report_id, x.version, v.title, r.conversation_id "
                "FROM agent.report_references x JOIN agent.reports r ON r.id = x.report_id "
                "JOIN agent.report_versions v ON v.report_id = x.report_id AND v.version = x.version "
                "WHERE x.conversation_id = %s AND x.tenant_id = %s AND x.user_id = %s "
                "AND r.tenant_id = %s AND r.user_id = %s ORDER BY x.referenced_at DESC LIMIT 1",
                (conversation_id, tenant_id, user_id, tenant_id, user_id),
            )
            row = await cursor.fetchone()
        return Reference.model_validate(dict(row)) if row else None

    async def has_reference(self, tenant_id: str, user_id: str, conversation_id: str) -> bool:
        return await self.referenced(tenant_id, user_id, conversation_id) is not None

    async def backfill(self, batch: int) -> int:
        """Indexes up to `batch` versions written before the library existed (ADR-0432); idempotent. Returns
        how many it indexed; 0 when none is left."""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT v.report_id::text AS report_id, v.version, v.title, v.citations, v.general, v.spec, "
                "v.created_at, r.tenant_id, r.user_id, r.conversation_id "
                "FROM agent.report_versions v JOIN agent.reports r ON r.id = v.report_id "
                "LEFT JOIN agent.report_library l ON l.report_id = v.report_id AND l.version = v.version "
                "WHERE l.report_id IS NULL ORDER BY v.created_at LIMIT %s",
                (batch,),
            )
            rows = await cursor.fetchall()
            for row in rows:
                async with conn.transaction():
                    await _index(
                        conn, report_id=row["report_id"], version=row["version"], tenant_id=row["tenant_id"],
                        user_id=row["user_id"], conversation_id=row["conversation_id"], title=row["title"],
                        spec=row["spec"], citations=row["citations"] or [], general=row["general"],
                        created_at=row["created_at"],
                    )
        return len(rows)


async def _index(
    conn, *, report_id: str, version: int, tenant_id: str, user_id: str, conversation_id: str, title: str,
    spec: dict | None, citations: list[dict], general: int, created_at: datetime | None,
) -> None:
    """One version's library entry (ADR-0432): linear in the spec, capped by its schema and the index's CHECKs."""
    await conn.execute(
        "INSERT INTO agent.report_library (report_id, version, tenant_id, user_id, conversation_id, title, subject, "
        "sections, sources, source_kinds, text_norm, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, now())) "
        "ON CONFLICT (report_id, version) DO NOTHING",
        (
            report_id, version, tenant_id, user_id, conversation_id, (title or "-")[:300],
            library.subject(spec), Jsonb(library.section_titles(spec)), Jsonb(library.source_names(citations)),
            library.source_kinds(citations, general), library.index_text(title, spec, citations), created_at,
        ),
    )
