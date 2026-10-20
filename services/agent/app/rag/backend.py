"""DocumentBackend (owner decision C6): the one interface a capability's tools use for
documents. `register` takes a stored upload, `status` says whether it is pending, indexed or
failed (with a reason), `search` returns the best chunks for a query, and `fetch` returns
chunk texts by reference.

Every call is scoped: a `DocumentScope` names the tenant, the user and the collections a call may
touch (I1, ADR-0202): the conversation's session collection and the knowledge bases the principal
may read and the turn selected. The broker builds it from the verified principal and the turn,
never from a model or a tool's arguments (S1). Every backend enforces it: nothing outside the
scope is ever returned, whatever ids a call names. A session collection is one user's; a
knowledge base collection is its tenant's.

What a backend returns is untrusted data. It passes the same runtime path as any tool output
(POST_TOOL with sanitize_untrusted first, then OUTPUT and DATA per principal); a backend
applies no policy itself, and chunks are stored as extracted (ADR-0158).

`InternalDocumentBackend` (app/rag/internal.py) is the pipeline of phase 04;
`MCPDocumentBackend` (app/rag/mcp.py) reaches a RAG server over MCP through the platform's MCP
client (I11). A capability's `config/<slug>.yaml` `backend` names the one its tools use.

A status may carry warnings (D7, ADR-0201): an indexed PDF with pages under the text threshold
says how many pages were not read."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.rag.collections import conversation_of, kind_of, session_collection
from app.rag.types import AttachmentStatus, FailureReason, Owner


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class DocumentScope:
    """Whose documents a call may touch: the principal's tenant and user, and the collections
    the broker allowed this turn (I1). An empty list of collections reaches nothing."""

    tenant_id: str
    user_id: str
    collections: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        collections = tuple(self.collections)
        for name in collections:
            if kind_of(name) is None:
                raise ValueError(f"unknown collection {name!r}")
        object.__setattr__(self, "collections", tuple(dict.fromkeys(collections)))

    @classmethod
    def session(cls, *, tenant_id: str, user_id: str, conversation_id: str) -> "DocumentScope":
        """One user's files in one conversation."""
        return cls(tenant_id=tenant_id, user_id=user_id, collections=(session_collection(conversation_id),))

    @property
    def owner(self) -> Owner:
        return Owner(tenant_id=self.tenant_id, user_id=self.user_id)

    @property
    def conversations(self) -> tuple[str, ...]:
        """The conversation ids of the scope's session collections."""
        return tuple(c for name in self.collections if (c := conversation_of(name)))

    def only(self, collection: str) -> "DocumentScope | None":
        """This scope narrowed to one of its collections; None for a collection outside it."""
        if collection not in self.collections:
            return None
        return DocumentScope(tenant_id=self.tenant_id, user_id=self.user_id, collections=(collection,))


class StoredFile(_Strict):
    """An upload the platform stored, as a backend receives it to index."""

    id: str
    conversation_id: str
    tenant_id: str
    user_id: str
    # Where the backend indexes it: the conversation's session collection for an upload (I1).
    collection: str
    name: str
    mime: str
    kind: str
    size: int = Field(ge=0)
    object_key: str


WarningCode = Literal["pages_without_text"]


class DocumentWarning(_Strict):
    """Something the user should know about a document that was indexed anyway (D7): `count`
    pages of a PDF held too little text to read; OCR is v2."""

    code: WarningCode
    count: int = Field(ge=1)


class DocumentStatus(_Strict):
    status: AttachmentStatus
    reason: FailureReason | None = None
    pages: int | None = Field(default=None, ge=0)
    warnings: list[DocumentWarning] = []


class SourceHit(_Strict):
    """One chunk a search found: its reference, its document, where it sits and its text."""

    ref: str = Field(description="Opaque chunk reference; `fetch` takes it back.")
    collection: str = Field(description="The collection the chunk was found in, one of the scope's.")
    document_id: str
    file_name: str
    page: int | None = Field(default=None, ge=1)
    section: str | None = None
    text: str
    score: float


class DocumentBackend(Protocol):
    async def register(self, stored: StoredFile) -> None:
        """Starts indexing a stored upload; returns at once (the file is `pending`)."""
        ...

    async def status(self, document_id: str, scope: DocumentScope) -> DocumentStatus | None:
        """None for a document outside the scope, another user's included."""
        ...

    async def search(self, query: str, scope: DocumentScope, k: int) -> list[SourceHit]:
        """At most `k` chunks of the scope's indexed documents, best first."""
        ...

    async def fetch(self, refs: Sequence[str], scope: DocumentScope) -> dict[str, str]:
        """The text of each reference inside the scope; a reference outside it is left out."""
        ...
