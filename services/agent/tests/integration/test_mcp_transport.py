"""MCP transport through the broker and the document facade (fix session 2026-10-18; ADR-0230 to
ADR-0233).

1. Every operation of MCPDocumentBackend (register, status, search, fetch) goes through
   `broker.transport` and leaves one audit entry and one `tool.exec` span, with the server, the
   tool, the status, the latency, the byte sizes and the argument HMAC, and never their text.
2. No capability can reach a backend or a connection: nothing in a RuntimeContext or a ToolContext
   is one, directions and general get no document facade, and docs reaches only its backends.
3. A file is searched on the backend that indexed it, across a switch of the uploads' backend.
4. Status reads never stall a turn: with a rag server that hangs, a general turn in a
   conversation holding that server's files completes within the status bound of its normal time,
   and the files show their last known status."""

import asyncio
import copy
import dataclasses
import json
import logging
import time
import uuid
from collections.abc import Mapping

import pytest
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

from app.api.runner import TurnRunner
from app.api.schemas import TurnRequest
from app.capabilities.contract import RuntimeContext
from app.graph.nodes import capabilities as capability_node
from app.identity import Principal
from app.mcp.client import MCPClient, ServerConnection
from app.mcp.config import MCPConfig, resolve
from app.rag.access import DocumentAccess, DocumentFacade, Held
from app.rag.backend import DocumentScope, StoredFile
from app.rag.mcp import MCPDocumentBackend
from app.tools.broker import ToolBroker, auditing
from app.tools.registry import ToolRegistry
from app.tools.spec import ToolContext
from tests.fixtures.backends import Harness
from tests.fixtures.fake_backend import FakeDocumentBackend
from tests.fixtures.mcp_server import free_port, tool_hashes
from tests.fixtures.rag_ref import rag_ref_backend, rag_ref_broker, rag_ref_client  # noqa: F401 (fixtures)
from tests.fixtures.replace import replacing

pytestmark = pytest.mark.integration

LEAVE = ["المادة الثانية: تمنح الموظفة إجازة أمومة مدتها عشرة أسابيع براتب كامل."]
PATERNITY = ["المادة الرابعة: يمنح الموظف إجازة أبوة مدتها ثلاثة أيام عمل براتب كامل."]
AUDIT_FIELDS = {"call_id", "tool", "caller", "status", "error", "latency_ms", "retries", "server",
                "request_bytes", "response_bytes", "args_hmac"}  # fmt: skip


def _scope(conversation: str | None = None) -> DocumentScope:
    return DocumentScope.session(
        tenant_id="default",
        user_id=f"u-tr-{uuid.uuid4().hex[:8]}",
        conversation_id=conversation or str(uuid.uuid4()),
    )


@pytest.fixture
def mcp_backend(request):
    """The reference RAG server's backend (an async session fixture, reached from a sync one)."""
    return request.getfixturevalue("rag_ref_backend")


@pytest.fixture
def mcp_broker(request):
    return request.getfixturevalue("rag_ref_broker")


def _transport_spans(spans) -> list:
    return [
        s
        for s in spans.get_finished_spans()
        if s.name == "tool.exec" and (s.attributes or {}).get("tool.transport")
    ]


# ------------------------------------------------------------------ 1. transport through the broker


