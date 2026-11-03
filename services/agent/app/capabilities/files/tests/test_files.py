"""The files capability's own rules (my-files PR, owner decision 2; ADR-0446): when a message refers to an earlier
file, which pages it names, and the title an answer cites. Arabic, Gulf and English cases (CLAUDE.md rule 7)."""

from pathlib import Path

import pytest
import yaml

from app.capabilities.contract import Manifest
from app.capabilities.files.graph import has_any, named_pages, refers_to_earlier_file
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
