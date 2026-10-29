"""A report turn draws from one kind of source (ADR-0398) and names no uncited source."""

from app.artifacts.writer import named_sources, sourced_turn

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
