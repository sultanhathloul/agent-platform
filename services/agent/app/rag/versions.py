"""The latest version of a source first (phase 08, D3, ADR-0335). A document may supersede an
earlier version of the same source; retrieval prefers the latest. Applied to the fused candidates
before rerank, and again after it, since rerank orders by text alone; idempotent, so applying it
twice changes nothing.

The rule, on candidates best first:
- A chunk of a superseded document whose source has a newer version among the candidates is
  demoted: at most `keep` such chunks stay (the best ones), each placed right after the best chunk
  of its source's latest version present, so the answer sees both and may note the change; the
  rest are dropped.
- A chunk of a superseded document whose newer versions have no chunk among the candidates stays
  where it is: it is the only evidence on that point, and its date and "superseded" label travel
  with it.
- Every other chunk keeps its order.

Capability-agnostic: documents are ids; the caller says which document supersedes which."""

from collections.abc import Callable, Mapping, Sequence
from typing import TypeVar

T = TypeVar("T")


def newer_versions(supersedes: Mapping[str, str | None]) -> dict[str, str]:
    """document → the document that supersedes it, from document → the one it supersedes."""
    return {earlier: later for later, earlier in supersedes.items() if earlier}


def chain_after(document: str, newer: Mapping[str, str]) -> list[str]:
    """The newer versions of `document`, nearest first; stops on a loop."""
    found: list[str] = []
    at = newer.get(document)
    while at is not None and at not in found and at != document:
        found.append(at)
        at = newer.get(at)
    return found


def prefer_latest(
    candidates: Sequence[T], document_of: Callable[[T], str], newer: Mapping[str, str], *, keep: int = 1
) -> list[T]:
    present = {document_of(c) for c in candidates}
    kept: list[T] = []
    demoted: dict[str, list[T]] = {}  # latest present version → its demoted chunks, best first
    budget = max(keep, 0)
    for candidate in candidates:
        document = document_of(candidate)
        newer_present = [d for d in chain_after(document, newer) if d in present]
        if not newer_present:
            kept.append(candidate)
            continue
        if budget > 0:
            budget -= 1
            demoted.setdefault(newer_present[-1], []).append(candidate)
    if not demoted:
        return kept
    ordered: list[T] = []
    placed: set[str] = set()
    for candidate in kept:
        ordered.append(candidate)
        document = document_of(candidate)
        if document in demoted and document not in placed:
            placed.add(document)
            ordered.extend(demoted[document])
    return ordered
