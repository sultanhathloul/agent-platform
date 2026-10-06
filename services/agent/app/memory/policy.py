"""What is worth remembering (README §8, ADR-0058, ADR-0064). The extractor's JSON
becomes candidates, kept only for the configured kinds, above the confidence floor,
naming the user, in the locale's writing scripts, grounded in the user's own
message, up to a bounded number per turn. A turn a guard flagged writes nothing. Deduplication and
storage are the writer's."""

import json
import re
from collections.abc import Collection, Iterable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.memory.config import MemoryConfig
from app.text.arabic import light_stems, normalize
from app.text.script import ScriptPolicy, script_issues

_ARRAY = re.compile(r"\[.*\]", re.S)
_WORD = re.compile(r"\w+")
# Guard actions that let a text through unchanged; anything else flags the turn.
_UNFLAGGED_ACTIONS = frozenset({"allow", "log_only"})


class Candidate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: str
    text: str
    confidence: float = Field(ge=0, le=1)


def grounding(text: str, source: str, ignore: Collection[str] = ()) -> float:
    """The share of the candidate's content words (three letters or more, not in
    `ignore`) that appear in `source`, compared after Arabic normalization with light
    proclitics stripped. 0.0 for a candidate with no content word."""
    haystack = normalize(source)
    ignored = {normalize(word) for word in ignore}
    words = [w for w in _WORD.findall(normalize(text)) if len(w) >= 3 and w not in ignored]
    if not words:
        return 0.0
    return sum(any(stem in haystack for stem in light_stems(w)) for w in words) / len(words)


def parse_candidates(
    output: str, config: MemoryConfig, *, source: str, scripts: ScriptPolicy | None = None
) -> list[Candidate]:
    """Candidates from the extractor's answer about `source`, the user's message.
    Anything that is not a JSON array of {kind, text, confidence} objects yields none:
    a malformed answer stores nothing."""
    match = _ARRAY.search(output or "")
    if not match:
        return []
    try:
        raw = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []
    kept: list[Candidate] = []
    seen: set[str] = set()
    for item in raw:
        try:
            candidate = Candidate.model_validate(item)
        except ValidationError:
            continue
        text = " ".join(candidate.text.split())[: config.max_text_chars]
        if candidate.kind not in config.kinds or candidate.confidence < config.min_confidence:
            continue
        if not text or text in seen:
            continue
        if config.subject_words and not any(normalize(w) in normalize(text) for w in config.subject_words):
            continue  # about the topic, not about the user
        if scripts is not None and script_issues(text, scripts):
            continue
        if grounding(text, source, config.grounding_ignore) < config.min_grounding:
            continue
        seen.add(text)
        kept.append(candidate.model_copy(update={"text": text}))
    return kept[: config.max_items_per_turn]


def guard_flagged(guard_events: Iterable[dict]) -> bool:
    """README §8: never store anything a guard flagged."""
    return any(event.get("action") not in _UNFLAGGED_ACTIONS for event in guard_events)
