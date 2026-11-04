"""files (my-files PR, owner decision 2; ADR-0443, ADR-0446): answers from a file the user uploaded in an
earlier
conversation, only when the message refers to one.

No model call, like docs: the broker calls decide what the answer reads.
1. The message must refer to an earlier file (config/files.yaml `refer`, ADR-0449): an upload or a send word
   together with a file word or an earlier-conversation word; one of them alone never does. Otherwise the capability calls no tool and returns nothing, whatever the router
   chose, so an earlier file is never used unless the user's message refers to it (decision 2's negative).
2. `file_search` with the message, less its reference and conversational words (ADR-0447), over the caller's own indexed files of every conversation.
3. `file_read` of the best file when the message names pages («الصفحة 3») or asks for the whole file
   («لخّصه»), its chunks in page order, beside what the search found.

Like docs, the capability returns its passages as source references (the phase 04 evidence and citation path,
S4) and findings that are notes only: which files they come from, with their conversation's date, and the
citation the answer gives, «من ملفك: <title> (محادثة <date>)». On a report turn it returns nothing: a report
draws on one kind of source (ADR-0398), and the router chose this capability for a text answer."""

import re
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from app.capabilities.contract import CapabilityInput, CapabilityOutput, RuntimeContext, Source
from app.models.prompts import PromptLibrary
from app.text.arabic import keyword_stems, normalize

PROMPTS = PromptLibrary.load(Path(__file__).resolve().parent / "prompts")

_WORD = re.compile(r"\w+")
# «الصفحة 3»، «صفحة ٣ و٤»، «ص 12»، "page 3", "pages 2 and 5", "p. 4".
_PAGES = re.compile(
    r"(?:صفح(?:ه|ة|ات)|ص\.?|pages?|pp?\.)\s*((?:\d{1,4}\s*(?:,|،|و|and|-|–|to|الى|إلى)?\s*){1,10})",
    re.IGNORECASE,
)


class _State(TypedDict, total=False):
    input: CapabilityInput
    output: CapabilityOutput


def _words(config: dict, key: str) -> list[str]:
    entry = config.get(key) or {}
    return [*(entry.get("ar") or []), *(entry.get("en") or [])]


def has_any(message: str, words: list[str]) -> bool:
    """A word of `words` in the message as a whole word (an Arabic prefix و، ف، ب، ل، ال stripped), or a
    phrase of several words as a run of whole words; both Arabic-normalized. "sent" is not in "present"."""
    text = f" {' '.join(_WORD.findall(normalize(message)))} "
    stems = {stem for word in text.split() for stem in keyword_stems(word, min_length=2)}
    for word in words:
        needle = " ".join(_WORD.findall(normalize(word)))
        if not needle:
            continue
        if " " in needle:
            if f" {needle} " in text:
                return True
        elif needle in stems:
            return True
    return False


def refers_to_earlier_file(message: str, config: dict) -> bool:
    """Decision 2: the message refers to a file the user sent before (ADR-0449): an upload or a send word
    together with a file word or an earlier-conversation word. An upload or a send word alone never does
    («رفعت سعر المنتج», «حملت التطبيق أمس»)."""
    refer = config.get("refer") or {}
    sent = has_any(message, [*_words(refer, "upload_words"), *_words(refer, "send_words")])
    if not sent:
        return False
    return has_any(
        message,
        [*_words(refer, "file_words"), *_words(refer, "subject_file_words"), *_words(refer, "earlier_words")],
    )


def search_query(message: str, config: dict) -> str:
    """The message without the words that only refer to the earlier file and without conversational words
    (config/files.yaml `query_stopwords`), Arabic-normalized; the whole message when nothing would be left."""
    refer = config.get("refer") or {}
    phrases = [
        " ".join(_WORD.findall(normalize(w)))
        for key in ("upload_words", "send_words", "file_words", "earlier_words")
        for w in _words(refer, key)
    ]
    text = f" {' '.join(_WORD.findall(normalize(message)))} "
    for phrase in sorted((p for p in phrases if " " in p), key=len, reverse=True):
        text = text.replace(f" {phrase} ", " ")
    drop = {p for p in phrases if " " not in p} | {
        " ".join(_WORD.findall(normalize(w))) for w in _words(config, "query_stopwords")
    }
    kept = [w for w in text.split() if not (keyword_stems(w, min_length=2) & drop)]
    return " ".join(kept) or message


def named_pages(message: str) -> list[int]:
    found: list[int] = []
    for match in _PAGES.finditer(normalize(message)):
        numbers = [int(n) for n in re.findall(r"\d{1,4}", match.group(1))]
        span = re.search(r"(\d{1,4})\s*(?:-|–|to|الى|إلى)\s*(\d{1,4})", match.group(1))
        if span and 0 < int(span.group(2)) - int(span.group(1)) <= 10:
            numbers += list(range(int(span.group(1)), int(span.group(2)) + 1))
        found += [n for n in numbers if 0 < n < 10000]
    return sorted(set(found))[:10]


def _sources(raw: list[dict]) -> list[Source]:
    return [
        Source(
            id=hit["id"],
            ref=hit["id"],
            title=hit.get("title") or "",
            kind="doc",
            locator=hit.get("locator"),
            page=hit.get("page"),
            document_id=hit["attachment_id"],
            score=hit.get("score"),
            backend=hit.get("backend"),
            collection=hit.get("collection"),
        )
        for hit in raw
    ]


async def _run(state: _State, runtime: Runtime[RuntimeContext]) -> dict:
    rt = runtime.context
    request = state["input"]
    runs = int(rt.cap_state.get("searches", 0))
    config = rt.config or {}
    if request.output == "report" or not refers_to_earlier_file(request.message, config):
        # Decision 2: no earlier file is used unless the message refers to one; no tool runs.
        return {"output": CapabilityOutput(findings="", cap_state={"searches": runs})}
    result = await rt.broker.execute(
        "file_search", {"query": search_query(request.message, config)[:8000], "k": int(config.get("top_k", 8))}
    )
    failed = result.status != "ok"
    found = [] if failed else list(result.sources)
    files: dict[str, dict] = (
        {} if failed else {f["file_id"]: f for f in result.data or [] if isinstance(f, dict)}
    )
    pages = named_pages(request.message)
    whole = any(has_any(request.message, [w]) for w in _words(config.get("refer") or {}, "whole_words"))
    if files and (pages or whole):
        best = found[0]["attachment_id"] if found else next(iter(files))
        read = await rt.broker.execute("file_read", {"file_id": best, **({"pages": pages} if pages else {})})
        if read.status == "ok":
            seen = {s["id"] for s in found}
            found = [s for s in read.sources if s["id"] not in seen] + found
            if isinstance(read.data, dict):
                files.setdefault(read.data["file_id"], read.data)
        else:
            failed = True
    sources = _sources(found)
    used = [files[i] for i in dict.fromkeys(s.document_id for s in sources) if i in files]
    findings = PROMPTS.render(
        "files.findings", request.locale, files=used, found=bool(sources), failed=failed
    ).text
    return {"output": CapabilityOutput(findings=findings, sources=sources, cap_state={"searches": runs + 1})}


def build() -> StateGraph:
    # Returns the uncompiled StateGraph; the core compiles it (ADR-0053).
    graph = StateGraph(_State, context_schema=RuntimeContext)
    graph.add_node("run", _run)
    graph.add_edge(START, "run")
    graph.add_edge("run", END)
    return graph
