# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27", "pyyaml>=6.0", "ragas==0.2.15", "langchain-openai>=0.2,<0.4"]
# ///
"""Offline evaluation (README §12) against the live stack, through the
gateway, with real models.

Suites are evals/golden/*.jsonl plus services/agent/app/capabilities/*/evals/*.jsonl
(README §18.4); the suite name is the file stem. Every suite needs a threshold
in evals/thresholds.yaml, and a suite without one fails the run. Each suite is
a Langfuse dataset with one item per case; every run creates a dataset run
(`make-eval-<UTC time>`) linking each case to its turn's trace, and pushes an
`eval_pass` score on that trace (README §12, ADR-0045).

Case checks so far: contains_any (answer contains one of the strings, compared
after Arabic normalization), excludes_any (answer contains none of them; catches
a right keyword inside a wrong answer) and script (arabic | latin: the answer
is mostly in that script). Suites for later phases add their own checks.

Guard cases (phase 03, I6) name a `policy_id` instead of a message: they run as the
seed user `admin` through the gateway's policy test endpoint, the runtime engine path,
and pass when the engine's action is `expect.action` (and the matched entity types
include `expect.entities`). Each case gets a Langfuse trace for its dataset run item.
A suite of guard cases has a structured threshold (D8): every deterministic case
(regex, presidio, keyword_list) must pass, and the llm_judge cases need an accuracy
and a false-positive rate within their bounds.

Document cases (phase 04) name a `document` of evals/docs/ (generated, fictional, G3).
Each case gets a new conversation: the document is uploaded through the gateway and
indexed first.
- A routing case (routing.jsonl, ADR-0139) then sends its message, with the file
  (`attach`) or, by default, without it, so the router model decides; it passes when the
  turn's capabilities are `expect.capabilities`.
- A rag case (rag.jsonl, ADR-0141) sends its message with the file. Its suite has a
  structured threshold: retrieval hit@8 (an expected page, or section hash, among what the
  `rag.retrieve` span reports), citation page accuracy (the cited pages are expected pages;
  a general-knowledge or not-in-document answer cites nothing, ADR-0140), correct abstention,
  and the Ragas faithfulness and answer correctness of answerable cases. Ragas calls
  LiteLLM's alias for the `judge` role directly (and `embed` for answer similarity): the
  harness is a client of the platform, not an agent model call (ADR-0141). Contexts are the
  retrieved pages' and sections' text from evals/docs/sections.json, since traces carry no
  document text (ADR-0137).
"""

import asyncio
import hashlib
import json
import re
import sys
import time
import unicodedata
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from get_token import env, login  # noqa: E402

# A dedicated seed user whose memory is switched off before every run, so a case never
# reads what an earlier case or run stored; the analyst keeps memory for smoke (ADR-0074).
EVAL_USER = ("evaluator", "evaluator")
# Guard cases run through the admin-only policy test endpoint (I6).
ADMIN_USER = ("admin", "admin")
DETERMINISTIC = {"regex", "presidio", "keyword_list", "tier_ceiling"}
_FOLD = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه", **{a: str(i) for i, a in enumerate("٠١٢٣٤٥٦٧٨٩")}})
_DIACRITICS = re.compile(r"[ً-ٰٟ]")


def fold(text: str) -> str:
    return _DIACRITICS.sub("", unicodedata.normalize("NFKC", text).replace("ـ", "")).translate(_FOLD).casefold()


