"""DocumentBackend (owner decision C6): the one interface a capability's tools use for
documents. `register` takes a stored upload, `status` says whether it is pending, indexed or
failed (with a reason), `search` returns the best chunks for a query, and `fetch` returns
chunk texts by reference.

Every call is scoped: a `DocumentScope` names the tenant, the user and the conversation, and
the broker builds it from the verified principal and the conversation in state, never from
a model or a tool's arguments (S1). Every backend enforces it: nothing outside the scope is
ever returned, whatever ids a call names.

What a backend returns is untrusted data. It passes the same runtime path as any tool output
(POST_TOOL with sanitize_untrusted first, then OUTPUT and DATA per principal); a backend
applies no policy itself, and chunks are stored as extracted (ADR-0158).

`InternalDocumentBackend` (app/rag/internal.py) is the pipeline of phase 04; a future backend
(the owner's RAG/OCR service over MCP) implements this protocol and nothing else changes.
`config/docs.yaml` `backend` selects one."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.rag.types import AttachmentStatus, FailureReason, Owner


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class DocumentScope:
    """Whose documents a call may touch: one user's files in one conversation."""

    tenant_id: str
    user_id: str
    conversation_id: str

    @property
    def owner(self) -> Owner:
        return Owner(tenant_id=self.tenant_id, user_id=self.user_id)


class StoredFile(_Strict):
    """An upload the platform stored, as a backend receives it to index."""

    id: str
    conversation_id: str
    tenant_id: str
    user_id: str
    name: str
    mime: str
    kind: str
    size: int = Field(ge=0)
    object_key: str


class DocumentStatus(_Strict):
    status: AttachmentStatus
    reason: FailureReason | None = None
    pages: int | None = Field(default=None, ge=0)


class SourceHit(_Strict):
    """One chunk a search found: its reference, its document, where it sits and its text."""

    ref: str = Field(description="Opaque chunk reference; `fetch` takes it back.")
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
