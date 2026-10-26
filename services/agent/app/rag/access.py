"""What a capability's tool may do with documents (ADR-0231): a facade the broker hands to a tool
handler in its ToolContext, bound to the scope the broker built from the verified principal and
the turn. It offers `search`, `fetch` and `status` and nothing else: no backend, no transport,
no connection is one of its fields, and a capability whose manifest declares no
`documents.collections` gets none at all.

A file is searched on the backend that indexed it (ADR-0232): each attachment record keeps the
backend it was registered with, so a session collection is searched on the backends its files
were registered with, grouped per backend, and the results are merged by score. A knowledge base
collection is searched on the backend the calling capability's `config/<slug>.yaml` names (with
its `<SLUG>_BACKEND` override, ADR-0224). Switching that backend changes new uploads only.
A file whose registration failed is never searched: a backend may hold its chunks (it indexed the
file and failed the call), and they are dropped from what it returns (ADR-0254); `fetch` drops the
text of a chunk this facade found in such a file too (ADR-0265).
Knowledge base hits (phase 08) carry their document's catalog entry (KB name, title, publisher, date,
the version that supersedes it, D3, I4), read from the platform's KB records whatever backend
found them; a hit whose document is not an indexed document of a live KB of the scope is dropped
(a deleted KB is unreachable, I7); and the latest version of a source is preferred over the merged
hits (app/rag/versions.py), which an MCP backend's server cannot do for the platform.
Capability-agnostic: no capability, backend or server is named here."""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import NamedTuple

from app.rag.backend import DocumentBackend, DocumentScope, DocumentStatus, SourceHit
from app.rag.collections import conversation_of, kind_of
from app.rag.versions import prefer_latest
from app.tools.spec import ToolFailure

log = logging.getLogger(__name__)


class Held(NamedTuple):
    """One attachment record as the facade needs it: its id, the backend it was registered with,
    and whether that registration failed (ADR-0254)."""

    id: str
    backend: str
    register_failed: bool = False


# (tenant_id, user_id, conversation_id) -> that owner's records of the conversation.
RecordsLookup = Callable[[str, str, str], Awaitable[list[Held]]]
# (tenant_id, KB slugs) -> the indexed documents of those live KBs, by id (phase 08): each entry has
# at least `superseded_by` (the id of the indexed version that supersedes it, or None).
CatalogLookup = Callable[[str, Sequence[str]], Awaitable[Mapping[str, object]]]

_KB_PREFIX = "kb_"


@dataclass(frozen=True)
class FoundChunk:
    """A chunk a search found and the backend that found it (the evidence step fetches its text
    there, ADR-0202), with its knowledge base document's catalog entry, if it is one's (phase 08)."""

    backend: str
    hit: SourceHit
    document: object | None = None


@dataclass(frozen=True)
class DocumentFacade:
    """Scope-bound document operations for one tool call. Every field is a function; none is a
    backend or a connection (ADR-0231)."""

    search: Callable[[str, int], Awaitable[list[FoundChunk]]]
    fetch: Callable[..., Awaitable[dict[str, str]]]
    status: Callable[[str], Awaitable[DocumentStatus | None]]


