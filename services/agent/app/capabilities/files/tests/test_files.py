"""The files capability's own rules (my-files PR, owner decision 2; ADR-0446): when a message refers to an earlier
file, which pages it names, and the title an answer cites. Arabic, Gulf and English cases (CLAUDE.md rule 7)."""

from pathlib import Path

import pytest
import yaml

from app.capabilities.contract import Manifest
from app.graph.nodes.route import router_candidates
from app.text.arabic import normalize
from app.capabilities.files.graph import has_any, named_pages, refers_to_earlier_file, search_query
from app.capabilities.files.tools import title_of

MANIFEST = Manifest.model_validate(
    yaml.safe_load((Path(__file__).resolve().parents[1] / "manifest.yaml").read_text(encoding="utf-8"))
)


@pytest.fixture(scope="module")
def config(config_dir):
    return yaml.safe_load((config_dir / "files.yaml").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "message",
    [
        "رفعت لك ملف تحليل الدم، كم الهيموجلوبين؟",
        "الملف اللي أرسلته في محادثة ثانية، وش فيه عن الإجازات؟",
        "رفعتلك تقرير المختبر أمس، وش نتيجة السكر؟",
        "المستند اللي بعثته لك قبل، لخصه لي",
        "في المحادثة السابقة أرسلت لك عقد الإيجار، كم مدته؟",
        "وش قال الملف اللي حملته الأسبوع الماضي عن الراتب؟",
        "I uploaded my lab report earlier, what was my hemoglobin?",
        "The document I sent you in another conversation: what does it say about leave?",
        "Look at the file I shared before and summarize it",
        "رفعت لك ملف الفحوصات في محادثة ثانية",
        "I've sent you the report earlier, what does it say about leave?",
    ],
)
def test_a_message_that_refers_to_an_earlier_file(message, config):
    assert refers_to_earlier_file(message, config)


@pytest.mark.parametrize(
    "message",
    [
        "كم المعدل الطبيعي للهيموجلوبين؟",
        "اشرح لي ما هو الهيموجلوبين",
        "ما هي عاصمة اليابان؟",
        "كيف أكتب ملفًا تعريفيًا لشركتي؟",  # a file word, but nothing sent or earlier
        "مستقبل الطاقة المتجددة",  # «قبل» inside another word
        "Please present the hemoglobin ranges",  # "sent" inside "present"
        "How do I update my profile?",  # "file" inside "profile"
        "What is a PDF?",
        # An upload or a send word alone never refers to a file (ADR-0449).
        "رفعت سعر المنتج، كيف أحسب الربح؟",
        "حملت التطبيق أمس",
        "رفعت الجلسة وحملت الأوراق",
        "How do I upload a file?",
        "The ministry sent the document yesterday",
        # A file or an earlier word with nothing sent or uploaded.
        "الملف في المحادثة السابقة",
        "What was in the previous document?",
    ],
)
def test_a_message_that_refers_to_no_earlier_file(message, config):
    assert not refers_to_earlier_file(message, config)


def test_words_match_whole_words_and_phrases_with_arabic_prefixes():
    assert has_any("وبالملف المرفق", ["ملف"])
    assert has_any("في المحادثة الثانية", ["المحادثه الثانيه"])
    assert not has_any("مستقبل", ["قبل"])
    assert not has_any("represented", ["sent"])


def test_named_pages():
    assert named_pages("وش في الصفحة 2؟") == [2]
    assert named_pages("صفحة ٣ و٤") == [3, 4]
    assert named_pages("ص 4-6") == [4, 5, 6]
    assert named_pages("pages 2 and 5") == [2, 5]
    assert named_pages("لخص الملف") == []


def test_the_title_an_answer_cites_has_no_extension():
    assert title_of("تحليل-الدم.pdf") == "تحليل-الدم"
    assert title_of("Lab Report.DOCX") == "Lab Report"
    assert title_of("ملاحظات") == "ملاحظات"


def test_the_manifest_reads_the_callers_files_guarded_as_docs():
    assert MANIFEST.access == ["user_files"] and MANIFEST.guard_as == ["docs"]
    assert MANIFEST.tools == ["file_search", "file_read"]
    assert [rule.when for rule in MANIFEST.routing_hints.rules] == ["keyword_candidate"]


def test_the_search_query_keeps_the_subject_and_drops_the_reference(config):
    assert search_query("رفعت لك تقرير المختبر في محادثة ثانية، كم كانت نسبة الهيموجلوبين؟", config) == (
        "تقرير المختبر نسبه الهيموجلوبين"
    )
    assert search_query("الملف اللي أرسلته لك قبل عن التحاليل، وش طلعت نتيجة السكر التراكمي فيه؟", config) == (
        "التحاليل نتيجه السكر التراكمي"
    )
    assert search_query("I uploaded my lab report in another conversation. What was my vitamin D level?", config) == (
        "lab report vitamin d level"
    )
    # Nothing but reference words: the message is searched whole.
    assert search_query("رفعت لك ملف", config) == "رفعت لك ملف"


class _Cap:
    """The parts of a loaded capability the router's candidacy reads."""

    slug = "files"
    manifest = MANIFEST


@pytest.mark.parametrize(
    ("message", "candidate"),
    [
        ("رفعت سعر المنتج، كيف أحسب الربح؟", False),
        ("حملت التطبيق أمس", False),
        ("رفعت لك ملف الفحوصات في محادثة ثانية", True),
        ("الملف اللي أرسلته لك قبل عن التحاليل", True),
        ("I uploaded my lab report in another conversation", True),
        ("How do I upload a file?", False),
    ],
)
def test_router_candidacy_needs_a_word_of_each_group(message, candidate):
    # An upload word alone makes no router candidate either (ADR-0449).
    assert bool(router_candidates([_Cap()], has_indexed=False, message=message)) is candidate


def test_the_manifest_keywords_are_the_config_words(config):
    refer = config["refer"]

    def words(*keys):
        return {normalize(w) for key in keys for lang in ("ar", "en") for w in refer[key][lang]}

    hints = MANIFEST.routing_hints
    assert {normalize(w) for w in [*hints.keywords.ar, *hints.keywords.en]} == words("upload_words", "send_words")
    assert {normalize(w) for w in [*hints.keywords_with.ar, *hints.keywords_with.en]} == words(
        "file_words", "subject_file_words", "earlier_words"
    )
