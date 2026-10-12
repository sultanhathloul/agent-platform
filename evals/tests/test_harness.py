"""The eval harness calls nothing outside the platform (owner decision S3): Ragas' usage
tracking is off before Ragas is imported, a tracked event sends nothing, and the judge's
model and embeddings are LiteLLM's aliases on the stack's own LiteLLM."""

import importlib
import os
import sys
from pathlib import Path

EVALS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVALS))


def _harness():
    """The harness imported afresh, with neither the variable nor Ragas already in place."""
    for name in [m for m in sys.modules if m in ("ragas", "run_evals") or m.startswith("ragas.")]:
        del sys.modules[name]
    os.environ.pop("RAGAS_DO_NOT_TRACK", None)
    return importlib.import_module("run_evals")


def test_tracking_is_off_before_ragas_is_imported():
    harness = _harness()
    assert "ragas" not in sys.modules  # the harness imports ragas lazily, after the switch
    assert os.environ["RAGAS_DO_NOT_TRACK"] == "true"
    from ragas import _analytics

    _analytics.do_not_track.cache_clear()
    assert _analytics.do_not_track() is True
    assert harness.os.environ["RAGAS_DO_NOT_TRACK"] == "true"


def test_a_tracked_event_sends_nothing(monkeypatch):
    _harness()
    from ragas import _analytics

    _analytics.do_not_track.cache_clear()
    posted = []
    monkeypatch.setattr(_analytics.requests, "post", lambda *args, **kwargs: posted.append(args))
    _analytics.track(_analytics.IsCompleteEvent(event_type="evaluate", is_completed=True))
    _analytics._analytics_batcher.flush()
    assert posted == []


def test_the_judge_calls_only_the_stacks_litellm():
    harness = _harness()
    judge = harness.Judge({"LITELLM_PORT": "4999", "LITELLM_MASTER_KEY": "test-key"})
    llm = judge.faithfulness.llm.langchain_llm
    embeddings = judge.correctness.embeddings.embeddings
    assert str(llm.openai_api_base) == "http://127.0.0.1:4999/v1"
    assert str(embeddings.openai_api_base) == "http://127.0.0.1:4999/v1"


# ------------------------------------------------------------------ scoring (owner decision Q2)


def test_a_number_matches_only_as_a_whole_number():
    harness = _harness()
    assert harness.contains("يحق لكل موظف 40 ساعة تدريبية", "40")
    assert harness.contains("يحق لكل موظف ٤٠ ساعة تدريبية", "40")  # Arabic-Indic digits fold
    assert not harness.contains("يحق لكل موظف 140 ساعة", "40")
    assert not harness.contains("حتى عام 2040", "40")
    assert harness.contains("أربعون ساعة", "اربعون")  # words still match after fold()


def test_an_abstention_needs_a_whole_phrase_and_no_source_line():
    harness = _harness()
    answer = "لا يذكر الملف مدة إجازة الزواج."
    assert harness.abstained(answer, citations=[])
    assert not harness.abstained(answer, citations=[{"id": "d1", "pages": [3]}])  # a source line shown
    assert not harness.abstained("ويليس في الملف ذكر", citations=[])  # «ليس في» inside another word
    assert harness.says("هذا ليس في الملف", "ليس في")


def test_a_label_or_bracketed_file_name_in_an_answer_is_caught():
    harness = _harness()
    assert harness.has_label("مدة الإجازة ثلاثون يوما [docs]")
    assert harness.has_label("ثلاثون يوما [leave-regulation.pdf، صفحة 2]")
    assert not harness.has_label("مدة الإجازة ثلاثون يوما (المادة الثالثة).")