async def test_each_operation_leaves_one_audit_entry_and_one_span_content_free(
    services, spans, caplog, mcp_backend
):
    backend = mcp_backend
    harness = Harness("mcp", services, backend)
    scope = _scope()
    document = await harness.upload(scope, "لائحة.pdf", LEAVE)
    assert await harness.ingest(document) == "indexed"
    stored = StoredFile(
        id=str(uuid.uuid4()), conversation_id=scope.conversations[0], tenant_id=scope.tenant_id,
        user_id=scope.user_id, collection=scope.collections[0], name="ملف.txt", mime="text/plain",
        kind="txt", size=4, object_key=f"test/{uuid.uuid4()}",
    )  # fmt: skip
    query = "إجازة الأمومة عشرة أسابيع"

    sink: list[dict] = []
    spans.clear()
    with auditing("docs", sink.append):
        await backend.register(stored)
        assert (await backend.status(document, scope)).status == "indexed"
        hits = await backend.search(query, scope, 8)
        texts = await backend.fetch([h.ref for h in hits], scope)
    assert hits and texts
    assert [e["tool"] for e in sink] == ["rag.register", "rag.status", "rag.search", "rag.fetch"]
    for entry in sink:
        assert set(entry) == AUDIT_FIELDS  # the audit record's fields, nothing more
        assert entry["caller"] == "docs" and entry["status"] == "ok"
        assert entry["server"] == "rag" and len(entry["args_hmac"]) == 64
        assert entry["request_bytes"] > 0 and entry["response_bytes"] > 0 and entry["latency_ms"] >= 0
    traced = _transport_spans(spans)
    assert [s.attributes["tool.name"] for s in traced] == [e["tool"] for e in sink]
    for span in traced:
        a = span.attributes
        assert a["tool.status"] == "ok" and a["tool.server"] == "rag" and "tool.latency_ms" in a
        assert a["tool.request_bytes"] > 0 and a["tool.response_bytes"] > 0 and len(a["tool.args_hmac"]) == 64
    leaked = json.dumps([dict(s.attributes) for s in traced], ensure_ascii=False) + json.dumps(
        sink, ensure_ascii=False
    )
    assert "الأمومة" not in leaked and "ملف.txt" not in leaked and stored.object_key not in leaked

    # Outside a turn (an upload's registration, the file list): the entry goes to the audit log.
    caplog.set_level(logging.INFO, logger="audit.transport")
    caplog.clear()
    await backend.status(document, scope)
    [record] = [r for r in caplog.records if r.name == "audit.transport"]
    entry = json.loads(record.getMessage())
    assert entry["tool"] == "rag.status" and entry["caller"] == "transport" and set(entry) >= AUDIT_FIELDS


async def test_transport_reaches_only_transport_operations(mcp_broker):
    broker = mcp_broker
    from app.tools.spec import ToolFailure

    scope = _scope()
    with pytest.raises(ToolFailure) as unknown:
        await broker.transport("rag", "drop_everything", {}, scope)
    assert unknown.value.code == "not_a_transport_operation"
    with pytest.raises(ToolFailure) as named:  # an injected field named by the adapter is refused
        await broker.transport("rag", "status", {"document_id": "x", "tenant": "other"}, scope)
    assert named.value.code == "invalid_arguments"
    with pytest.raises(ToolFailure) as elsewhere:
        await broker.transport("nowhere", "status", {"document_id": "x"}, scope)
    assert elsewhere.value.code == "tool_unavailable"


# ------------------------------------------------------------------ 2. no capability reaches a backend


def _reachable(root: object) -> list[object]:
    """Every object reachable from `root` through attributes, fields, mappings and sequences (not
    through a function's closure: the reflection limit of ADR-0053)."""
    seen: set[int] = set()
    found: list[object] = []
    stack = [root]
    while stack:
        value = stack.pop()
        if id(value) in seen or isinstance(value, str | bytes | int | float | bool | type(None)):
            continue
        seen.add(id(value))
        found.append(value)
        if isinstance(value, Mapping):
            stack += list(value.keys()) + list(value.values())
        elif isinstance(value, list | tuple | set | frozenset):
            stack += list(value)
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            stack += [getattr(value, f.name) for f in dataclasses.fields(value)]
        stack += list(getattr(value, "__dict__", {}).values())
        stack += [
            getattr(value, slot) for slot in getattr(type(value), "__slots__", ()) if hasattr(value, slot)
        ]
        if hasattr(value, "__self__"):  # a bound method holds its instance
            stack.append(value.__self__)
    return found


