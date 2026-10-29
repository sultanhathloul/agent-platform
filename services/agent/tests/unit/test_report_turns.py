"""A report turn draws from one kind of source (ADR-0398), names no uncited source, and is an edit of the
conversation's report only when the router says so (ADR-0400)."""

from app.artifacts.writer import named_sources, sourced_turn
from app.graph.router import parse_decision

ROUTE = '{"capabilities": ["general"], "skill": null, "output": "report"%s, "complexity": "low", "risk": "low"}'


def test_the_router_says_edit_only_with_a_current_report_and_only_an_explicit_true_is_one():
    edit = parse_decision(ROUTE % ', "edit": true', ["general"], output=True, current=True)
    new = parse_decision(ROUTE % ', "edit": false', ["general"], output=True, current=True)
    missing = parse_decision(ROUTE % "", ["general"], output=True, current=True)
    word = parse_decision(ROUTE % ', "edit": "yes"', ["general"], output=True, current=True)
    assert edit.edit is True and new.edit is False and missing.edit is False and word.edit is False
    # No report in the conversation: nothing to edit, and the field is not read.
    assert parse_decision(ROUTE % ', "edit": true', ["general"], output=True).edit is None
    assert parse_decision(ROUTE % ', "edit": true', ["general"]).edit is None


def test_a_turn_is_sourced_by_its_own_kbs_files_or_findings_only():
    general = {"capability": "general", "findings": "", "sources": []}
    assert not sourced_turn({"capability_outputs": [general]}, [])
    assert sourced_turn({"capability_outputs": [general], "selected_kbs": ["stats-gov"]}, [])
    assert sourced_turn({"capability_outputs": [general], "turn_attachment_ids": ["a1"]}, [])
    assert sourced_turn({"capability_outputs": [{"capability": "kb", "findings": "notes", "sources": []}]}, [])
    assert sourced_turn({"capability_outputs": [general]}, [{"ref": "c1"}])


STATS = {"kb_name": {"ar": "إحصاءات الهيئة العامة للإحصاء", "en": "GASTAT statistics"},
         "title": "نشرة سوق العمل", "publisher": "الهيئة العامة للإحصاء"}


def test_the_page_names_no_source_this_turn_did_not_cite():
    known = ["إحصاءات الهيئة العامة للإحصاء", "GASTAT statistics", "نشرة سوق العمل", "الهيئة العامة للإحصاء", "IMF"]
    page = "لوحة نادي الهلال\nالمصدر: الهيئة العامة للإحصاء\nعدد الألقاب 66"
    assert named_sources(page, known, []) == ["الهيئة العامة للإحصاء"]
    # Cited this turn: allowed, and so is the KB's name, which holds the publisher's.
    assert named_sources(page, known, [STATS]) == []
    assert named_sources("لوحة سوق العمل من إحصاءات الهيئة العامة للإحصاء", known, [STATS]) == []
    # A name too short to tell from a word is not read as a source.
    assert named_sources("IMF data", known, []) == []
