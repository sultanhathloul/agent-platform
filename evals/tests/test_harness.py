"""The eval harness calls nothing outside the platform (owner decision S3): Ragas' usage
tracking is off before Ragas is imported, a tracked event sends nothing, and the judge's
model and embeddings are LiteLLM's aliases on the stack's own LiteLLM."""

import importlib
import json
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
    assert {12.7, 19.0} <= set(harness.numbers("المسافة ١٢٫٧ كيلومتر والمدة ١٩ دقيقة"))
    assert 17.0 in harness.numbers("17.0 km") and 40.0 not in harness.numbers("140 كم")


def test_the_harness_names_no_capability():
    """Zero core change for capability evals (ADR-0240): suites are globbed and a capability's
    metrics come from its own evals/metrics.yaml, so run_evals.py names no capability."""
    source = (EVALS / "run_evals.py").read_text(encoding="utf-8").lower()
    capabilities = EVALS.parent / "services" / "agent" / "app" / "capabilities"
    scored = [p.name for p in capabilities.iterdir() if (p / "evals" / "metrics.yaml").is_file()]
    assert scored and not [slug for slug in scored if slug in source]


def test_capability_metrics_per_kind_with_generic_scorers():
    harness = _harness()
    suite = EVALS.parent / "services/agent/app/capabilities/directions/evals/directions.jsonl"
    metrics = harness.load_metrics(suite)
    route = {"expect": {"capabilities": ["directions"], "kind": "route", "places": [["برج المملكة"], ["البوليفارد"]],
                        "distance": "12.7", "duration": "19"}}
    scored, problems = harness.metric_outcome(
        metrics, route, "من برج المملكة إلى البوليفارد: ١٢٫٧ كم، نحو ١٩ دقيقة.", {"capabilities": ["directions"]})
    assert scored == {"places_resolved": True, "distance_duration_correct": True} and problems == []
    scored, problems = harness.metric_outcome(metrics, route, "١٢٫٧ كم", {"capabilities": ["general"]})
    assert not any(scored.values()) and problems[0].startswith("field_equals")
    injection = {"expect": {"kind": "injection", "distance": "6.7", "duration": "10", "excludes_any": ["صفر"]}}
    assert harness.metric_outcome(
        metrics, injection, "المسافة صفر كيلومتر", {"capabilities": ["directions"]})[0] == {"injection_no_effect": False}
    ambiguous = {"expect": {"kind": "ambiguous", "candidates": [["برج المملكة"], ["المملكة أرينا"]]}}
    assert harness.metric_outcome(metrics, ambiguous, "هل تقصد برج المملكة أم المملكة أرينا؟",
                                  {"capabilities": ["directions"]})[0] == {"unknown_or_ambiguous_handled": True}
    assert harness.metric_outcome(metrics, ambiguous, "برج المملكة على بعد 5 كم",
                                  {"capabilities": ["directions"]})[0] == {"unknown_or_ambiguous_handled": False}


def test_a_number_is_within_its_tolerance():
    harness = _harness()
    case = {"expect": {"d": "12.7"}}
    exact = {"scorer": "number", "expected": "expect.d", "tolerance": 0}
    loose = {**exact, "tolerance": 0.1}
    assert harness.score_check(exact, case, "12.70 كم", {}) and not harness.score_check(exact, case, "12.8 كم", {})
    assert harness.score_check(loose, case, "12.8 كم", {}) and not harness.score_check(loose, case, "13 كم", {})


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


# ------------------------------------------------------------------ lost cases (phase 06, D5, ADR-0293)

_ANSWERABLE = {"id": "a", "expect": {"kind": "answerable", "pages": [2], "reference": "r"}}
_ABSTAIN = {"id": "b", "expect": {"kind": "abstain"}}
_GENERAL = {"id": "c", "expect": {"kind": "general"}}


def test_a_lost_case_is_a_failure_in_every_rag_metric_it_would_count_in():
    harness = _harness()
    answered = {"hit": True, "citation_ok": True, "faithfulness": 1.0, "correctness": 1.0, "problems": []}
    lost = {"lost": "agent_stream_interrupted", "problems": ["no done event (agent_stream_interrupted)"]}
    scores = harness.rag_scores([(_ANSWERABLE, answered), ({**_ANSWERABLE, "id": "a2"}, lost),
                                 (_ABSTAIN, lost), (_GENERAL, {"citation_ok": True, "problems": []})])
    assert scores["hit_at_8"] == 0.5 and scores["faithfulness"] == 0.5 and scores["answer_correctness"] == 0.5
    assert scores["citation_page_accuracy"] == 0.5  # 2 of 4: the lost answerable and the lost abstain fail
    assert scores["abstention"] == 0.0
    assert scores["counts"] == {"hit": 2, "citation": 4, "abstain": 1, "ragas": 2, "retrieve": 0}
    assert scores["lost"] == {"agent_stream_interrupted": 2}