FORBIDDEN = (ServerConnection, MCPClient, ToolBroker, DocumentAccess, FakeDocumentBackend, MCPDocumentBackend)


def _forbidden(root: object) -> list[str]:
    from app.graph.deps import Services
    from app.rag.internal import InternalDocumentBackend

    kinds = (*FORBIDDEN, InternalDocumentBackend, Services)
    return sorted({type(v).__name__ for v in _reachable(root) if isinstance(v, kinds)})


async def test_no_runtime_context_reaches_a_backend_or_a_connection(services, litellm, monkeypatch):
    captured: list[RuntimeContext] = []
    original = capability_node._invoke_isolated

    async def capture(cap, cap_input, runtime_context):
        captured.append(runtime_context)
        return await original(cap, cap_input, runtime_context)

    monkeypatch.setattr(capability_node, "_invoke_isolated", capture)
    for slug, router in [
        ("general", '{"capabilities": ["general"]}'),
        ("directions", '{"capabilities": ["directions"]}'),
    ]:
        swapped, _ = replacing(
            services, {"router": router, "extractor": '{"origin": null, "destination": null}'}
        )
        principal = Principal(
            user_id=f"u-rc-{uuid.uuid4().hex[:6]}", permissions=("cap:general", "cap:directions")
        )
        message = "ودني من برج المملكة" if slug == "directions" else "ما عاصمة فرنسا؟"
        request = TurnRequest(
            conversation_id=str(uuid.uuid4()), principal=principal, message=message, locale="ar"
        )
        events = [e async for e in TurnRunner(swapped).stream(request, f"req-rc-{slug}")]
        assert events[-1].type == "done", events[-1]
    assert {rc.slug for rc in captured} >= {"general", "directions"}
    for runtime_context in captured:
        assert not hasattr(runtime_context, "documents")
        assert _forbidden(runtime_context) == [], runtime_context.slug


def test_directions_and_general_get_no_facade_and_docs_reaches_only_its_backends(services):
    broker = services.broker
    principal = Principal(user_id="u-facade", permissions=("cap:docs", "cap:directions"))
    for slug in ("directions", "general"):
        scope = broker.scope_for(slug, principal, conversation_id=str(uuid.uuid4()))
        assert scope.collections == () and broker.facade_for(slug, scope) is None
        assert _forbidden(ToolContext(scope=scope, documents=broker.facade_for(slug, scope))) == []
    scope = broker.scope_for("docs", principal, conversation_id=str(uuid.uuid4()))
    facade = broker.facade_for("docs", scope)
    assert isinstance(facade, DocumentFacade)
    assert {f.name for f in dataclasses.fields(facade)} == {"search", "fetch", "status"}
    assert _forbidden(ToolContext(scope=scope, documents=facade)) == []


class _Spy(FakeDocumentBackend):
    def __init__(self, name: str):
        super().__init__()
        self.name, self.searched = name, []

    async def search(self, query, scope, k):
        self.searched.append(scope.collections)
        return await super().search(query, scope, k)


async def test_docs_searches_its_configured_backend_and_the_backends_its_files_name():
    configured, other, unused = _Spy("configured"), _Spy("other"), _Spy("unused")
    records: dict[str, list[Held]] = {}

    async def lookup(tenant_id, user_id, conversation_id):
        return records.get(conversation_id, [])

    access = DocumentAccess(
        {"configured": configured, "other": other, "unused": unused},
        configured={"docs": "configured"},
        default="unused",
        records=lookup,
    )
    from app.tools.spec import ToolScope

    conversation = str(uuid.uuid4())
    scope = ToolScope(tenant_id="default", user_id="u-spy", collections=(f"sess_{conversation}",))
    await access.facade("docs", scope).search("سؤال", 8)
    assert (configured.searched, other.searched, unused.searched) == ([scope.collections], [], [])
    records[conversation] = [Held("d1", "other")]
    await access.facade("docs", scope).search("سؤال", 8)
    assert other.searched == [scope.collections] and unused.searched == []


