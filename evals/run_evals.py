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

Phase 05: a case with `expect.capabilities` and no document checks the turn's capabilities (the
routing suite's capability cases). A suite whose folder holds a metrics.yaml (a capability's
evals/, ADR-0240) is scored by the metrics it declares with generic scorers (field equals, number
within tolerance, expected abstention or candidates, text absent from the answer), each against
its threshold; the harness names no capability. A case with `report_set` is scored and reported
under that set and never counts toward its suite's rate (ADR-0241). The rag suite runs twice, the second time as
`rag@mcp` with the agent recreated on DOCS_BACKEND=mcp, and the internal backend is restored
afterwards, pass or fail. A provider 429 (a `model_unavailable` turn, or a rate-limited judge
call) is retried with bounded exponential backoff, and each suite records its retries.

Phase 06 (I9, I10, ADR-0303, ADR-0304): suites are also globbed from skills/*/evals, and the
harness names no skill. A case may name `skill`, sent as the composer's picker would send it; a
metrics suite's case may name a `document`, uploaded and attached first. The turn fields a
metrics.yaml check reads are `capabilities`, `skill` (the applied skill's slug, or null) and
`cited` (the answer shows a source). A case with `expected_skill` (routing.jsonl) also checks
the applied skill. New generic scorers: sections_in_order, text_present, length_between.

Phase 06 (D5, ADR-0293): a case lost or interrupted (no `done` event: `agent_stream_interrupted`,
a provider error, an HTTP refusal, a document that never indexed) is a failure in every metric it
would have counted in, never a case dropped from a denominator; each suite reports its lost cases
by code. `--rescore <run>` scores a recorded run (build/evals/<run>.json) again under this rule and
prints the old and new scores and verdicts per suite.

Phase 06 (owner decision of 2026-10-25, ADR-0320): a skill suite gates the mechanism and reports
the content. The metrics a suite's threshold names are gated; the others are printed and recorded
as reported. More turn fields: `traced_skill` (the skill whose block the answer's
`context.assemble` span shows, as name@version, read from the trace) and `refused` (the error
code of a refusal before any stream). A case may name a seed `user` (default the eval user); a
refusal it expects (`expect.refused`) is its outcome, not a lost case. Skill suites run one case at
a time with a longer, jittered 429 backoff; a skill suite with more than 5% of its cases lost to
the provider's 429s stops the run and reports the rate (a key or tier with more headroom is the
owner's cost decision).

Phase 08 (I6, ADR-0338): a suite may need knowledge bases in place first: `fixtures.yaml` beside it
lists them with their documents and source metadata, and the harness creates them as the admin
(an existing knowledge base or document is kept), uploads what is missing and waits until it is indexed. A
case's `selected_kbs` goes with its turn (the composer's toggles). More turn fields, for metrics
that name them: `citations` (the done event's), `retrieved` (the trace's `rag.retrieve` results)
and `documents` (each fixture key's id). More scorers: `retrieved` (hit@8), `cites` (citations with
their attribution) and `text_first` (the latest figure before any superseded one). Generic: no
capability and no knowledge base is named here.
"""

import asyncio
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import time
import unicodedata
import uuid
from datetime import UTC, datetime
from pathlib import Path

# Ragas posts usage events to an outside service (https://t.explodinggradients.com) unless this is
# set, and it caches the value the first time it reads it; so it is set here, before anything
# imports ragas (owner decision S3). The harness calls nothing outside the platform: the gateway,
# Keycloak and Langfuse of the stack, and LiteLLM for the judge.
os.environ["RAGAS_DO_NOT_TRACK"] = "true"

import httpx
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from get_token import env, login  # noqa: E402

# A dedicated seed user whose memory is switched off before every run, so a case never
# reads what an earlier case or run stored; the analyst keeps memory for smoke (ADR-0074).
EVAL_USER = ("evaluator", "evaluator")
# The seed users a case may run as (`user`), by name: the realm's development users (ADR-0065).
SEED_USERS = {"evaluator": EVAL_USER, "viewer": ("viewer", "viewer"), "analyst": ("analyst", "analyst")}
# Guard cases run through the admin-only policy test endpoint (I6).
ADMIN_USER = ("admin", "admin")
DETERMINISTIC = {"regex", "presidio", "keyword_list", "tier_ceiling"}
_FOLD = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه", **{a: str(i) for i, a in enumerate("٠١٢٣٤٥٦٧٨٩")}})
# Tashkeel (U+064B–U+065F) and the superscript alef (U+0670); not U+0660–U+0669, the
# Arabic-Indic digits between them, which fold() turns into ASCII digits (Q2).
_DIACRITICS = re.compile("[\u064b-\u065f\u0670]")


def fold(text: str) -> str:
    return _DIACRITICS.sub("", unicodedata.normalize("NFKC", text).replace("ـ", "")).translate(_FOLD).casefold()


def fold_pattern(pattern: str) -> str:
    """A regular expression folded as fold() folds text, but not casefolded (`\\W` would become
    `\\w`); match it with re.IGNORECASE against fold()ed text (phase 06)."""
    return _DIACRITICS.sub("", unicodedata.normalize("NFKC", pattern).replace("ـ", "")).translate(_FOLD)


def script_share(text: str, script: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    arabic = sum("؀" <= c <= "ۿ" for c in letters)
    return (arabic if script == "arabic" else len(letters) - arabic) / len(letters)


_DIGITS = re.compile(r"\d+")


def contains(answer: str, expected: str) -> bool:
    """`expected` in the answer after fold(); a number only as a whole number, so 40 never
    matches 140 or 2040 (owner decision Q2)."""
    text, wanted = fold(answer), fold(expected)
    if _DIGITS.fullmatch(wanted):
        return re.search(rf"(?<!\d){wanted}(?!\d)", text) is not None
    return wanted in text


def says(answer: str, phrase: str) -> bool:
    """`phrase` in the answer as whole words, after fold() (Q2): «ليس في» is not «وليس فيه»."""
    return re.search(rf"(?<!\w){re.escape(fold(phrase))}(?!\w)", fold(answer)) is not None


def abstained(answer: str, citations: list) -> bool:
    """An abstention says so in a whole phrase and shows no source line (Q2, ADR-0140)."""
    return not citations and any(says(answer, phrase) for phrase in ABSTAIN_PHRASES)


# A capability label or a bracketed file name copied into an answer (owner decision C1).
# Any bracketed lower-case tag ([docs], a capability's slug) or a bracketed file name (phase 08: generic,
# so the harness names no capability).
_LABEL = re.compile(r"\[[a-z_]{2,20}\](?!\()|\[[^\]\n]*\.(?:pdf|docx|xlsx|txt|md)\b", re.I)


def has_label(answer: str) -> bool:
    return _LABEL.search(answer or "") is not None


def applied_skill(data: dict) -> str | None:
    """The slug of the skill a done event's answer was written with (state_digest.skill, slug@version)."""
    ref = (data.get("state_digest") or {}).get("skill")
    return ref.split("@", 1)[0] if isinstance(ref, str) and ref else None


def skill_problems(case: dict, skill: str | None) -> list[str]:
    """routing.jsonl's `expected_skill` (null when none, phase 06, I10)."""
    if "expected_skill" not in case or case["expected_skill"] == skill:
        return []
    return [f"skill {skill} != {case['expected_skill']}"]


def check(case: dict, answer: str) -> list[str]:
    expect, failures = case.get("expect", {}), []
    if "contains_any" in expect and not any(contains(answer, s) for s in expect["contains_any"]):
        failures.append(f"none of {expect['contains_any']}")
    wrong = [s for s in expect.get("excludes_any", []) if contains(answer, s)]
    if wrong:
        failures.append(f"contains {wrong}")
    if "script" in expect and script_share(answer, expect["script"]) < 0.6:
        failures.append(f"not mostly {expect['script']}")
    return failures


# ------------------------------------------------------------------ lost cases (phase 06, D5)

_LOST = re.compile(r"^no done event \((.*)\)$")


def lost_code(event: dict) -> str | None:
    """Why a turn produced no result, or None when it ended `done`: the terminal event's code,
    or `no_terminal_event` when the stream ended without one."""
    if event.get("type") == "done":
        return None
    return (event.get("data") or {}).get("code") or ("no_terminal_event" if not event else event.get("type", "unknown"))


def lost_of(record: dict) -> str | None:
    """A recorded case's lost code: its `lost` field, or, in a record written before phase 06,
    the code inside its `no done event (...)` problem."""
    if record.get("lost"):
        return record["lost"]
    for problem in record.get("problems") or []:
        match = _LOST.match(problem)
        if match:
            return match.group(1) or "None"
    return None


def lost_by_code(records: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        code = lost_of(record)
        if code:
            counts[code] = counts.get(code, 0) + 1
    return dict(sorted(counts.items()))


# ------------------------------------------------------------------ provider 429s (phase 05, G1)

RETRY_DELAYS_S = (5, 10, 20, 40, 60)  # bounded: five retries, about two minutes
# Skill suites (owner decision of 2026-10-25): longer and jittered, about eight minutes at most.
SKILL_RETRY_DELAYS_S = (15, 30, 60, 120, 240)
SKILL_LOST_TO_429_MAX = 0.05  # past this share of a skill suite lost to 429s, the run stops
RETRIES = {"count": 0, "delays": RETRY_DELAYS_S, "jitter": 0.0}  # this suite's; reset per suite


def retryable(event: dict) -> bool:
    """A turn the provider refused for now: LiteLLM's 429 ends it as model_unavailable."""
    return event.get("type") == "error" and event.get("data", {}).get("code") == "model_unavailable"


def with_backoff(call, is_retryable):
    """`call()` again after each delay while `is_retryable(result)`; the last result otherwise."""
    result = call()
    for base in RETRIES["delays"]:
        if not is_retryable(result):
            return result
        RETRIES["count"] += 1
        delay = base * (1 + random.uniform(-RETRIES["jitter"], RETRIES["jitter"]))
        print(f"    provider busy; retrying in {delay:.0f} s")
        time.sleep(delay)
        result = call()
    return result


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
    if case.get("skill"):
        body["skill"] = case["skill"]  # the composer's picker (phase 06, ADR-0292)
    if case.get("selected_kbs"):
        body["selected_kbs"] = case["selected_kbs"]  # the composer's knowledge base toggles (phase 08, I3)
    headers = {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}

    def once() -> dict:
        last: dict = {}
        try:
            with http.stream("POST", f"{gateway}/v1/chat/turns", json=body, headers=headers) as response:
                if response.status_code >= 400:
                    # A refusal before any stream (429 too_many_turns, 502 agent_unavailable) is a
                    # lost case under its HTTP status, never a crashed run (D5); its error code is
                    # kept as `refusal` (403 skill_forbidden, phase 06).
                    response.read()
                    try:
                        refusal = (response.json().get("error") or {}).get("code")
                    except (ValueError, AttributeError):
                        refusal = None
                    return {"type": "error", "data": {"code": f"http_{response.status_code}", "refusal": refusal}}
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        last = json.loads(line[6:])
        except httpx.TransportError as exc:
            return {"type": "error", "data": {"code": f"transport_{type(exc).__name__}"}}
        return last

    return with_backoff(once, retryable)


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
# How an answer says the document does not hold the answer: whole phrases, after fold() (Q2).
# Three entries of f5fc687 are kept (ADR-0181): «لا توجد اي معلومات» is «لا توجد معلومات» with «أي»,
# and «لا يمكن تحديد» says the document does not settle the question.
ABSTAIN_PHRASES = [
    "لم اجد", "لا اجد", "لم يرد", "لا يرد", "لا يتضمن", "لا تتضمن", "لم يتضمن", "لم تتضمن", "لا يحتوي",
    "لا تحتوي", "لم يذكر", "لم تذكر", "لا يذكر", "لا تذكر", "غير مذكور", "لا توجد معلومات", "لا تتوفر",
    "لم تشر", "لا تشير", "لا يشير", "لا توجد اي معلومات", "لا يمكن تحديد", "ليس في", "لا يوجد في", "لا يوجد ذكر", "لم ترد", "لا ترد", "لا يمكنني العثور",
    "not mentioned", "does not mention", "not in the", "no information",
]


def upload(http: httpx.Client, gateway: str, token: str, conversation_id: str, document: str) -> str:
    """Uploads a generated document to a new conversation and waits until it is indexed."""
    path = DOCS / document
    headers = {"Authorization": f"Bearer {token}"}
    for _ in range(5):
        with path.open("rb") as handle:
            response = http.post(
                f"{gateway}/v1/conversations/{conversation_id}/attachments",
                files={"file": (path.name, handle)},
                headers=headers,
            )
        if response.status_code != 429:
            break
        time.sleep(61)  # the gateway's per-user upload allowance is per minute (ADR-0177)
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


def _milliseconds(span: dict) -> float | None:
    """A span's duration from its Langfuse start and end times."""
    try:
        start = datetime.fromisoformat(span["startTime"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(span["endTime"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    return (end - start).total_seconds() * 1000


def retrieved(http: httpx.Client, cfg: dict, auth: tuple[str, str], trace_id: str, timing: dict | None = None):
    """What the turn's `rag.retrieve` span reports (ids, pages, section hashes, scores), from
    Langfuse; None when the trace has no retrieval. With `timing`, its duration lands in
    timing["retrieve_ms"] (owner decision Q1: latency per rerank configuration)."""
    for _ in range(60):
        response = http.get(f"{cfg['LANGFUSE_PUBLIC_URL']}/api/public/traces/{trace_id}", auth=auth)
        if response.status_code == 200:
            spans = [o for o in response.json().get("observations", []) if o.get("name") == "rag.retrieve"]
            for span in spans:
                metadata = span.get("metadata") or {}
                attributes = metadata.get("attributes", metadata)
                if "rag.results" in attributes:
                    value = attributes["rag.results"]
                    if timing is not None:
                        timing["retrieve_ms"] = _milliseconds(span)
                    return json.loads(value) if isinstance(value, str) else value
            names = {o.get("name") for o in response.json().get("observations", [])}
            if "model.call" in names and "tool.exec" not in names and "rag.retrieve" not in names:
                return None
        time.sleep(2)
    return None


def traced_skill(http: httpx.Client, cfg: dict, auth: tuple[str, str], trace_id: str | None,
                 ref: str | None) -> str | None:
    """The slug of the skill whose block the answer's `context.assemble` span shows, from Langfuse
    (phase 06, ADR-0320): a `skill` block with tokens and the turn's `name@version` (`ref`, the done
    event's) among the package's prompts. None when the answer's context holds no skill block;
    `mismatch:<refs>` when its block names another skill or version."""
    if not trace_id:
        return None
    for _ in range(60):
        response = http.get(f"{cfg['LANGFUSE_PUBLIC_URL']}/api/public/traces/{trace_id}", auth=auth)
        if response.status_code == 200:
            for span in response.json().get("observations", []):
                context = (span.get("metadata") or {}).get("context") or {}
                if span.get("name") != "context.assemble" or context.get("node") != "answer":
                    continue
                tokens = {b.get("name"): b.get("tokens", 0) for b in context.get("blocks") or []}
                if not tokens.get("skill"):
                    return None
                prompts = context.get("prompts") or []
                return ref.split("@")[0] if ref and ref in prompts else f"mismatch:{','.join(prompts)}"
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

        def once():
            try:
                return asyncio.run(both())
            except Exception as exc:  # a rate-limited judge call is retried; anything else is raised
                if "429" in str(exc) or "RateLimit" in type(exc).__name__:
                    return exc
                raise

        result = with_backoff(once, lambda r: isinstance(r, Exception))
        if isinstance(result, Exception):
            raise result
        faithfulness, correctness = result
        return float(faithfulness), float(correctness)


def contexts_for(document: str, results: list[dict], sections: dict) -> list[str]:
    """The text of the pages and sections retrieval returned, once each, in rank order, each
    under the source label the answer model saw (`context.retrieval`)."""
    entry = sections.get(document, {})
    texts: list[str] = []
    for result in results or []:
        text = None
        if result.get("page") is not None:
            body = entry.get("pages", {}).get(str(result["page"]))
            text = body and f"{document}، صفحة {result['page']}:\n{body}"
        elif result.get("section_sha"):
            section = entry.get("sections", {}).get(result["section_sha"]) or {}
            text = section.get("text") and f"{document}، {section['path']}:\n{section['text']}"
        if text and text not in texts:
            texts.append(text)
    return texts


def expected_section_sha(document: str, section: str, sections: dict) -> str | None:
    for sha, entry in sections.get(document, {}).get("sections", {}).items():
        if entry["path"] == section or entry["path"].endswith(f" › {section}"):
            return sha
    return None


def metric_document_turn(http, cfg, token: str, case: dict) -> dict:
    """A metrics suite's case over a document (phase 06, I9): a new conversation, the document
    uploaded and indexed, then the turn with it attached (unless `attach` is false). The terminal
    event, or a lost one when the upload failed."""
    gateway = cfg["GATEWAY_PUBLIC_URL"]
    conversation = str(uuid.uuid4())
    try:
        attachment = upload(http, gateway, token, conversation, case["document"])
    except (RuntimeError, httpx.HTTPError) as exc:
        code = "upload_failed" if isinstance(exc, httpx.HTTPError) else "not_indexed"
        return {"type": "error", "data": {"code": code}}
    return run_turn(http, gateway, token, case, conversation_id=conversation,
                    attachment_ids=[attachment] if case.get("attach", True) else [])


def run_document_case(http, cfg, token: str, case: dict, judge: "Judge | None", sections: dict, auth) -> dict:
    """One routing or rag case in a new conversation with its document indexed first."""
    gateway = cfg["GATEWAY_PUBLIC_URL"]
    conversation = str(uuid.uuid4())
    try:
        attachment = upload(http, gateway, token, conversation, case["document"])
    except (RuntimeError, httpx.HTTPError) as exc:  # the case is lost, the run goes on (D5)
        code = "upload_failed" if isinstance(exc, httpx.HTTPError) else "not_indexed"
        return {"event": {}, "lost": code, "problems": [f"no done event ({code})"]}
    attach = case.get("attach", "pages" in case["expect"] or case["expect"].get("kind") is not None)
    event = run_turn(http, gateway, token, case, conversation_id=conversation,
                     attachment_ids=[attachment] if attach else [])
    code = lost_code(event)
    if code:
        return {"event": event, "lost": code, "problems": [f"no done event ({code})"]}
    data = event["data"]
    outcome = {"event": event, "answer": data["answer"], "trace_id": data.get("trace_id"),
               "capabilities": data["state_digest"]["capabilities"], "citations": data.get("citations") or [],
               "skill": applied_skill(data)}
    expect = case["expect"]
    problems: list[str] = []
    if "capabilities" in expect:  # routing
        if outcome["capabilities"] != expect["capabilities"]:
            problems.append(f"capabilities {outcome['capabilities']} != {expect['capabilities']}")
        problems += skill_problems(case, outcome["skill"])
        outcome["problems"] = problems
        return outcome
    kind = expect["kind"]
    answer = data["answer"]
    cited = [c for c in outcome["citations"] if c.get("document_id") == attachment]
    cited_pages = sorted({p for c in cited for p in c.get("pages") or []})
    outcome["cited_pages"] = cited_pages
    if expect.get("contains_any") and not any(contains(answer, s) for s in expect["contains_any"]):
        problems.append(f"none of {expect['contains_any']}")
    if any(contains(answer, s) for s in expect.get("excludes_any", [])):
        problems.append("shows text it must not show")
    if has_label(answer):
        problems.append("a label or bracketed file name in the answer")
    timing: dict = {}
    results = retrieved(http, cfg, auth, outcome["trace_id"], timing) if outcome["trace_id"] else None
    outcome["retrieved"] = results
    if timing.get("retrieve_ms") is not None:
        outcome["retrieve_ms"] = timing["retrieve_ms"]
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
        # An abstention says so in a whole phrase and shows no source line (Q2).
        outcome["abstained"] = abstained(answer, outcome["citations"])
        if not outcome["abstained"]:
            problems.append("did not abstain: no whole abstention phrase, or a source line shown")
    if kind == "answerable" and judge is not None:
        contexts = contexts_for(case["document"], results or [], sections)
        faithfulness, correctness = judge.score(case["message"], answer, contexts, case["expect"]["reference"])
        outcome["faithfulness"], outcome["correctness"] = faithfulness, correctness
    outcome["problems"] = problems
    return outcome


def rag_metrics_of(case: dict, judged: bool = True) -> list[str]:
    """The rag metrics a case counts in, from the case alone, so a lost case counts in the same
    ones as an answered case would (D5): hit@8 for answerable and pii cases; citation page
    accuracy for answerable cases with expected pages and for general and abstain cases;
    abstention for abstain cases; faithfulness and answer correctness for answerable cases."""
    expect = case.get("expect", {})
    kind = expect.get("kind")
    names = []
    if kind in ("answerable", "pii"):
        names.append("hit")
    if (kind == "answerable" and expect.get("pages")) or kind in ("general", "abstain"):
        names.append("citation")
    if kind == "abstain":
        names.append("abstain")
    if kind == "answerable" and judged:
        names.append("ragas")
    return names


def rag_scores(outcomes: list[tuple[dict, dict]], judged: bool = True) -> dict:
    """Each metric over every case that counts in it; a lost case is a failure (0 for Ragas)."""
    def mean(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    hits: list[float] = []
    citations: list[float] = []
    abstentions: list[float] = []
    faithfulness: list[float] = []
    correctness: list[float] = []
    for case, outcome in outcomes:
        names = rag_metrics_of(case, judged)
        lost = lost_of(outcome) is not None
        if "hit" in names:
            hits.append(0.0 if lost else float(bool(outcome.get("hit"))))
        if "citation" in names:
            citations.append(0.0 if lost else float(bool(outcome.get("citation_ok"))))
        if "abstain" in names:
            abstentions.append(0.0 if lost else float(bool(outcome.get("abstained"))))
        if "ragas" in names:
            faithfulness.append(0.0 if lost else float(outcome.get("faithfulness") or 0.0))
            correctness.append(0.0 if lost else float(outcome.get("correctness") or 0.0))
    latencies = sorted(o["retrieve_ms"] for _, o in outcomes if o.get("retrieve_ms") is not None)

    def percentile(share: float) -> float:
        return latencies[min(len(latencies) - 1, int(share * len(latencies)))] if latencies else 0.0

    return {
        "hit_at_8": mean(hits),
        "citation_page_accuracy": mean(citations),
        "faithfulness": mean(faithfulness),
        "answer_correctness": mean(correctness),
        "abstention": mean(abstentions),
        "retrieve_ms_p50": percentile(0.5),
        "retrieve_ms_p95": percentile(0.95),
        "counts": {"hit": len(hits), "citation": len(citations), "abstain": len(abstentions),
                   "ragas": len(faithfulness), "retrieve": len(latencies)},
        "lost": lost_by_code([o for _, o in outcomes]),
    }


def rag_threshold_ok(scores: dict, threshold: dict) -> bool:
    return all(scores[name] >= value for name, value in threshold.items())


def rerank_config() -> str:
    """The rerank configuration the stack runs with, for the rag summary (Q1). It comes from the
    environment now, not config/docs.yaml (review round 2, finding 3): the same RERANK_* the agent
    reads, so the summary names what actually ran."""
    values = env()
    if (values.get("RERANK_ENABLED") or "").strip().lower() not in {"1", "true", "yes", "on"}:
        return "rerank off"
    candidates = (values.get("RERANK_CANDIDATES") or "16").strip()
    bound = (values.get("RERANK_MAX_CHARS") or "").strip()
    return f"rerank {candidates} candidates, {f'{bound} chars' if bound else 'full text'}"


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


# ------------------------------------------------------------------ capability metrics (phase 05, ADR-0240)

METRICS_FILE = "metrics.yaml"
_NUMBER = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)(?![\d.])")


def numbers(answer: str) -> list[float]:
    """Every number in the answer, Arabic-Indic digits and decimal marks folded: «١٢٫٧» is 12.7."""
    text = re.sub(r"(?<=\d)[٫,](?=\d)", ".", fold(answer))
    return [float(value) for value in _NUMBER.findall(text)]


_URL = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>()\[\]{}\"'«»]+")


def urls(answer: str) -> list[str]:
    """The links an answer holds (http, https or www.), without trailing punctuation."""
    return [match.rstrip(".,;:!?؟،…*_`") for match in _URL.findall(answer or "")]


def case_value(case: dict, path: str):
    value = case
    for key in path.split("."):
        value = value.get(key) if isinstance(value, dict) else None
    return value


def _alternatives(item) -> list[str]:
    return [item] if isinstance(item, str) else [str(alt) for alt in item]


def _alternatives_all(items) -> list[str]:
    """A text, or a list of texts and lists of alternatives, flattened."""
    if items is None:
        return []
    if isinstance(items, str):
        return [items]
    return [alt for item in items for alt in _alternatives(item)]


def score_check(check: dict, case: dict, answer: str, turn: dict) -> bool:
    """One generic check (see a capability's evals/metrics.yaml); names no capability."""
    scorer = check["scorer"]
    expected = case_value(case, check["expected"]) if "expected" in check else None
    if scorer == "field_equals":
        wanted = expected if expected is not None else check.get("default")
        return turn.get(check["field"]) == wanted
    if scorer == "number":
        if expected is None:
            return False
        tolerance = float(check.get("tolerance", 0))
        return any(abs(found - float(expected)) <= tolerance + 1e-9 for found in numbers(answer))
    if scorer == "candidates":
        items = list(expected or [])
        named = sum(any(contains(answer, alt) for alt in _alternatives(item)) for item in items)
        wanted = len(items) if check.get("min", "all") == "all" else int(check["min"])
        return bool(items) and named >= wanted
    if scorer == "abstention":
        return any(says(answer, phrase) for phrase in check["phrases"])
    if scorer == "text_absent":
        folded = fold(answer)
        if any(contains(answer, text) for text in expected or []):
            return False
        # `patterns_from`: more patterns from the case (phase 06), e.g. only where a field was not given.
        patterns = list(check.get("patterns", [])) + list(
            (case_value(case, check["patterns_from"]) or []) if "patterns_from" in check else [])
        return not any(re.search(fold_pattern(pattern), folded, re.I) for pattern in patterns)
    if scorer == "text_present":
        # Every expected text (or one of its alternatives) is in the answer, folded (phase 06, I9).
        items = list(expected or []) + list(check.get("texts", []))
        return all(any(contains(answer, alt) for alt in _alternatives(item)) for item in items)
    if scorer == "sections_in_order":
        if "expected" in check:
            sections = case_value(case, check["expected"])
        elif "by" in check:  # one layout per value of a case field, e.g. its locale
            sections = check["sections"].get(case_value(case, check["by"]))
        else:
            sections = check["sections"]
        return sections_in_order(answer, sections)
    if scorer == "urls_from_inputs":
        # Every link in the answer is in the case's inputs, word for word (phase 08, F3): the message,
        # plus any case field `inputs` names. An answer with no link passes.
        given = " ".join(str(case_value(case, path) or "") for path in check.get("inputs", ["message"]))
        return all(url in given for url in urls(answer))
    if scorer == "retrieved":
        # Retrieval hit@8 (phase 08, I6): each expected {document, pages} (`min: all`, or at least one with
        # `min: 1`) is among the turn's retrieved chunks; documents are the suite's fixture keys.
        documents = turn.get("documents") or {}
        results = turn.get("retrieved") or []
        hits = [any(r.get("attachment_id") == documents.get(item["document"]) and r.get("page") in item.get("pages", [])
                    for r in results) for item in expected or []]
        return bool(hits) and (all(hits) if check.get("min", "all") == "all" else sum(hits) >= int(check["min"]))
    if scorer == "cites":
        # Citations with their source attribution (phase 08, I4, I6): every expected {document, pages, ...}
        # is cited on expected (or acceptable) pages, with every other field of the expected item equal
        # (e.g. the source's collection slug) and every field `required` names present (e.g. its name,
        # publisher and date); a citation of a document neither expected nor in `allow` fails.
        documents = turn.get("documents") or {}
        citations = turn.get("citations") or []
        keys = [item["document"] for item in expected or []]
        keys += list(case_value(case, check["allow"]) or []) if "allow" in check else []
        allowed = {documents.get(key) for key in keys}
        if any(c.get("document_id") not in allowed for c in citations):
            return False
        required = check.get("required", [])
        for item in expected or []:
            pages = set(item.get("pages", [])) | set(item.get("acceptable_pages", []))
            fields = {k: v for k, v in item.items() if k not in ("document", "pages", "acceptable_pages")}
            match = [c for c in citations if c.get("document_id") == documents.get(item["document"])]
            if not any(all(c.get(k) == v for k, v in fields.items()) and all(c.get(k) for k in required)
                       and c.get("pages") and set(c["pages"]) <= pages for c in match):
                return False
        return bool(expected)
    if scorer == "text_first":
        # The expected text (or an alternative) is in the answer, before any `before` text (phase 08, D3):
        # a superseded figure may be noted as the change, never given first as the answer.
        folded = fold(answer)
        found = [folded.find(fold(alt)) for alt in _alternatives_all(expected)]
        first = min((at for at in found if at >= 0), default=-1)
        if first < 0:
            return False
        earlier = [folded.find(fold(alt)) for alt in _alternatives_all(case_value(case, check["before"]) if "before" in check else [])]
        return all(at < 0 or at > first for at in earlier)
    if scorer == "length_between":
        words = len(re.findall(r"\w+", answer))
        return int(check.get("min", 0)) <= words <= int(check.get("max", 10**9))
    raise ValueError(f"unknown scorer {scorer!r}")


def sections_in_order(answer: str, sections: list) -> bool:
    """Each section, an Arabic-folded regular expression (or a list of alternatives), is found in
    the answer after the previous one (phase 06, I9): the layout's headings in their order. A
    pattern is Arabic-folded like the answer and matched case-insensitively, line by line (re.M)."""
    folded, at = fold(answer), 0
    for section in sections or []:
        found = [m for m in (re.search(fold_pattern(p), folded[at:], re.I | re.M) for p in _alternatives(section)) if m]
        if not found:
            return False
        at += min(m.end() for m in found)
    return bool(sections)


FIXTURES_FILE = "fixtures.yaml"
FIXTURE_WAIT_S = 240


def load_fixtures(path: Path) -> dict | None:
    """What a suite needs in place before its cases (phase 08): `knowledge_bases`, each with its
    documents and their source metadata, from fixtures.yaml beside the suite; None when it has none."""
    file = path.parent / FIXTURES_FILE
    if not file.is_file():
        return None
    fixtures = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    # `include`: another suite's fixtures, by path from the repository root, so two suites share them.
    for included in fixtures.pop("include", []) or []:
        other = yaml.safe_load((REPO / included).read_text(encoding="utf-8")) or {}
        fixtures["knowledge_bases"] = [*(fixtures.get("knowledge_bases") or []), *(other.get("knowledge_bases") or [])]
    return fixtures


def prepare_fixtures(http: httpx.Client, cfg: dict, fixtures: dict) -> dict[str, str]:
    """Creates the suite's knowledge bases as the admin (an existing one is kept), uploads each
    document not there yet with its metadata (`supersedes` names another document's key), and waits
    until every one is indexed. Returns each document key's id. Generic: the KBs are the suite's."""
    gateway = cfg["GATEWAY_PUBLIC_URL"]
    admin = {"Authorization": f"Bearer {login(*ADMIN_USER)['access_token']}"}
    ids: dict[str, str] = {}
    for base in fixtures.get("knowledge_bases") or []:
        slug = base["slug"]
        created = http.post(f"{gateway}/v1/admin/kbs", headers=admin, json={
            "slug": slug, "name": base["name"], "description": base.get("description") or {"ar": "", "en": ""}})
        if created.status_code not in (201, 409):
            created.raise_for_status()
        listed = http.get(f"{gateway}/v1/admin/kbs/{slug}/documents", headers=admin)
        listed.raise_for_status()
        held = {d["metadata"]["title"]: d for d in listed.json()["data"]["documents"] if d["status"] != "failed"}
        for document in base.get("documents") or []:
            meta = dict(document["metadata"])
            if meta.get("supersedes"):
                meta["supersedes"] = ids[meta["supersedes"]]
            if meta["title"] in held:
                ids[document["key"]] = held[meta["title"]]["id"]
                continue
            path = DOCS / document["file"]
            fields = {k: str(v) for k, v in meta.items() if v}
            for _ in range(5):
                with path.open("rb") as handle:
                    response = http.post(f"{gateway}/v1/admin/kbs/{slug}/documents", headers=admin, data=fields,
                                         files={"file": (path.name, handle)})
                if response.status_code != 429:
                    break
                time.sleep(61)  # the per-user upload allowance is per minute (ADR-0177)
            response.raise_for_status()
            ids[document["key"]] = response.json()["data"]["id"]
        deadline = time.monotonic() + FIXTURE_WAIT_S
        while True:
            listed = http.get(f"{gateway}/v1/admin/kbs/{slug}/documents", headers=admin)
            listed.raise_for_status()
            status = {d["id"]: d["status"] for d in listed.json()["data"]["documents"]}
            wanted = [ids[d["key"]] for d in base.get("documents") or []]
            if any(status.get(i) == "failed" for i in wanted):
                raise SystemExit(f"a document of knowledge base {slug} failed to index")
            if all(status.get(i) == "indexed" for i in wanted):
                break
            if time.monotonic() > deadline:
                raise SystemExit(f"the documents of knowledge base {slug} were not indexed in {FIXTURE_WAIT_S} s")
            time.sleep(1)
        print(f"  (fixture: knowledge base {slug}, {len(wanted)} documents indexed)")
    return ids


def load_metrics(path: Path) -> dict | None:
    """The metrics of a suite's folder, from its metrics.yaml; None when it has none."""
    file = path.parent / METRICS_FILE
    return yaml.safe_load(file.read_text(encoding="utf-8")) if file.is_file() else None


def metric_outcome(metrics: dict, case: dict, answer: str, turn: dict) -> tuple[dict[str, bool], list[str]]:
    """The metrics a case counts in, each pass or fail, and what failed."""
    kind = case_value(case, metrics.get("case_kind", "expect.kind"))
    common = metrics.get("common", [])
    outcome: dict[str, bool] = {}
    problems: list[str] = []
    common_ok = True
    for check in common:
        if not score_check(check, case, answer, turn):
            common_ok = False
            problems.append(f"{check['scorer']} {check.get('field') or check.get('expected')} failed")
    for metric, kinds in metrics["metrics"].items():
        if kind not in kinds:
            continue
        outcome[metric] = common_ok and all(score_check(c, case, answer, turn) for c in kinds[kind])
        if not outcome[metric]:
            problems.append(f"{metric} failed")
    return outcome, problems


def metric_scores(metrics: dict, outcomes: list[dict[str, bool]]) -> dict:
    scores, counts = {}, {}
    for metric in metrics["metrics"]:
        values = [o[metric] for o in outcomes if metric in o]
        scores[metric] = sum(values) / len(values) if values else 0.0
        counts[metric] = len(values)
    return {**scores, "counts": counts}


# ------------------------------------------------------------------ the second document backend (phase 05)

MCP_SUFFIX = "@mcp"
_BACKEND = {"current": ""}


def use_backend(name: str) -> None:
    """Recreates the agent on DOCS_BACKEND=`name` ("" restores config/docs.yaml's) and waits for it."""
    if _BACKEND["current"] == name:
        return
    print(f"  (agent recreated with DOCS_BACKEND={name or 'config/docs.yaml'})")
    subprocess.run(
        ["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "--wait", "agent"],
        cwd=REPO, check=True, env={**os.environ, "DOCS_BACKEND": name}, stdout=subprocess.DEVNULL,
    )
    _BACKEND["current"] = name


def suites() -> dict[str, Path]:
    files = sorted((REPO / "evals" / "golden").glob("*.jsonl"))
    files += sorted((REPO / "services" / "agent" / "app" / "capabilities").glob("*/evals/*.jsonl"))
    files += sorted((REPO / "skills").glob("*/evals/*.jsonl"))  # phase 06 (I9): names no skill
    found = {path.stem: path for path in files}
    # rag.jsonl runs on both document backends; the second run is `rag@mcp` (phase 05, I11).
    ordered: dict[str, Path] = {}
    for name, path in found.items():
        ordered[name] = path
        if name == "rag":
            ordered[f"rag{MCP_SUFFIX}"] = path
    return ordered


def main() -> int:
    if sys.argv[1:2] == ["--rescore"]:
        return rescore_main(sys.argv[2:])
    cfg = env()
    thresholds = yaml.safe_load((REPO / "evals" / "thresholds.yaml").read_text()) or {}
    found = suites()
    if not found:
        print("no eval suites found")
        return 1
    token = login(*EVAL_USER)["access_token"]  # fail before any Langfuse write when the user cannot log in
    auth = (cfg["LANGFUSE_PUBLIC_KEY"], cfg["LANGFUSE_SECRET_KEY"])
    run_name = f"make-eval-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    rows: list = []
    # Per-case results and suite scores, written to build/evals/<run>.json at the end so a run
    # survives `make clean` even though its Langfuse dataset does not (review round 2, finding 4).
    records: dict[str, dict] = {}
    sections = json.loads((DOCS / "sections.json").read_text(encoding="utf-8")) if (DOCS / "sections.json").exists() else {}
    only = set(sys.argv[1:])  # `make eval s=rag` runs one suite (and rag@mcp); empty runs them all

    with httpx.Client(timeout=300) as http:
        disable_memory(http, cfg["GATEWAY_PUBLIC_URL"], token)
        try:
            for name, path in found.items():
                if only and name not in only and name.removesuffix(MCP_SUFFIX) not in only:
                    continue
                use_backend("mcp" if name.endswith(MCP_SUFFIX) else "")
                skill_suite = is_skill_suite(path)
                RETRIES.update(count=0, delays=SKILL_RETRY_DELAYS_S if skill_suite else RETRY_DELAYS_S,
                               jitter=0.2 if skill_suite else 0.0)
                run_suite(http, cfg, auth, run_name, name, path, thresholds, sections, rows, records)
                share = lost_to_429(records[name])
                if skill_suite and share > SKILL_LOST_TO_429_MAX:
                    print(f"\nSTOP: {name} lost {share:.0%} of its cases to provider 429s "
                          f"(over {SKILL_LOST_TO_429_MAX:.0%}); a key or tier with more headroom is the "
                          "owner's cost decision. The suites after it were not run.")
                    stopped = True
                    break
            else:
                stopped = False
        finally:
            use_backend("")  # pass or fail, the internal backend is restored
    failed = stopped or any(not row[5] for row in rows)
    return finish(run_name, rows, records, failed)


def is_skill_suite(path: Path) -> bool:
    """A suite of a skill's own evals (skills/<name>/evals/)."""
    return path.resolve().is_relative_to((REPO / "skills").resolve())


def lost_to_429(suite: dict) -> float:
    """The share of a suite's cases lost to the provider's 429 (`model_unavailable` after the backoff)."""
    cases = suite.get("cases_recorded") or 0
    return (suite.get("lost") or {}).get("model_unavailable", 0) / cases if cases else 0.0


def run_suite(http, cfg, auth, run_name, name, path, thresholds, sections, rows, records) -> None:
    judge: Judge | None = None
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    # One Langfuse dataset per suite, one item per case, one run per `make eval` (README §12).
    langfuse(http, cfg, auth, "v2/datasets", {"name": name, "description": f"{path.relative_to(REPO)}"})
    passed = 0
    guard_results: list[tuple[dict, bool, str]] = []
    checkers: dict[str, str] = {}
    document_outcomes: list[tuple[dict, dict]] = []
    metrics = load_metrics(path)  # the suite's own metrics (a capability's evals/metrics.yaml)
    fixtures = load_fixtures(path)  # what the suite needs in place first (phase 08: knowledge bases)
    documents = prepare_fixtures(http, cfg, fixtures) if fixtures else {}
    threshold = thresholds.get(name.removesuffix(MCP_SUFFIX))
    metric_outcomes: list[dict[str, bool]] = []
    report_sets: dict[str, list[bool]] = {}  # cases scored and reported under a set, never gated
    gated = 0
    case_records: list[dict] = []
    for case in cases:
        item_id = f"{name}:{case['id']}"
        if "document" in case and metrics is None:
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
            case_records.append({
                "id": case["id"], "passed": not problems, "problems": problems,
                **{k: outcome.get(k) for k in ("hit", "citation_ok", "abstained", "faithfulness",
                                               "correctness", "cited_pages", "capabilities",
                                               "retrieve_ms", "answer")},
                **({"lost": outcome["lost"]} if outcome.get("lost") else {}),
            })
            trace_id = outcome.get("trace_id")
            if trace_id:
                metadata = {k: outcome.get(k) for k in ("hit", "citation_ok", "abstained", "faithfulness",
                                                        "correctness", "cited_pages", "capabilities",
                                                        "retrieve_ms")}
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
            try:
                result, problems = run_guard_case(http, cfg["GATEWAY_PUBLIC_URL"], admin, case)
            except httpx.TransportError as exc:  # lost, the run goes on (D5)
                result, problems = None, [f"no done event (transport_{type(exc).__name__})"]
            checker = checkers.get(case["policy_id"], "unknown")
            guard_results.append((case, not problems, checker))
            passed += not problems
            mark = "pass" if not problems else "FAIL"
            shown = result["action"] if result else "-"
            print(f"  {mark}  {name}/{case['id']}  [{checker}] {shown}  {'; '.join(problems)}")
            guard_lost = None if result is not None else (
                lost_of({"problems": problems}) or (problems[0].split(":")[0].replace("HTTP ", "http_") if problems else "unknown"))
            case_records.append({
                "id": case["id"], "passed": not problems, "problems": problems,
                "checker": checker, "action": result["action"] if result else None,
                **({"lost": guard_lost} if guard_lost else {}),
            })
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
        token = login(*SEED_USERS[case.get("user", "evaluator")])["access_token"]
        event = metric_document_turn(http, cfg, token, case) if "document" in case else \
            run_turn(http, cfg["GATEWAY_PUBLIC_URL"], token, case)
        scored: dict[str, bool] = {}
        capabilities: list[str] | None = None
        lost = lost_code(event)
        refusal = (event.get("data") or {}).get("refusal") if event.get("type") == "error" else None
        if lost and refusal and case_value(case, "expect.refused") is not None:
            # The refusal the case expects is its outcome, scored like any turn (phase 06, ADR-0320).
            lost, answer, trace_id = None, "", None
            turn_fields = {"capabilities": None, "skill": None, "traced_skill": None, "cited": False,
                           "refused": refusal}
            scored, problems = metric_outcome(metrics, case, answer, turn_fields) if metrics is not None \
                else ([], [] if refusal == case_value(case, "expect.refused") else [f"refused {refusal}"])
        elif not lost:
            answer = event["data"]["answer"]
            capabilities = event["data"]["state_digest"]["capabilities"]
            trace_id = event["data"].get("trace_id")
            turn_fields = {"capabilities": capabilities, "skill": applied_skill(event["data"]),
                           "cited": bool(event["data"].get("citations")), "refused": None,
                           "citations": event["data"].get("citations") or [], "documents": documents}
            if metrics is not None and '"retrieved"' in json.dumps(metrics):
                turn_fields["retrieved"] = retrieved(http, cfg, auth, trace_id) if trace_id else None
            if metrics is not None and "traced_skill" in json.dumps(metrics):
                turn_fields["traced_skill"] = traced_skill(
                    http, cfg, auth, trace_id, (event["data"].get("state_digest") or {}).get("skill"))
            if metrics is not None:
                scored, problems = metric_outcome(metrics, case, answer, turn_fields)
            else:
                problems = check(case, answer)
                wanted = case.get("expect", {}).get("capabilities")
                if wanted is not None and capabilities != wanted:
                    problems.append(f"capabilities {capabilities} != {wanted}")
                problems += skill_problems(case, turn_fields["skill"])
        else:
            answer, problems, trace_id = "", [f"no done event ({lost})"], None
            if metrics is not None:
                scored = {m: False for m in metric_outcome(metrics, case, "", {})[0]}
        if metrics is not None and not case.get("report_set"):
            # A case of a report set is scored and reported under its set, never in a gated metric.
            metric_outcomes.append(scored)
        if metrics is not None and isinstance(threshold, dict):
            # A metric the threshold does not name is reported, never gated (ADR-0320).
            problems = [p if p.split()[0] in threshold or p.startswith("no done event") else f"{p} (reported)"
                        for p in problems]
        gating = [p for p in problems if not p.endswith("(reported)")]
        report_set = case.get("report_set")
        if report_set:
            report_sets.setdefault(report_set, []).append(not gating)
        else:
            gated += 1
            passed += not gating
        mark = ("pass" if not gating else "FAIL") if not report_set else ("ok  " if not gating else "miss")
        shown = f"[{report_set}] " if report_set else ""
        print(f"  {mark}  {name}/{shown}{case['id']}  {answer[:80]!r}  {'; '.join(problems)}")
        case_records.append({
            "id": case["id"], "passed": not gating, "problems": problems, "answer": answer,
            "capabilities": capabilities, **({"report_set": report_set} if report_set else {}),
            **({"metrics": scored} if scored else {}), **({"lost": lost} if lost else {}),
        })
        if trace_id:
            langfuse(
                http, cfg, auth, "dataset-run-items",
                {"runName": run_name, "datasetItemId": item_id, "traceId": trace_id,
                 "metadata": {"passed": not gating, "problems": problems}},
            )
            langfuse(
                http, cfg, auth, "scores",
                {"traceId": trace_id, "name": "eval_pass", "value": 0 if gating else 1,
                 "dataType": "NUMERIC", "comment": f"{name}/{case['id']}"},
            )
    total = len(cases) - sum(len(v) for v in report_sets.values())
    rate = passed / total if total else 0.0
    scores = None
    if document_outcomes and isinstance(threshold, dict):
        scores = rag_scores(document_outcomes, judged=judge is not None)
        ok = rag_threshold_ok(scores, threshold)
        counts = scores["counts"]
        print(
            f"  {name}: hit@8 {scores['hit_at_8']:.2f} ({counts['hit']}), citation page accuracy "
            f"{scores['citation_page_accuracy']:.2f} ({counts['citation']}), faithfulness "
            f"{scores['faithfulness']:.2f} and answer correctness {scores['answer_correctness']:.2f} "
            f"({counts['ragas']}, judge alias {judge.alias if judge else '-'}), abstention "
            f"{scores['abstention']:.2f} ({counts['abstain']}); {rerank_config()}: rag.retrieve p50 "
            f"{scores['retrieve_ms_p50'] / 1000:.2f} s, p95 {scores['retrieve_ms_p95'] / 1000:.2f} s "
            f"({counts['retrieve']} retrieval spans, {len(case_records)} cases recorded)"
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
    elif metrics is not None and isinstance(threshold, dict):
        scores = metric_scores(metrics, metric_outcomes)
        ok = all(scores[metric] >= value for metric, value in threshold.items())
        counts = scores["counts"]
        print(f"  {name}: " + ", ".join(
            f"{metric} {scores[metric]:.2f} ({counts[metric]}){'' if metric in threshold else ', reported'}"
            for metric in metrics["metrics"]))
    else:
        ok = isinstance(threshold, int | float) and rate >= threshold
    # Reported, never gated (ADR-0241): e.g. the routing suite's keyword-free coverage.
    coverage = {key: {"passed": sum(v), "total": len(v), "rate": sum(v) / len(v)} for key, v in report_sets.items()}
    for key, value in coverage.items():
        print(f"  {name}[{key}]: {value['passed']}/{value['total']} ({value['rate']:.2f}), reported, not gated")
    lost = lost_by_code(case_records)
    print(f"  {name}: {RETRIES['count']} provider retries; lost cases {sum(lost.values())}"
          + (f" ({', '.join(f'{code} {n}' for code, n in lost.items())})" if lost else ""))
    rows.append((name, passed, total, rate, threshold, ok))
    records[name] = {
        "passed": passed, "total": total, "cases_recorded": len(case_records), "report_sets": coverage,
        "lost": lost,
        "rate": rate, "threshold": threshold, "ok": ok, "scores": scores,
        "rerank": rerank_config(), "backend": "mcp" if name.endswith(MCP_SUFFIX) else "internal",
        "retries": RETRIES["count"], "cases": case_records,
    }


def finish(run_name: str, rows: list, records: dict, failed: bool) -> int:
    run_dir = REPO / "build" / "evals"
    run_dir.mkdir(parents=True, exist_ok=True)
    record_path = run_dir / f"{run_name}.json"
    record_path.write_text(
        json.dumps({"run": run_name, "rerank": rerank_config(), "suites": records}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"\n{'suite':<14}{'passed':>9}{'rate':>8}{'threshold':>11}  result")
    for name, passed, total, rate, threshold, ok in rows:
        shown = "missing" if threshold is None else ("per metric" if isinstance(threshold, dict) else f"{threshold:.2f}")
        print(f"{name:<14}{passed:>4}/{total:<4}{rate:>8.2f}{shown:>11}  {'PASS' if ok else 'FAIL'}")
    print(f"\nLangfuse dataset run: {run_name}")
    print(f"Eval records: {record_path.relative_to(REPO)}")
    return 1 if failed else 0


# ------------------------------------------------------------------ rescoring a recorded run (phase 06, D5)


def rescore_suite(name: str, path: Path, suite: dict, threshold) -> dict:
    """A recorded suite's scores and verdict under D5, from its per-case records and its cases
    file; the same scoring as run_suite, with every lost case a failure in each metric."""
    cases = {c["id"]: c for c in (json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip())}
    recorded = suite["cases"]
    metrics = load_metrics(path)
    gated = [r for r in recorded if not r.get("report_set")]
    passed = sum(bool(r["passed"]) for r in gated)
    rate = passed / len(gated) if gated else 0.0
    scores: dict | None = None
    old = suite.get("scores") or {}
    if "hit_at_8" in old and isinstance(threshold, dict):
        pairs = [(cases[r["id"]], r) for r in recorded if r["id"] in cases]
        scores = rag_scores(pairs, judged=old.get("counts", {}).get("ragas", 1) > 0)
        ok = rag_threshold_ok(scores, threshold)
    elif "deterministic" in old and isinstance(threshold, dict):
        scores = guard_scores([(cases[r["id"]], bool(r["passed"]), r.get("checker", "unknown"))
                               for r in recorded if r["id"] in cases])
        ok = guard_threshold_ok(scores, threshold)
    elif metrics is not None and isinstance(threshold, dict):
        outcomes = []
        for r in recorded:
            if lost_of(r) or "metrics" not in r:
                outcomes.append({m: False for m in metric_outcome(metrics, cases[r["id"]], "", {})[0]})
            else:
                outcomes.append(r["metrics"])
        scores = metric_scores(metrics, outcomes)
        ok = all(scores[metric] >= value for metric, value in threshold.items())
    else:
        ok = isinstance(threshold, int | float) and rate >= threshold
    return {"passed": passed, "total": len(gated), "rate": rate, "scores": scores, "ok": ok,
            "lost": lost_by_code(recorded)}


def rescore_main(runs: list[str]) -> int:
    """`run_evals.py --rescore <run> [...]`: each recorded run scored again under D5, old and new."""
    thresholds = yaml.safe_load((REPO / "evals" / "thresholds.yaml").read_text()) or {}
    paths = suites()
    for run in runs:
        record_path = REPO / "build" / "evals" / f"{run.removesuffix('.json')}.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        print(f"{record['run']}")
        out = {}
        for name, suite in record["suites"].items():
            threshold = thresholds.get(name.removesuffix(MCP_SUFFIX))
            new = rescore_suite(name, paths[name], suite, threshold)
            out[name] = new
            old_scores = {k: v for k, v in (suite.get("scores") or {}).items() if isinstance(v, float)}
            new_scores = {k: v for k, v in (new["scores"] or {}).items() if isinstance(v, float)}
            changed = ", ".join(f"{k} {old_scores[k]:.2f}→{new_scores[k]:.2f}" for k in new_scores
                                if k in old_scores and not k.startswith("retrieve") and k != "llm_judge_borderline_or_gulf")
            print(f"  {name}: {'PASS' if suite['ok'] else 'FAIL'} → {'PASS' if new['ok'] else 'FAIL'}; "
                  f"{new['passed']}/{new['total']}; lost {new['lost'] or 0}" + (f"; {changed}" if changed else ""))
        (record_path.parent / f"{record['run']}.rescored.json").write_text(
            json.dumps({"run": record["run"], "rule": "D5 (ADR-0293)", "suites": out}, ensure_ascii=False, indent=2),
            encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