class DocumentAccess:
    """The broker's view of the document backends: every backend by name, the one each
    capability's configuration names, and the attachment records that say which backend holds
    each upload."""

    def __init__(
        self,
        backends: Mapping[str, DocumentBackend],
        *,
        configured: Mapping[str, str] | None = None,
        default: str | None = None,
        records: RecordsLookup | None = None,
        catalog: CatalogLookup | None = None,
        superseded_keep: int = 1,
    ):
        self.backends = dict(backends)
        self.configured = dict(configured or {})
        self.default = default
        self.records = records
        self.catalog = catalog
        self.superseded_keep = superseded_keep

    def backend_name(self, caller: str) -> str | None:
        return self.configured.get(caller) or self.default

    def facade(self, caller: str, scope) -> DocumentFacade | None:
        """`scope`: the ToolScope the broker built for `caller` this turn. None when the caller's
        manifest declares no collection kind, or this platform has no backend for it."""
        name = self.backend_name(caller)
        if name is None or name not in self.backends:
            return None
        tenant_id, user_id = scope.tenant_id, scope.user_id
        collections = tuple(scope.collections)

        def scoped(names: Sequence[str]) -> DocumentScope:
            return DocumentScope(tenant_id=tenant_id, user_id=user_id, collections=tuple(names))

        # The document of every chunk this facade's searches returned, by reference: fetch reads
        # it to drop what belongs to a file whose registration failed (ADR-0265).
        found_in: dict[str, str] = {}

        async def placed() -> tuple[dict[str, list[str]], set[str]]:
            """The scope's collections per backend (a session collection on its files' backends),
            and the files whose registration failed."""
            groups: dict[str, list[str]] = defaultdict(list)
            failed: set[str] = set()
            for collection in collections:
                conversation = conversation_of(collection)
                held: set[str] = set()
                if conversation is not None and self.records is not None:
                    records = await self.records(tenant_id, user_id, conversation)
                    held = {r.backend for r in records if not r.register_failed}
                    failed |= {r.id for r in records if r.register_failed}
                for backend in sorted(held or {name}):
                    groups[backend].append(collection)
            return groups, failed

        async def search(query: str, k: int) -> list[FoundChunk]:
            placement, failed = await placed()
            groups = [(b, c) for b, c in placement.items() if b in self.backends]
            if not groups:
                return []
            outcomes = await asyncio.gather(
                *(self.backends[b].search(query, scoped(c), k) for b, c in groups), return_exceptions=True
            )
            found: list[FoundChunk] = []
            failures: list[BaseException] = []
            for (backend, _), outcome in zip(groups, outcomes, strict=True):
                if isinstance(outcome, BaseException):
                    failures.append(outcome)
                    log.warning("document search on backend %s failed (%s)", backend, type(outcome).__name__)
                    continue
                found += [
                    FoundChunk(backend=backend, hit=hit) for hit in outcome if hit.document_id not in failed
                ]
            if failures and not found:
                first = failures[0]
                raise first if isinstance(first, ToolFailure) else ToolFailure("tool_unavailable")
            # Backends rank alike (the same fusion of the same pipeline): merged by score, best first.
            found.sort(key=lambda chunk: chunk.hit.score, reverse=True)
            found = await catalogued(found)
            found_in.update({chunk.hit.ref: chunk.hit.document_id for chunk in found})
            return found[:k]

        async def catalogued(found: list[FoundChunk]) -> list[FoundChunk]:
            """Knowledge base hits with their catalog entries, the latest version of a source first;
            a hit the catalog does not hold is dropped (phase 08, D3, I7)."""
            slugs = [c[len(_KB_PREFIX) :] for c in collections if kind_of(c) == "kb"]
            if self.catalog is None or not slugs or not any(kind_of(c.hit.collection) == "kb" for c in found):
                return found  # a platform without a KB catalog (a test's backend) keeps what it found
            entries = await self.catalog(tenant_id, slugs)
            kept = [
                FoundChunk(backend=c.backend, hit=c.hit, document=entries.get(c.hit.document_id))
                if kind_of(c.hit.collection) == "kb"
                else c
                for c in found
                if kind_of(c.hit.collection) != "kb" or c.hit.document_id in entries
            ]
            newer = {
                doc: later
                for doc, entry in entries.items()
                if (later := getattr(entry, "superseded_by", None)) is not None
            }
            return prefer_latest(kept, lambda c: c.hit.document_id, newer, keep=self.superseded_keep)

        async def fetch(refs: Sequence[str], *, backend: str | None = None) -> dict[str, str]:
            chosen = self.backends.get(backend or name)
            if chosen is None:
                return {}
            texts = await chosen.fetch(refs, scoped(collections))
            _, failed = await placed()
            return {ref: text for ref, text in texts.items() if found_in.get(ref) not in failed}

        async def status(document_id: str) -> DocumentStatus | None:
            for collection in collections:
                conversation = conversation_of(collection)
                if conversation is None or self.records is None:
                    continue
                for record in await self.records(tenant_id, user_id, conversation):
                    if record.id == document_id and record.register_failed:
                        return DocumentStatus(
                            status="failed", reason="register_failed", pages=None, warnings=[]
                        )
                    if record.id == document_id and record.backend in self.backends:
                        return await self.backends[record.backend].status(document_id, scoped((collection,)))
            return None

        return DocumentFacade(search=search, fetch=fetch, status=status)
