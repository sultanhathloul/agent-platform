"""Hostile files (owner decision R4): each ends failed with its reason, in a child process the
ingest service survives, while a chat turn running at the same time completes. Plus the walls
behind the archive check: python-docx resolves no entity, openpyxl parses through defusedxml,
and a child stopped by its time or memory limit ends the file as extraction_aborted."""

import asyncio
import uuid

import pytest

from app.api.runner import TurnRunner
from app.api.schemas import TurnRequest
from app.identity import Principal
from app.rag.backend import DocumentScope
from app.rag.config import load_docs_config
from app.rag.extract import ExtractionFailed
from app.rag.ingest.sandbox import run_sandboxed
from tests.fixtures.hostile import BUILDERS, EXPECTED, external_entity_docx
from tests.fixtures.ingestion import ingestion

pytestmark = pytest.mark.integration

MIMES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}


@pytest.mark.parametrize("name", list(BUILDERS))
async def test_a_hostile_file_fails_with_its_reason_while_a_turn_completes(services, litellm, name):
    principal = Principal(user_id=f"u-r4-{uuid.uuid4().hex[:6]}", permissions=("cap:general", "cap:docs"))
    conversation = str(uuid.uuid4())
    data = BUILDERS[name]()
    key = f"test/{conversation}/{uuid.uuid4()}"
    await services.attachments.objects.put(key, data, MIMES[name.rsplit(".", 1)[1]])
    owner = DocumentScope.session(
        tenant_id=principal.tenant_id, user_id=principal.user_id, conversation_id=conversation
    )
    record = await services.attachments.register(
        owner.owner,
        conversation,
        name=name,
        mime=MIMES[name.rsplit(".", 1)[1]],
        size=len(data),
        object_key=key,
    )

    async def chat() -> str:
        request = TurnRequest(
            conversation_id=str(uuid.uuid4()), principal=principal, message="ما عاصمة المملكة؟", locale="ar"
        )
        events = [e async for e in TurnRunner(services).stream(request, "req-r4")]
        return events[-1].type

    outcome, turn = await asyncio.gather(ingestion(services).pipeline.run(record.id), chat())
    assert turn == "done"
    assert outcome == "failed"
    status = await services.documents.status(record.id, owner)
    assert (status.status, status.reason) == ("failed", EXPECTED[name])


def test_python_docx_resolves_no_entity_even_past_the_archive_check():
    """The second wall: called on the XXE part directly, the parser leaves the entity unread."""
    import io
    import zipfile

    from docx.oxml.parser import parse_xml

    import app.rag.extract.docx  # noqa: F401  (installs the hardened parser)

    with zipfile.ZipFile(io.BytesIO(external_entity_docx())) as archive:
        xml = archive.read("word/document.xml")
    element = parse_xml(xml)
    assert "root:" not in "".join(element.itertext())


def test_openpyxl_parses_through_defusedxml():
    import openpyxl.xml

    import app.rag.extract.xlsx  # noqa: F401  (sets the switch before openpyxl reads it)

    assert openpyxl.xml.DEFUSEDXML is True


def test_a_child_out_of_time_or_memory_ends_the_file_as_extraction_aborted(config_dir):
    config = load_docs_config(config_dir)
    rows = BUILDERS["fifty-thousand-rows.xlsx"]()
    for sandbox in ({"timeout_seconds": 0.05}, {"memory_mb": 64}):
        tight = config.model_copy(
            update={
                "ingest": config.ingest.model_copy(
                    update={"sandbox": config.ingest.sandbox.model_copy(update=sandbox)}
                )
            }
        )
        with pytest.raises(ExtractionFailed) as failure:
            run_sandboxed("xlsx", rows, tight)
        assert failure.value.reason == "extraction_aborted", sandbox


def test_a_large_workbook_reaches_the_text_cap_not_the_memory_limit(config_dir):
    """Review round 1 of PR #17, finding 8 (ADR-0422): a 120k-row workbook, merged headers included, is read
    to its end in the sandbox child under config/docs.yaml's limits (640 MiB of address space, 60 s of CPU)
    and ends on the text cap (`text_too_long`, 1,000,000 characters), as on main; opened in full it peaked
    at 370 MB of heap and ended `extraction_aborted`."""
    from tests.fixtures.documents import labour_rows_xlsx_bytes

    with pytest.raises(ExtractionFailed) as failure:
        run_sandboxed("xlsx", labour_rows_xlsx_bytes(120_000), load_docs_config(config_dir))
    assert failure.value.reason == "text_too_long"


def test_a_workbook_under_the_text_cap_with_merged_headers_parses_in_the_sandbox(config_dir):
    from tests.fixtures.documents import labour_rows_xlsx_bytes

    parsed = run_sandboxed("xlsx", labour_rows_xlsx_bytes(12_000), load_docs_config(config_dir))
    assert parsed.chunks and "السعوديين › الذكور" in parsed.chunks[-1].text
