"""Citations (owner decision C1): a capability may supply citations, page attribution adds
its own before validate, and validate fails any citation that names none of this turn's
sources (`citation_not_in_sources`). No capability label or bracketed file name is put
before the answer model as something to copy."""

import pytest

from app.capabilities.contract import CapabilityOutput, Citation, Source
from app.errors import TurnError
from app.graph.deps import TurnContext
from app.graph.nodes.attribute import attribute_citations
from app.graph.nodes.validate import validate
from app.identity import Principal


def _runtime(services):
    from types import SimpleNamespace

    context = TurnContext(
        services=services, principal=Principal(user_id="u-c1"), request_id="req-c1", turn_id="turn-c1"
    )
    return SimpleNamespace(context=context, stream_writer=lambda _event: None)


def _state(output: CapabilityOutput, answer: str = "تمنح إجازة الأمومة عشرة أسابيع.") -> dict:
    return {
        "conversation_id": "c-1",
        "locale": "ar",
        "user_message": "كم مدة إجازة الأمومة؟",
        "draft_answer": answer,
        "shown_answer": answer,
        "answer_mode": "buffered",
        "intent": {"capabilities": ["docs"]},
        "capability_outputs": [{"capability": "docs", **output.model_dump(mode="json")}],
    }


async def _run(services, state: dict) -> dict:
    runtime = _runtime(services)
    state = {**state, **await attribute_citations(state, runtime)}
    await validate(state, runtime)
    return state


async def test_a_citation_a_capability_supplies_for_one_of_its_sources_is_kept(services):
    output = CapabilityOutput(
        findings="",
        sources=[Source(id="s-1", title="لائحة.pdf", kind="doc", document_id="d-1", page=2)],
        citations=[Citation(id="d-1", title="لائحة.pdf", document_id="d-1", pages=[2])],
        cap_state={},
    )
    state = await _run(services, _state(output))
    assert [(c["id"], c["pages"]) for c in state["citations"]] == [("d-1", [2])]


async def test_a_citation_naming_no_source_of_the_turn_fails_validation(services):
    output = CapabilityOutput(
        findings="",
        sources=[Source(id="s-1", title="لائحة.pdf", kind="doc", document_id="d-1", page=2)],
        citations=[Citation(id="d-elsewhere", title="ملف آخر.pdf", document_id="d-elsewhere", pages=[1])],
        cap_state={},
    )
    with pytest.raises(TurnError) as raised:
        await _run(services, _state(output))
    assert raised.value.code == "validation_failed" and "citation_not_in_sources" in str(raised.value)


@pytest.mark.parametrize("locale", ["ar", "en"])
def test_the_findings_block_carries_no_capability_label(prompts, locale):
    text = prompts.render(
        "context.findings", locale, findings=[{"capability": "docs", "findings": "ملاحظة"}]
    ).text
    assert "[docs]" not in text and "docs" not in text and "ملاحظة" in text


@pytest.mark.parametrize("locale", ["ar", "en"])
def test_the_answer_prompt_asks_for_no_source_names_or_labels(prompts, locale):
    text = prompts.render("answer.system", locale, tools=[], has_evidence=True, policy_hints=[]).text
    assert "مصدر كل معلومة" not in text and "name the source" not in text
    assert ("بين أقواس" in text) if locale == "ar" else ("in brackets" in text)