def script_share(text: str, script: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    arabic = sum("؀" <= c <= "ۿ" for c in letters)
    return (arabic if script == "arabic" else len(letters) - arabic) / len(letters)


def check(case: dict, answer: str) -> list[str]:
    expect, failures = case.get("expect", {}), []
    if "contains_any" in expect and not any(fold(s) in fold(answer) for s in expect["contains_any"]):
        failures.append(f"none of {expect['contains_any']}")
    wrong = [s for s in expect.get("excludes_any", []) if fold(s) in fold(answer)]
    if wrong:
        failures.append(f"contains {wrong}")
    if "script" in expect and script_share(answer, expect["script"]) < 0.6:
        failures.append(f"not mostly {expect['script']}")
    return failures


def run_turn(
    http: httpx.Client,
    gateway: str,
    token: str,
    case: dict,
    *,
    conversation_id: str | None = None,
    attachment_ids: list[str] | None = None,
) -> dict:
    body = {
        "conversation_id": conversation_id or str(uuid.uuid4()),
        "message": case["message"],
        "locale": case.get("locale", "ar"),
        "attachment_ids": attachment_ids or [],
    }
    headers = {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}
    last: dict = {}
    with http.stream("POST", f"{gateway}/v1/chat/turns", json=body, headers=headers) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if line.startswith("data: "):
                last = json.loads(line[6:])
    return last


def run_guard_case(http: httpx.Client, gateway: str, token: str, case: dict) -> tuple[dict | None, list[str]]:
    body = {"policy_id": case["policy_id"], "text": case["text"]}
    for key in ("checkpoint", "target"):
        if case.get(key):
            body[key] = case[key]
    response = http.post(
        f"{gateway}/v1/admin/policies/test", json=body, headers={"Authorization": f"Bearer {token}"}
    )
    if response.status_code != 200:
        return None, [f"HTTP {response.status_code}: {response.text[:200]}"]
    result = response.json()["data"]
    expect, problems = case["expect"], []
    if result["action"] != expect["action"]:
        problems.append(f"action {result['action']} != {expect['action']} ({result.get('explanation')})")
    found = {span["entity"] for span in result["spans"]}
    missing = set(expect.get("entities", [])) - found
    if missing:
        problems.append(f"missing entities {sorted(missing)}; found {sorted(found)}")
    return result, problems


# ------------------------------------------------------------------ documents (phase 04)

DOCS = REPO / "evals" / "docs"
# How an answer says the document does not hold the answer (compared after fold()).
ABSTAIN_PHRASES = [
    "لم اجد", "لا اجد", "لم يرد", "لا يرد", "لا يتضمن", "لا تتضمن", "لم يتضمن", "لم تتضمن", "لا يحتوي",
    "لا تحتوي", "لم يذكر", "لم تذكر", "لا يذكر", "لا تذكر", "غير مذكور", "لا توجد معلومات", "لا تتوفر",
    "لم تشر", "لا تشير", "لا يشير", "ليس في", "لا يوجد في", "لا يوجد ذكر", "لم ترد", "لا ترد", "لا يمكنني العثور",
    "not mentioned", "does not mention", "not in the", "no information",
]


def upload(http: httpx.Client, gateway: str, token: str, conversation_id: str, document: str) -> str:
    """Uploads a generated document to a new conversation and waits until it is indexed."""
    path = DOCS / document
    headers = {"Authorization": f"Bearer {token}"}
    with path.open("rb") as handle:
        response = http.post(
            f"{gateway}/v1/conversations/{conversation_id}/attachments",
            files={"file": (path.name, handle)},
            headers=headers,
        )
    response.raise_for_status()
    attachment = response.json()["data"]["id"]
    for _ in range(120):
        status = http.get(f"{gateway}/v1/conversations/{conversation_id}/attachments/{attachment}", headers=headers)
        status.raise_for_status()
        data = status.json()["data"]
        if data["status"] == "indexed":
            return attachment
        if data["status"] == "failed":
            raise RuntimeError(f"{document} failed to index: {data.get('reason')}")
        time.sleep(1)
    raise RuntimeError(f"{document} was not indexed within 120 s")


def retrieved(http: httpx.Client, cfg: dict, auth: tuple[str, str], trace_id: str) -> list[dict] | None:
    """What the turn's `rag.retrieve` span reports (ids, pages, section hashes, scores), from
    Langfuse; None when the trace has no retrieval."""
    for _ in range(60):
        response = http.get(f"{cfg['LANGFUSE_PUBLIC_URL']}/api/public/traces/{trace_id}", auth=auth)
        if response.status_code == 200:
            spans = [o for o in response.json().get("observations", []) if o.get("name") == "rag.retrieve"]
            for span in spans:
                metadata = span.get("metadata") or {}
                attributes = metadata.get("attributes", metadata)
                if "rag.results" in attributes:
                    value = attributes["rag.results"]
                    return json.loads(value) if isinstance(value, str) else value
            names = {o.get("name") for o in response.json().get("observations", [])}
            if "model.call" in names and "tool.exec" not in names and "rag.retrieve" not in names:
                return None
        time.sleep(2)
    return None


class Judge:
    """Ragas metrics through LiteLLM's alias for the `judge` role (ADR-0141); `embed` for the
    similarity half of answer correctness. Aliases come from config/models.yaml."""

    def __init__(self, cfg: dict):
        from langchain_openai import ChatOpenAI, OpenAIEmbeddings
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from ragas.llms import LangchainLLMWrapper
        from ragas.metrics import AnswerCorrectness, Faithfulness
        from ragas.run_config import RunConfig

        models = yaml.safe_load((REPO / "config" / "models.yaml").read_text())
        judge_alias = models["tiers"][models["roles"]["judge"]]["alias"]
        base = cfg.get("LITELLM_PUBLIC_URL") or f"http://127.0.0.1:{cfg.get('LITELLM_PORT') or 4000}"
        key = cfg["LITELLM_MASTER_KEY"]
        llm = LangchainLLMWrapper(ChatOpenAI(model=judge_alias, base_url=f"{base}/v1", api_key=key, temperature=0))
        embeddings = LangchainEmbeddingsWrapper(
            OpenAIEmbeddings(model=models["embedding"]["alias"], base_url=f"{base}/v1", api_key=key,
                             check_embedding_ctx_length=False)
        )
        self.faithfulness = Faithfulness(llm=llm)
        self.correctness = AnswerCorrectness(llm=llm, embeddings=embeddings)
        for metric in (self.faithfulness, self.correctness):
            metric.init(RunConfig(timeout=180, max_retries=2))
        self.alias = judge_alias

    def score(self, question: str, answer: str, contexts: list[str], reference: str) -> tuple[float, float]:
        from ragas.dataset_schema import SingleTurnSample

        sample = SingleTurnSample(user_input=question, response=answer, retrieved_contexts=contexts or [""],
                                  reference=reference)

        async def both() -> tuple[float, float]:
            return (await self.faithfulness.single_turn_ascore(sample),
                    await self.correctness.single_turn_ascore(sample))

        faithfulness, correctness = asyncio.run(both())
        return float(faithfulness), float(correctness)


def contexts_for(document: str, results: list[dict], sections: dict) -> list[str]:
    """The text of the pages and sections retrieval returned, once each, in rank order."""
    entry = sections.get(document, {})
    texts: list[str] = []
    for result in results or []:
        text = None
        if result.get("page") is not None:
            text = entry.get("pages", {}).get(str(result["page"]))
        elif result.get("section_sha"):
            text = (entry.get("sections", {}).get(result["section_sha"]) or {}).get("text")
        if text and text not in texts:
            texts.append(text)
    return texts


def expected_section_sha(document: str, section: str, sections: dict) -> str | None:
    for sha, entry in sections.get(document, {}).get("sections", {}).items():
        if entry["path"] == section or entry["path"].endswith(f" › {section}"):
            return sha
    return None


def run_document_case(http, cfg, token: str, case: dict, judge: "Judge | None", sections: dict, auth) -> dict:
    """One routing or rag case in a new conversation with its document indexed first."""
    gateway = cfg["GATEWAY_PUBLIC_URL"]
    conversation = str(uuid.uuid4())
    attachment = upload(http, gateway, token, conversation, case["document"])
    attach = case.get("attach", "pages" in case["expect"] or case["expect"].get("kind") is not None)
    event = run_turn(http, gateway, token, case, conversation_id=conversation,
                     attachment_ids=[attachment] if attach else [])
    if event.get("type") != "done":
        return {"event": event, "problems": [f"no done event ({event.get('data', {}).get('code')})"]}
    data = event["data"]
    outcome = {"event": event, "answer": data["answer"], "trace_id": data.get("trace_id"),
               "capabilities": data["state_digest"]["capabilities"], "citations": data.get("citations") or []}
    expect = case["expect"]
    problems: list[str] = []
    if "capabilities" in expect:  # routing
        if outcome["capabilities"] != expect["capabilities"]:
            problems.append(f"capabilities {outcome['capabilities']} != {expect['capabilities']}")
        outcome["problems"] = problems
        return outcome
    kind = expect["kind"]
    answer = data["answer"]
    cited = [c for c in outcome["citations"] if c.get("document_id") == attachment]
    cited_pages = sorted({p for c in cited for p in c.get("pages") or []})
    outcome["cited_pages"] = cited_pages
    if expect.get("contains_any") and not any(fold(s) in fold(answer) for s in expect["contains_any"]):
        problems.append(f"none of {expect['contains_any']}")
    if any(fold(s) in fold(answer) for s in expect.get("excludes_any", [])):
        problems.append("shows text it must not show")
    results = retrieved(http, cfg, auth, outcome["trace_id"]) if outcome["trace_id"] else None
    outcome["retrieved"] = results
    if kind in ("answerable", "pii"):
        pages = {r.get("page") for r in results or []}
        shas = {r.get("section_sha") for r in results or []}
        hit = bool(set(expect.get("pages", [])) & pages)
        if expect.get("section"):
            hit = hit or expected_section_sha(case["document"], expect["section"], sections) in shas
        outcome["hit"] = hit
        if not hit:
            problems.append("expected page or section not retrieved in the top 8")
    if kind == "answerable" and expect.get("pages"):
        acceptable = set(expect["pages"]) | set(expect.get("acceptable_pages", []))
        outcome["citation_ok"] = bool(cited_pages) and set(cited_pages) <= acceptable
        if not outcome["citation_ok"]:
            problems.append(f"cited pages {cited_pages}, expected {sorted(acceptable)}")
    if kind in ("general", "abstain"):
        outcome["citation_ok"] = not outcome["citations"]
        if outcome["citations"]:
            problems.append(f"a source line where none belongs: {cited_pages or outcome['citations']}")
    if kind == "abstain":
        said = any(fold(p) in fold(answer) for p in ABSTAIN_PHRASES)
        outcome["abstained"] = said and not outcome["citations"]
        if not said:
            problems.append("did not say the document lacks the answer")
    if kind == "answerable" and judge is not None:
        contexts = contexts_for(case["document"], results or [], sections)
        faithfulness, correctness = judge.score(case["message"], answer, contexts, expect["reference"])
        outcome["faithfulness"], outcome["correctness"] = faithfulness, correctness
    outcome["problems"] = problems
    return outcome


def rag_scores(outcomes: list[tuple[dict, dict]]) -> dict:
    def mean(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    hits = [o["hit"] for _, o in outcomes if "hit" in o]
    citations = [o["citation_ok"] for _, o in outcomes if "citation_ok" in o]
    abstentions = [o.get("abstained", False) for c, o in outcomes if c["expect"].get("kind") == "abstain"]
    faithfulness = [o["faithfulness"] for _, o in outcomes if "faithfulness" in o]
    correctness = [o["correctness"] for _, o in outcomes if "correctness" in o]
    return {
        "hit_at_8": mean([float(h) for h in hits]),
        "citation_page_accuracy": mean([float(c) for c in citations]),
        "faithfulness": mean(faithfulness),
        "answer_correctness": mean(correctness),
        "abstention": mean([float(a) for a in abstentions]),
        "counts": {"hit": len(hits), "citation": len(citations), "abstain": len(abstentions),
                   "ragas": len(faithfulness)},
    }


def rag_threshold_ok(scores: dict, threshold: dict) -> bool:
    return all(scores[name] >= value for name, value in threshold.items())


def guard_trace(http: httpx.Client, cfg: dict, auth: tuple[str, str], case: dict, result: dict | None) -> str:
    """A trace for the case's dataset run item: the policy test endpoint makes no turn."""
    trace_id = uuid.uuid4().hex
    now = datetime.now(UTC).isoformat()
    body = {
        "id": trace_id,
        "name": "guardrails-eval",
        "timestamp": now,
        "input": {"policy_id": case["policy_id"], "checkpoint": case.get("checkpoint"), "target": case.get("target"),
                  "text_sha256": hashlib.sha256(case["text"].encode()).hexdigest()},
        "output": None if result is None else {"action": result["action"], "matched": result["matched"],
                                               "policy_ref": result["policy_ref"]},
    }
    langfuse(http, cfg, auth, "ingestion", {"batch": [{"id": uuid.uuid4().hex, "type": "trace-create",
                                                       "timestamp": now, "body": body}]})
    return trace_id


def guard_scores(results: list[tuple[dict, bool, str]]) -> dict:
    """D8: deterministic accuracy; llm_judge accuracy and false-positive rate (a case that
    should be allowed but was blocked or flagged)."""
    deterministic = [ok for case, ok, checker in results if checker in DETERMINISTIC]
    judge = [(case, ok) for case, ok, checker in results if checker == "llm_judge"]
    negatives = [(case, ok) for case, ok in judge if case["expect"]["action"] == "allow"]
    tagged = [(case, ok) for case, ok in judge if case.get("tags")]
    return {
        "llm_judge_borderline_or_gulf": len(tagged),
        "llm_judge_borderline_or_gulf_passed": sum(ok for _, ok in tagged),
        "deterministic": sum(deterministic) / len(deterministic) if deterministic else 1.0,
        "deterministic_cases": len(deterministic),
        "llm_judge_accuracy": sum(ok for _, ok in judge) / len(judge) if judge else 1.0,
        "llm_judge_false_positive_rate": sum(not ok for _, ok in negatives) / len(negatives) if negatives else 0.0,
        "llm_judge_cases": len(judge),
    }


def guard_threshold_ok(scores: dict, threshold: dict) -> bool:
    judge = threshold.get("llm_judge", {})
    return (
        scores["deterministic"] >= threshold.get("deterministic", 1.0)
        and scores["llm_judge_accuracy"] >= judge.get("accuracy", 0.0)
        and scores["llm_judge_false_positive_rate"] <= judge.get("false_positive_rate", 1.0)
    )


def policy_checkers(http: httpx.Client, gateway: str, token: str) -> dict[str, str]:
    response = http.get(f"{gateway}/v1/admin/policies", headers={"Authorization": f"Bearer {token}"})
    response.raise_for_status()
    return {view["policy"]["id"]: view["policy"]["checker"] for view in response.json()["data"]["policies"]}


def langfuse(http: httpx.Client, cfg: dict, auth: tuple[str, str], path: str, body: dict) -> None:
    response = http.post(f"{cfg['LANGFUSE_PUBLIC_URL']}/api/public/{path}", auth=auth, json=body)
    response.raise_for_status()


def disable_memory(http: httpx.Client, gateway: str, token: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    response = http.put(f"{gateway}/v1/memory/settings", json={"enabled": False}, headers=headers)
    response.raise_for_status()
    listed = http.get(f"{gateway}/v1/memory", headers=headers)
    listed.raise_for_status()
    if listed.json()["data"]["enabled"] is not False:
        raise SystemExit("the eval user's memory is still on")


def suites() -> dict[str, Path]:
    files = sorted((REPO / "evals" / "golden").glob("*.jsonl"))
    files += sorted((REPO / "services" / "agent" / "app" / "capabilities").glob("*/evals/*.jsonl"))
    return {path.stem: path for path in files}


def main() -> int:
    cfg = env()
    thresholds = yaml.safe_load((REPO / "evals" / "thresholds.yaml").read_text()) or {}
    found = suites()
    if not found:
        print("no eval suites found")
        return 1
    token = login(*EVAL_USER)["access_token"]  # fail before any Langfuse write when the user cannot log in
    auth = (cfg["LANGFUSE_PUBLIC_KEY"], cfg["LANGFUSE_SECRET_KEY"])
    run_name = f"make-eval-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    rows, failed = [], False
    judge: Judge | None = None
    sections = json.loads((DOCS / "sections.json").read_text(encoding="utf-8")) if (DOCS / "sections.json").exists() else {}
    only = set(sys.argv[1:])  # `make eval s=rag` runs one suite; empty runs them all

    with httpx.Client(timeout=300) as http:
        disable_memory(http, cfg["GATEWAY_PUBLIC_URL"], token)
        for name, path in found.items():
            if only and name not in only:
                continue
            cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            # One Langfuse dataset per suite, one item per case, one run per `make eval` (README §12).
            langfuse(http, cfg, auth, "v2/datasets", {"name": name, "description": f"{path.relative_to(REPO)}"})
            passed = 0
            guard_results: list[tuple[dict, bool, str]] = []
            checkers: dict[str, str] = {}
            document_outcomes: list[tuple[dict, dict]] = []
            for case in cases:
                item_id = f"{name}:{case['id']}"
                if "document" in case:
                    langfuse(
                        http, cfg, auth, "dataset-items",
                        {"datasetName": name, "id": item_id,
                         "input": {"message": case["message"], "document": case["document"],
                                   "locale": case.get("locale", "ar")},
                         "expectedOutput": case["expect"]},
                    )
                    token = login(*EVAL_USER)["access_token"]
                    if judge is None and any("kind" in c.get("expect", {}) for c in cases):
                        judge = Judge(cfg)
                    outcome = run_document_case(http, cfg, token, case, judge, sections, auth)
                    document_outcomes.append((case, outcome))
                    problems = outcome["problems"]
                    passed += not problems
                    mark = "pass" if not problems else "FAIL"
                    extra = ""
                    if "faithfulness" in outcome:
                        extra = f" faith={outcome['faithfulness']:.2f} corr={outcome['correctness']:.2f}"
                    shown = outcome.get("answer", "")[:70]
                    print(f"  {mark}  {name}/{case['id']}  {outcome.get('capabilities')}{extra}  {shown!r}  "
                          f"{'; '.join(problems)}")
                    trace_id = outcome.get("trace_id")
                    if trace_id:
                        metadata = {k: outcome.get(k) for k in ("hit", "citation_ok", "abstained", "faithfulness",
                                                                "correctness", "cited_pages", "capabilities")}
                        langfuse(
                            http, cfg, auth, "dataset-run-items",
                            {"runName": run_name, "datasetItemId": item_id, "traceId": trace_id,
                             "metadata": {"passed": not problems, "problems": problems, **metadata}},
                        )
                        langfuse(
                            http, cfg, auth, "scores",
                            {"traceId": trace_id, "name": "eval_pass", "value": 0 if problems else 1,
                             "dataType": "NUMERIC", "comment": f"{name}/{case['id']}"},
                        )
                    continue
                if "policy_id" in case:
                    langfuse(
                        http, cfg, auth, "dataset-items",
                        {"datasetName": name, "id": item_id,
                         "input": {"policy_id": case["policy_id"], "text": case["text"],
                                   "checkpoint": case.get("checkpoint"), "target": case.get("target")},
                         "expectedOutput": case["expect"]},
                    )
                    admin = login(*ADMIN_USER)["access_token"]
                    if not checkers:
                        checkers = policy_checkers(http, cfg["GATEWAY_PUBLIC_URL"], admin)
                    result, problems = run_guard_case(http, cfg["GATEWAY_PUBLIC_URL"], admin, case)
                    checker = checkers.get(case["policy_id"], "unknown")
                    guard_results.append((case, not problems, checker))
                    passed += not problems
                    mark = "pass" if not problems else "FAIL"
                    shown = result["action"] if result else "-"
                    print(f"  {mark}  {name}/{case['id']}  [{checker}] {shown}  {'; '.join(problems)}")
                    trace_id = guard_trace(http, cfg, auth, case, result)
                    langfuse(
                        http, cfg, auth, "dataset-run-items",
                        {"runName": run_name, "datasetItemId": item_id, "traceId": trace_id,
                         "metadata": {"passed": not problems, "problems": problems, "checker": checker}},
                    )
                    langfuse(
                        http, cfg, auth, "scores",
                        {"traceId": trace_id, "name": "eval_pass", "value": 0 if problems else 1,
                         "dataType": "NUMERIC", "comment": f"{name}/{case['id']}"},
                    )
                    continue
                langfuse(
                    http, cfg, auth, "dataset-items",
                    {"datasetName": name, "id": item_id,
                     "input": {"message": case["message"], "locale": case.get("locale", "ar")},
                     "expectedOutput": case.get("expect", {})},
                )
                # A fresh token per case: a suite outlasts the realm's 300 s access token (ADR-0065).
                token = login(*EVAL_USER)["access_token"]
                event = run_turn(http, cfg["GATEWAY_PUBLIC_URL"], token, case)
                if event.get("type") == "done":
                    answer = event["data"]["answer"]
                    problems = check(case, answer)
                    trace_id = event["data"].get("trace_id")
                else:
                    answer, problems, trace_id = "", [f"no done event ({event.get('data', {}).get('code')})"], None
                passed += not problems
                mark = "pass" if not problems else "FAIL"
                print(f"  {mark}  {name}/{case['id']}  {answer[:80]!r}  {'; '.join(problems)}")
                if trace_id:
                    langfuse(
                        http, cfg, auth, "dataset-run-items",
                        {"runName": run_name, "datasetItemId": item_id, "traceId": trace_id,
                         "metadata": {"passed": not problems, "problems": problems}},
                    )
                    langfuse(
                        http, cfg, auth, "scores",
                        {"traceId": trace_id, "name": "eval_pass", "value": 0 if problems else 1,
                         "dataType": "NUMERIC", "comment": f"{name}/{case['id']}"},
                    )
            rate = passed / len(cases)
            threshold = thresholds.get(name)
            if document_outcomes and isinstance(threshold, dict):
                scores = rag_scores(document_outcomes)
                ok = rag_threshold_ok(scores, threshold)
                counts = scores["counts"]
                print(
                    f"  {name}: hit@8 {scores['hit_at_8']:.2f} ({counts['hit']}), citation page accuracy "
                    f"{scores['citation_page_accuracy']:.2f} ({counts['citation']}), faithfulness "
                    f"{scores['faithfulness']:.2f} and answer correctness {scores['answer_correctness']:.2f} "
                    f"({counts['ragas']}, judge alias {judge.alias if judge else '-'}), abstention "
                    f"{scores['abstention']:.2f} ({counts['abstain']})"
                )
            elif guard_results:
                scores = guard_scores(guard_results)
                ok = isinstance(threshold, dict) and guard_threshold_ok(scores, threshold)
                print(
                    f"  {name}: deterministic {scores['deterministic']:.2f} ({scores['deterministic_cases']} cases), "
                    f"llm_judge accuracy {scores['llm_judge_accuracy']:.2f} and false-positive rate "
                    f"{scores['llm_judge_false_positive_rate']:.2f} ({scores['llm_judge_cases']} cases, "
                    f"{scores['llm_judge_borderline_or_gulf']} borderline or Gulf-dialect, "
                    f"{scores['llm_judge_borderline_or_gulf_passed']} of them passed)"
                )
            else:
                ok = isinstance(threshold, int | float) and rate >= threshold
            failed |= not ok
            rows.append((name, passed, len(cases), rate, threshold, ok))

    print(f"\n{'suite':<14}{'passed':>9}{'rate':>8}{'threshold':>11}  result")
    for name, passed, total, rate, threshold, ok in rows:
        shown = "missing" if threshold is None else ("per metric" if isinstance(threshold, dict) else f"{threshold:.2f}")
        print(f"{name:<14}{passed:>4}/{total:<4}{rate:>8.2f}{shown:>11}  {'PASS' if ok else 'FAIL'}")
    print(f"\nLangfuse dataset run: {run_name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