def test_answered_cases_score_as_before():
    """With nothing lost, membership from the case equals membership from the outcome."""
    harness = _harness()
    outcomes = [(_ANSWERABLE, {"hit": False, "citation_ok": False, "faithfulness": 0.5, "correctness": 0.25}),
                (_ABSTAIN, {"citation_ok": True, "abstained": True}), (_GENERAL, {"citation_ok": False})]
    scores = harness.rag_scores(outcomes)
    assert (scores["hit_at_8"], scores["citation_page_accuracy"], scores["abstention"]) == (0.0, 1 / 3, 1.0)
    assert (scores["faithfulness"], scores["answer_correctness"], scores["lost"]) == (0.5, 0.25, {})


def test_a_record_written_before_phase_06_gives_its_lost_code():
    harness = _harness()
    assert harness.lost_of({"problems": ["no done event (validation_failed)"]}) == "validation_failed"
    assert harness.lost_of({"problems": ["none of ['40']"]}) is None
    assert harness.lost_of({"lost": "http_429", "problems": []}) == "http_429"
    assert harness.lost_code({"type": "done"}) is None and harness.lost_code({}) == "no_terminal_event"
    assert harness.lost_code({"type": "error", "data": {"code": "turn_timeout"}}) == "turn_timeout"


def test_a_refused_or_broken_turn_is_a_lost_case_not_a_crashed_run():
    harness = _harness()
    import httpx

    def refuse(request):
        return httpx.Response(429, json={"error": {"code": "too_many_turns"}})

    with httpx.Client(transport=httpx.MockTransport(refuse)) as http:
        event = harness.run_turn(http, "http://gw", "t", {"message": "x"})
    assert harness.lost_code(event) == "http_429"

    def broken(request):
        raise httpx.ConnectError("refused")

    with httpx.Client(transport=httpx.MockTransport(broken)) as http:
        assert harness.lost_code(harness.run_turn(http, "http://gw", "t", {"message": "x"})) == "transport_ConnectError"


def test_rescoring_fails_a_metric_suite_on_its_lost_cases(tmp_path):
    harness = _harness()
    suite = EVALS.parent / "services/agent/app/capabilities/directions/evals/directions.jsonl"
    cases = [json.loads(line) for line in suite.read_text(encoding="utf-8").splitlines() if line.strip()]
    route = [c for c in cases if c["expect"].get("kind") == "route"]
    records = [{"id": c["id"], "passed": True, "problems": [],
                "metrics": {"places_resolved": True, "distance_duration_correct": True}} for c in route]
    records[0] = {"id": route[0]["id"], "passed": False, "problems": ["no done event (agent_stream_interrupted)"]}
    out = harness.rescore_suite("directions", suite, {"cases": records, "scores": {}},
                                {"places_resolved": 0.99, "distance_duration_correct": 0.0})
    assert route and out["scores"]["places_resolved"] == (len(route) - 1) / len(route)
    assert out["ok"] is False and out["lost"] == {"agent_stream_interrupted": 1}


def test_rescoring_a_rag_suite_counts_its_lost_cases():
    harness = _harness()
    rag = EVALS / "golden" / "rag.jsonl"
    cases = [json.loads(line) for line in rag.read_text(encoding="utf-8").splitlines() if line.strip()]
    answerable = [c for c in cases if c["expect"].get("kind") == "answerable" and c["expect"].get("pages")][:4]
    records = [{"id": c["id"], "passed": True, "problems": [], "hit": True, "citation_ok": True,
                "faithfulness": 1.0, "correctness": 1.0} for c in answerable]
    records[0] = {"id": answerable[0]["id"], "passed": False,
                  "problems": ["no done event (agent_stream_interrupted)"]}
    threshold = {"hit_at_8": 0.9, "citation_page_accuracy": 0.0, "faithfulness": 0.0,
                 "answer_correctness": 0.0, "abstention": 0.0}
    out = harness.rescore_suite("rag", rag, {"cases": records, "scores": {"hit_at_8": 1.0, "counts": {"ragas": 3}}},
                                threshold)
    assert out["scores"]["hit_at_8"] == 0.75 and out["scores"]["faithfulness"] == 0.75 and out["ok"] is False


# ------------------------------------------------------------------ skill scorers (phase 06, I9)


