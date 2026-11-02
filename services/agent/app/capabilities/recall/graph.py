"""recall (report library PR, owner decisions 2, 3 and 5; ADR-0431 to ADR-0434): answers questions about the
user's reports and about what was said earlier in this conversation.

No model call, like kb: three broker calls decide what the answer reads.
1. `report_search` in this conversation, then in the caller's whole library, with the message as the query
   (conversational words removed by the store's stopwords).
2. `report_read` of the report the question is about: the best match in this conversation, else the best
   match of another conversation of the caller's (which the next report turn here may make a new version
   of, ADR-0433), else this conversation's latest report; at most config/recall.yaml `max_reads`.
3. `conversation_recall` with the message: earlier messages of this conversation, verbatim.

The findings carry what was read inside delimiters (a report's text and an earlier message are data, not
instructions), the list of the reports found, and how to answer: in text, citing «من التقرير: <title>
(الإصدار n)», never as a report page or a claim that one was made. On a report turn the capability returns
nothing (a report draws on one kind of source, ADR-0398): the router chose it only for a text answer."""

from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from app.capabilities.contract import CapabilityInput, CapabilityOutput, RuntimeContext
from app.models.prompts import PromptLibrary
from app.text.arabic import contains_keyword

PROMPTS = PromptLibrary.load(Path(__file__).resolve().parent / "prompts")


class _State(TypedDict, total=False):
    input: CapabilityInput
    output: CapabilityOutput


async def _search(rt: RuntimeContext, query: str, scope: str) -> list[dict]:
    result = await rt.broker.execute("report_search", {"query": query[:2000], "scope": scope, "k": 5})
    return list(result.data or []) if result.status == "ok" else []


def _choose(conversation: list[dict], library: list[dict], latest: list[dict], reads: int) -> list[dict]:
    chosen: list[dict] = []
    for pool in ([h for h in conversation if h.get("matched")],
                 [h for h in library if h.get("matched") and not h.get("this_conversation")],
                 latest[:1]):
        for hit in pool:
            if len(chosen) >= reads:
                return chosen
            if all(c["report_id"] != hit["report_id"] for c in chosen):
                chosen.append(hit)
        if chosen:
            return chosen
    return chosen


async def _run(state: _State, runtime: Runtime[RuntimeContext]) -> dict:
    rt = runtime.context
    request = state["input"]
    runs = int(rt.cap_state.get("runs", 0)) + 1
    if request.output == "report":
        return {"output": CapabilityOutput(findings="", cap_state={"runs": runs})}
    config = rt.config or {}
    reads = int(config.get("max_reads", 1))
    message = request.message
    conversation = await _search(rt, message, "conversation")
    library = await _search(rt, message, "library")
    # This conversation's latest report is read only for a message that names a report («اللوحة», «التقرير»)
    # and matched none by its words: a question about a pasted table reads no report.
    names_report = any(contains_keyword(message, w) for w in config.get("report_words") or [])
    latest = []
    if names_report and not any(h.get("matched") for h in conversation):
        latest = await _search(rt, "", "conversation")
    read: list[dict] = []
    failed = False
    for hit in _choose(conversation, library, latest, reads):
        result = await rt.broker.execute(
            "report_read", {"report_id": hit["report_id"], "locale": "en" if request.locale == "en" else "ar"}
        )
        if result.status == "ok" and isinstance(result.data, dict):
            read.append({**result.data, "text": result.content})
        else:
            failed = True
    recalled = await rt.broker.execute("conversation_recall", {"query": message[:2000]})
    messages = list(recalled.data or []) if recalled.status == "ok" else []
    listed = {h["report_id"]: h for h in [*latest, *conversation, *library]}
    findings = PROMPTS.render(
        "recall.findings",
        request.locale,
        reports=read,
        listed=[h for h in listed.values() if all(r["report_id"] != h["report_id"] for r in read)][:8],
        messages=messages,
        failed=failed or recalled.status != "ok",
    ).text
    return {"output": CapabilityOutput(findings=findings, cap_state={"runs": runs})}


def build() -> StateGraph:
    # Returns the uncompiled StateGraph; the core compiles it (ADR-0053).
    graph = StateGraph(_State, context_schema=RuntimeContext)
    graph.add_node("run", _run)
    graph.add_edge(START, "run")
    graph.add_edge("run", END)
    return graph
