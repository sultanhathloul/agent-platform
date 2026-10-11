"""Recursive chunking (README §9.3, ADR-0135): at most `max_tokens` per chunk with
`overlap_tokens` carried from the end of the previous chunk, never across a segment
(one PDF page, one section, one sheet), so each chunk has one page or section. Text is
split on the coarsest separator that works: paragraphs, lines, sentence ends (. ؟ ? !
؛), Arabic and Latin commas, then spaces; a run longer than the budget with no
separator is cut by tokens. A segment's `repeat` line (an XLSX header) opens every
chunk cut from it and counts against the budget. Tokens are counted as Context
Assembly counts them."""

from collections.abc import Sequence

from app.context.compress import truncate
from app.context.tokens import TokenCounter
from app.rag.types import Chunk, Segment

SEPARATORS = ("\n\n", "\n", ". ", "؟ ", "? ", "! ", "؛ ", "، ", ", ", " ")


def _units(text: str, limit: int, counter: TokenCounter, separators: Sequence[str] = SEPARATORS) -> list[str]:
    """Pieces of `text`, each within `limit` tokens, that concatenate back to `text`."""
    if counter.count(text) <= limit:
        return [text]
    for index, separator in enumerate(separators):
        if separator not in text:
            continue
        parts = text.split(separator)
        pieces = [part + separator for part in parts[:-1]] + [parts[-1]]
        units: list[str] = []
        for piece in pieces:
            if piece:
                units.extend(_units(piece, limit, counter, separators[index + 1 :]))
        return units
    units = []
    while text:
        head = truncate(text, limit, counter) or text[:1]
        units.append(head)
        text = text[len(head) :]
    return units


def chunk_segments(
    segments: Sequence[Segment], counter: TokenCounter, *, max_tokens: int, overlap_tokens: int
) -> list[Chunk]:
    chunks: list[Chunk] = []
    for segment in segments:
        header = (segment.repeat.strip() + "\n") if segment.repeat else ""
        budget = max(max_tokens - counter.count(header), 1)
        overlap = min(overlap_tokens, budget // 2)
        units = _units(segment.text, budget, counter)
        current: list[str] = []
        size = 0
        for unit in units:
            cost = counter.count(unit)
            if current and size + cost > budget:
                chunks.append(_chunk(len(chunks), header + "".join(current), segment, counter))
                carried: list[str] = []
                carried_size = 0
                for previous in reversed(current):
                    previous_cost = counter.count(previous)
                    if carried_size + previous_cost > overlap:
                        break
                    carried.insert(0, previous)
                    carried_size += previous_cost
                current, size = carried, carried_size
            current.append(unit)
            size += cost
        if current and "".join(current).strip():
            chunks.append(_chunk(len(chunks), header + "".join(current), segment, counter))
    return chunks


def _chunk(ord_: int, text: str, segment: Segment, counter: TokenCounter) -> Chunk:
    text = text.strip()
    return Chunk(ord=ord_, text=text, page=segment.page, section=segment.section, tokens=counter.count(text))