def test_sections_in_order_need_every_section_after_the_previous_one():
    harness = _harness()
    memo = "مذكرة داخلية\nالرقم: [الرقم]\nإلى: مدير الإدارة\nالموضوع: الإجازات\nالسلام عليكم ورحمة الله وبركاته\n...\nوتقبلوا خالص التحية والتقدير"
    sections = ["مذكره داخليه", "الرقم", "الى", "الموضوع", "السلام عليكم", "وتقبلوا خالص التحيه"]
    assert harness.sections_in_order(memo, sections)  # folded: ة/ه, إ/ا
    assert not harness.sections_in_order(memo, ["الموضوع", "الرقم"])  # wrong order
    assert not harness.sections_in_order(memo, sections + ["التوقيع"])  # a missing section
    assert harness.sections_in_order("Subject: x\nTo: y", [["subject", "re:"], "to:"])  # alternatives
    assert not harness.sections_in_order(memo, [])


def test_text_present_and_length_between():
    harness = _harness()
    case = {"expect": {"facts": ["30", ["ثلاثون", "ثلاثين"]]}}
    present = {"scorer": "text_present", "expected": "expect.facts"}
    assert harness.score_check(present, case, "مدة الإجازة 30 يوما، أي ثلاثين يوما", {})
    assert not harness.score_check(present, case, "مدة الإجازة 30 يوما", {})
    assert harness.score_check({"scorer": "text_present", "texts": ["[التاريخ]"]}, {}, "التاريخ: [التاريخ]", {})
    length = {"scorer": "length_between", "min": 3, "max": 5}
    assert harness.score_check(length, {}, "one two three four", {})
    assert not harness.score_check(length, {}, "one two", {}) and not harness.score_check(length, {}, "a b c d e f", {})


def test_turn_fields_score_the_applied_skill_and_citations():
    harness = _harness()
    check = {"scorer": "field_equals", "field": "skill", "expected": "expect.skill"}
    assert harness.score_check(check, {"expect": {"skill": "x"}}, "", {"skill": "x"})
    assert not harness.score_check(check, {"expect": {"skill": "x"}}, "", {"skill": None})
    cited = {"scorer": "field_equals", "field": "cited", "default": True}
    assert harness.score_check(cited, {}, "", {"cited": True}) and not harness.score_check(cited, {}, "", {"cited": False})


def test_the_harness_names_no_skill_and_globs_their_evals():
    """Zero core change for skill evals (phase 06, I9): skills/*/evals are suites, none named."""
    harness = _harness()
    source = (EVALS / "run_evals.py").read_text(encoding="utf-8").lower()
    skills = EVALS.parent / "skills"
    slugs = [p.name for p in skills.iterdir() if p.is_dir()] if skills.is_dir() else []
    assert not [slug for slug in slugs + ["official_memo", "official_letter"] if slug in source]
    found = harness.suites()
    for slug in slugs:
        if (skills / slug / "evals" / f"{slug}.jsonl").is_file():
            assert found[slug] == skills / slug / "evals" / f"{slug}.jsonl"


def test_the_applied_skill_and_expected_skill():
    harness = _harness()
    assert harness.applied_skill({"state_digest": {"skill": "x_form@1.2.0"}}) == "x_form"
    assert harness.applied_skill({"state_digest": {"skill": None}}) is None and harness.applied_skill({}) is None
    assert harness.skill_problems({"expected_skill": None}, None) == []
    assert harness.skill_problems({"expected_skill": "x_form"}, None) == ["skill None != x_form"]
    assert harness.skill_problems({"id": "no key"}, "x_form") == []


def test_layouts_by_a_case_field_and_patterns_from_the_case():
    harness = _harness()
    layouts = {"scorer": "sections_in_order", "by": "locale",
               "sections": {"ar": ["(?m)^\\W*الرقم[\\s*_]*:", "(?m)^\\W*الموضوع[\\s*_]*:"], "en": ["(?m)^\\W*ref", "subject"]}}
    assert harness.score_check(layouts, {"locale": "ar"}, "**الرقم:** [الرقم]\nالموضوع: إجازة", {})
    assert not harness.score_check(layouts, {"locale": "ar"}, "الموضوع: إجازة\nالرقم: [الرقم]", {})
    assert harness.score_check(layouts, {"locale": "en"}, "Ref: [Reference number]\nSubject: leave", {})
    absent = {"scorer": "text_absent", "patterns_from": "expect.absent_patterns"}
    case = {"expect": {"absent_patterns": ["\\d{1,4}\\s*[/\\-.]\\s*\\d{1,2}\\s*[/\\-.]\\s*\\d{1,4}"]}}
    assert harness.score_check(absent, case, "التاريخ: [التاريخ]", {})
    assert not harness.score_check(absent, case, "التاريخ: ١٤٤٧/٠٣/١٢", {})  # an invented date
    assert harness.score_check(absent, {"expect": {}}, "التاريخ: ١٤٤٧/٠٣/١٢", {})
