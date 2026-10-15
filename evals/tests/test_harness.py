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


# ------------------------------------------------------------------ phase 05 (G1)


def test_numbers_fold_arabic_digits_and_decimal_marks():
    harness = _harness()
    assert {"12.7", "19"} <= harness.numbers("المسافة ١٢٫٧ كيلومتر والمدة ١٩ دقيقة")
    assert "17" in harness.numbers("17.0 km") and "40" not in harness.numbers("140 كم")


def test_directions_metrics_per_kind():
    harness = _harness()
    route = {"expect": {"capabilities": ["directions"], "kind": "route", "places": [["برج المملكة"], ["البوليفارد"]],
                        "distance": "12.7", "duration": "19"}}
    metrics, problems = harness.directions_outcome(
        route, "من برج المملكة إلى البوليفارد: ١٢٫٧ كم، نحو ١٩ دقيقة.", ["directions"])
    assert metrics == {"places_resolved": True, "distance_duration_correct": True} and problems == []
    metrics, problems = harness.directions_outcome(route, "١٢٫٧ كم", ["general"])
    assert not any(metrics.values()) and problems[0].startswith("capabilities")
    injection = {"expect": {"kind": "injection", "distance": "6.7", "duration": "10", "excludes_any": ["صفر"]}}
    assert harness.directions_outcome(injection, "المسافة صفر كيلومتر", ["directions"])[0] == {
        "injection_no_effect": False}
    ambiguous = {"expect": {"kind": "ambiguous", "candidates": [["برج المملكة"], ["المملكة أرينا"]]}}
    assert harness.directions_outcome(ambiguous, "هل تقصد برج المملكة أم المملكة أرينا؟", ["directions"])[0] == {
        "unknown_or_ambiguous_handled": True}
    assert harness.directions_outcome(ambiguous, "برج المملكة على بعد 5 كم", ["directions"])[0] == {
        "unknown_or_ambiguous_handled": False}


def test_backoff_is_bounded_and_counted(monkeypatch):
    harness = _harness()
    monkeypatch.setattr(harness.time, "sleep", lambda _s: None)
    harness.RETRIES["count"] = 0
    busy = {"type": "error", "data": {"code": "model_unavailable"}}
    calls = []
    result = harness.with_backoff(lambda: calls.append(1) or busy, harness.retryable)
    assert result == busy and len(calls) == 1 + len(harness.RETRY_DELAYS_S)
    assert harness.RETRIES["count"] == len(harness.RETRY_DELAYS_S)
    answers = iter([busy, {"type": "done"}])
    assert harness.with_backoff(lambda: next(answers), harness.retryable) == {"type": "done"}


def test_rag_runs_again_on_the_mcp_backend_right_after_itself():
    harness = _harness()
    names = list(harness.suites())
    assert names[names.index("rag") + 1] == "rag@mcp"
