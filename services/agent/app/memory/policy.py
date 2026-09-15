"""What is worth remembering (README §8, ADR-0058). The extractor's JSON becomes
candidates, kept only for the configured kinds, above the confidence floor, up to
a bounded number per turn. A turn a guard flagged writes nothing. Deduplication
and storage are the writer's."""

import json
import re
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.memory.config import MemoryConfig

_ARRAY = re.compile(r"\[.*\]", re.S)
# Guard actions that let a text through unchanged; anything else flags the turn.
_UNFLAGGED_ACTIONS = frozenset({"allow", "log_only"})


class Candidate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: str
    text: str
    confidence: float = Field(ge=0, le=1)


def parse_candidates(output: str, config: MemoryConfig) -> list[Candidate]:
    """Candidates from the extractor's answer. Anything that is not a JSON array of
    {kind, text, confidence} objects yields none: a malformed answer stores nothing."""
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
        seen.add(text)
        kept.append(candidate.model_copy(update={"text": text}))
    return kept[: config.max_items_per_turn]


def guard_flagged(guard_events: Iterable[dict]) -> bool:
    """README §8: never store anything a guard flagged."""
    return any(event.get("action") not in _UNFLAGGED_ACTIONS for event in guard_events)
