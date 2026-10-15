"""One harness per DocumentBackend for the contract suite and the docs capability's tests (C6):
`upload` stores an attachment record through the platform's attachment service, which hands
it to the backend; `ingest` brings it to a terminal status the way that backend does.

- internal: a generated PDF (pages, in visual order as real producers write Arabic), a
  Markdown file or a DOCX (sections) in MinIO, indexed by the phase 04 pipeline;
- fake: the in-memory backend of tests/fixtures/fake_backend.py, indexed directly;
- mcp: the same files in MinIO, registered with the reference RAG MCP server through
  MCPDocumentBackend and indexed by that server (phase 05, D2); `ingest` waits for its status."""

import asyncio
import uuid
from collections.abc import Sequence

from app.rag.backend import DocumentScope
from tests.fixtures.documents import docx_bytes, pdf_bytes
from tests.fixtures.fake_backend import FakeDocumentBackend, with_backend
from tests.fixtures.ingestion import ingestion

BACKENDS = ("internal", "fake")
CONTRACT_BACKENDS = ("internal", "fake", "mcp")


class Harness:
    def __init__(self, name: str, services, mcp_backend=None):
        self.name = name
        self.fake = None
        if name == "fake":
            self.fake = FakeDocumentBackend()
            self.services = with_backend(services, self.fake)
        elif name == "mcp":
            self.services = with_backend(services, mcp_backend)
        else:
            self.services = services
        self.backend = self.services.documents
        self._content: dict[str, tuple] = {}

    async def upload(
        self,
        scope: DocumentScope,
        name: str,
        pages: Sequence[str],
        *,
        sections: Sequence[str] | None = None,
        broken: bool = False,
    ) -> str:
        """`pages` are page texts (lines split on newlines), or section bodies when `sections`
        names their headings (a Markdown file without pages, or a DOCX when `name` ends so)."""
        if broken:
            data, mime = b"%PDF-1.4\nnot really a pdf", "application/pdf"
        elif sections and name.endswith(".docx"):
            blocks = [
                block
                for heading, text in zip(sections, pages, strict=True)
                for block in ((heading, 1), (text, "p"))
            ]
            data = docx_bytes(blocks)
            mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif sections:
            body = "\n\n".join(f"# {heading}\n{text}" for heading, text in zip(sections, pages, strict=True))
            data, mime = body.encode("utf-8"), "text/markdown"
        else:
            # Laid out as a bidi-aware producer lays Arabic out (python-bidi, owner decision A2).
            data, mime = pdf_bytes([page.split("\n") for page in pages], visual=True), "application/pdf"
        key = f"test/{scope.conversations[0]}/{uuid.uuid4()}"
        if self.name in ("internal", "mcp"):
            await self.services.attachments.objects.put(key, data, mime)
        record = await self.services.attachments.register(
            scope.owner, scope.conversations[0], name=name, mime=mime, size=len(data), object_key=key
        )
        self._content[record.id] = (pages, sections, broken)
        return record.id

    async def ingest(self, document_id: str) -> str:
        pages, sections, broken = self._content[document_id]
        if self.name == "internal":
            return await ingestion(self.services).pipeline.run(document_id)
        if self.name == "mcp":
            return await self._indexed_by_server(document_id)
        if broken:
            self.fake.fail(document_id, "corrupt_file")
            return "failed"
        self.fake.index(document_id, pages, sections=sections or ())
        return "indexed"

    async def _indexed_by_server(self, document_id: str, timeout: float = 120.0) -> str:
        """The MCP server indexes in the background: wait for a terminal status."""
        record = await self.services.attachments.store.by_id(document_id)
        scope = DocumentScope(
            tenant_id=record.tenant_id, user_id=record.user_id, collections=(record.collection,)
        )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            status = await self.backend.status(document_id, scope)
            if status is not None and status.status != "pending":
                return status.status
            await asyncio.sleep(0.2)
        raise TimeoutError(f"the MCP server did not index {document_id} in {timeout}s")
