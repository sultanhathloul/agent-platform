"""The report library as a tool sees it (report library PR, owner decisions 2 and 3; ADR-0431, ADR-0432).

The broker builds one facade per tool call, bound to the scope it built from the verified principal and the
turn (S1): the caller's tenant and user, and the turn's conversation. A tool names a report by id and a
query, never an owner: every read goes through ReportStore with that owner in its WHERE, so another user's
report is the same nothing as a report that does not exist. Each call is bounded by the tool's own timeout
(the broker's `deadline.within`), and what it returns is size-capped by config/library.yaml (rule 12)."""

from dataclasses import dataclass

from app.artifacts.library import LibraryConfig, readable
from app.artifacts.store import LibraryHit, ReportStore
from app.rag.retrieve import query_terms


@dataclass(frozen=True)
class ReadResult:
    report_id: str
    version: int
    latest_version: int
    title: str
    conversation_id: str
    this_conversation: bool
    text: str


class ReportLibraryFacade:
    def __init__(
        self, store: ReportStore, config: LibraryConfig, *, tenant_id: str, user_id: str, conversation_id: str
    ):
        self._store = store
        self._config = config
        self._tenant = tenant_id
        self._user = user_id
        self._conversation = conversation_id

    @property
    def conversation_id(self) -> str:
        return self._conversation

    def terms(self, query: str) -> list[str]:
        search = self._config.search
        return query_terms(query, search.stopwords.all())[: search.max_query_terms]

    async def search(
        self, query: str, *, this_conversation: bool = False, limit: int | None = None
    ) -> list[LibraryHit]:
        """The caller's reports matching `query` (with no subject word: the latest), at most max_results."""
        size = min(limit or self._config.search.max_results, self._config.search.max_results)
        return await self._store.search(
            self._tenant, self._user, self.terms(query), limit=size,
            conversation_id=self._conversation if this_conversation else None,
        )

    async def read(self, report_id: str, version: int | None, locale: str) -> ReadResult | None:
        record = await self._store.owned_version(self._tenant, self._user, report_id, version)
        if record is None:
            return None
        report = await self._store.get(self._tenant, self._user, report_id)
        latest = report.latest_version if report else record.version
        text = readable(
            title=record.title, version=record.version, spec=record.spec, citations=record.citations,
            locale=locale, config=self._config.read, general=not record.citations,
        )
        if record.conversation_id != self._conversation:
            # A report of another conversation of the caller's: the next report turn here may make a new
            # version of it in this conversation (ADR-0433).
            await self._store.refer(self._tenant, self._user, self._conversation, report_id, record.version)
        return ReadResult(
            report_id=record.report_id, version=record.version, latest_version=latest, title=record.title,
            conversation_id=record.conversation_id, this_conversation=record.conversation_id == self._conversation,
            text=text,
        )