# ------------------------------------------------------------------ 3. the backend that indexed it


async def test_files_uploaded_before_and_after_a_backend_switch_are_searched_and_cited(services, litellm):
    """Uploads switch backend (DOCS_BACKEND); earlier files stay on theirs and are still searched
    and cited (ADR-0232)."""
    fake = FakeDocumentBackend()
    principal = Principal(user_id=f"u-sw-{uuid.uuid4().hex[:8]}", permissions=("cap:general", "cap:docs"))
    conversation = str(uuid.uuid4())
    scope = DocumentScope.session(
        tenant_id=principal.tenant_id, user_id=principal.user_id, conversation_id=conversation
    )
    before = Harness("internal", services)
    first = await before.upload(scope, "الأمومة.pdf", LEAVE)
    assert await before.ingest(first) == "indexed"

    # The switch: new uploads go to `fake`; the internal backend still holds the first file.
    backends = {**services.backends, "fake": fake}
    attachments = copy.copy(services.attachments)
    attachments.documents, attachments.backends = fake, backends
    access = services.broker.documents
    switched = dataclasses.replace(
        services,
        attachments=attachments,
        documents=fake,
        backends=backends,
        broker=services.broker.with_documents(
            DocumentAccess(backends, configured={"docs": "fake"}, default="fake", records=access.records)
        ),
    )
    second = await attachments.register(
        scope.owner,
        conversation,
        name="الأبوة.txt",
        mime="text/plain",
        size=10,
        object_key=f"test/{uuid.uuid4()}",
    )
    fake.index(second.id, PATERNITY)
    records = {r.id: r.backend for r in await services.attachments.store.list_for(scope.owner, conversation)}
    assert records == {first: "internal", second.id: "fake"}

    answer = (
        "تمنح الموظفة إجازة أمومة مدتها عشرة أسابيع براتب كامل، "
        "ويمنح الموظف إجازة أبوة مدتها ثلاثة أيام عمل براتب كامل."
    )
    replaced, _ = replacing(switched, {"router": '{"capabilities": ["docs"]}', "reasoning": answer})
    request = TurnRequest(
        conversation_id=conversation,
        principal=principal,
        message="كم مدة إجازة الأمومة وإجازة الأبوة؟",
        locale="ar",
    )
    events = [e async for e in TurnRunner(replaced).stream(request, "req-switch")]
    assert events[-1].type == "done", events[-1]
    state = await replaced.graph.aget_state({"configurable": {"thread_id": conversation}})
    [docs] = [o for o in state.values["capability_outputs"] if o["capability"] == "docs"]
    assert {(s["document_id"], s["backend"]) for s in docs["sources"]} >= {
        (first, "internal"),
        (second.id, "fake"),
    }
    cited = {getattr(c, "document_id", None) for c in events[-1].data.citations}
    assert {first, second.id} <= cited


# ------------------------------------------------------------------ 4. status reads never stall a turn


class _Registered(BaseModel):
    registered: bool


class _Status(BaseModel):
    found: bool
    status: str | None = None
    reason: str | None = None
    pages: int | None = None
    warnings: list = []


class HangingRag:
    """A rag-shaped server whose `status` answers until it is told to hang (then sleeps 60 s)."""

    def __init__(self):
        self.port, self.hang = free_port(), False

    def app(self):
        mcp = FastMCP("hanging-rag", host="127.0.0.1", port=self.port, log_level="WARNING")
        server = self

        @mcp.tool()
        async def register(
            document_id: str,
            name: str,
            mime: str,
            kind: str,
            size: int,
            object_key: str,
            collection: str,
            tenant: str,
            user: str,
            collections: list[str],
        ) -> _Registered:
            """Registers."""
            return _Registered(registered=True)

        @mcp.tool()
        async def status(document_id: str, tenant: str, user: str, collections: list[str]) -> _Status:
            """Status."""
            if server.hang:
                await asyncio.sleep(60)
            return _Status(found=True, status="indexed", pages=3)

        return mcp.streamable_http_app()


@pytest.fixture
async def hanging_rag():
    import uvicorn

    rag = HangingRag()
    server = uvicorn.Server(uvicorn.Config(rag.app(), host="127.0.0.1", port=rag.port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    yield rag
    server.should_exit = server.force_exit = True
    await asyncio.wait_for(task, 10)


def _rag_config(hashes: dict[str, str]) -> MCPConfig:
    injected = {
        "tenant": "principal.tenant_id",
        "user": "principal.user_id",
        "collections": "scope.collections",
    }
    tools = [
        {"name": name, "remote_name": name, "class": "transport",
         "risk": "write" if name == "register" else "read", "label": {"ar": "ملف", "en": "File"},
         "input_schema_sha256": hashes[name], "injected": injected}
        for name in ("register", "status")
    ]  # fmt: skip
    return MCPConfig.model_validate(
        {
            "servers": [
                {
                    "id": "rag",
                    "url_env": "RAG_URL",
                    "internal": True,
                    "profiles": ["default"],
                    "timeout_s": 30,
                    "tools": tools,
                }
            ]
        }  # fmt: skip
    )


async def _general_turn_s(services, principal, conversation) -> float:
    started = time.monotonic()
    request = TurnRequest(
        conversation_id=conversation, principal=principal, message="ما عاصمة فرنسا؟", locale="ar"
    )
    events = [e async for e in TurnRunner(services).stream(request, f"req-{uuid.uuid4().hex[:6]}")]
    assert events[-1].type == "done", events[-1]
    return time.monotonic() - started


async def test_a_hung_rag_server_never_stalls_a_general_turn_and_files_show_their_last_status(
    services, hanging_rag, litellm
):
    url = f"http://127.0.0.1:{hanging_rag.port}/mcp"
    hashes = await tool_hashes(url)
    client = MCPClient(
        resolve(_rag_config(hashes), profile="default", allow_external=False, environ={"RAG_URL": url}),
        hash_key="test-content-hash-key",
        client_version="test",
    )
    client.start()
    assert await client.wait_connected(10) and client.servers["rag"].disabled == {}
    try:
        backend = MCPDocumentBackend(ToolBroker(ToolRegistry(), services.guard, mcp=client).transport, "rag")
        backends = {**services.backends, "mcp": backend}
        attachments = copy.copy(services.attachments)
        attachments.documents, attachments.backends = backend, backends
        held = dataclasses.replace(services, attachments=attachments, backends=backends)
        principal = Principal(
            user_id=f"u-hang-{uuid.uuid4().hex[:8]}", permissions=("cap:general", "cap:docs")
        )
        router = {"router": '{"capabilities": ["general"]}'}
        normal, _ = replacing(services, router)
        baseline = min([await _general_turn_s(normal, principal, str(uuid.uuid4())) for _ in range(2)])

        conversation = str(uuid.uuid4())
        for name in ("أ.txt", "ب.txt", "ج.txt"):
            await attachments.register(
                principal_owner(principal),
                conversation,
                name=name,
                mime="text/plain",
                size=4,
                object_key=f"test/{uuid.uuid4()}",
            )
        listed = await attachments.with_status(principal_owner(principal), conversation)
        assert [r.status for r in listed] == ["indexed"] * 3  # read once and remembered

        hanging_rag.hang = True
        replaced, _ = replacing(held, router)
        elapsed = await _general_turn_s(replaced, principal, conversation)
        bound = services.attachments.status_timeout_s
        assert elapsed < baseline + bound + 1.5, (elapsed, baseline)
        listed = await attachments.with_status(principal_owner(principal), conversation)
        assert [r.status for r in listed] == ["indexed"] * 3  # the last known status, not pending
    finally:
        await client.stop()


def principal_owner(principal: Principal):
    from app.rag.types import Owner

    return Owner(tenant_id=principal.tenant_id, user_id=principal.user_id)
