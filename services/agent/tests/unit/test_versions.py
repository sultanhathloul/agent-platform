"""The latest version of a source first (phase 08, D3, ADR-0335)."""

import pytest

from app.rag.versions import chain_after, newer_versions, prefer_latest

# v1 (April) ← v2 (July) ← v3 (October); other is unrelated.
NEWER = newer_versions({"v2": "v1", "v3": "v2", "v1": None, "other": None})


def _ids(chunks):
    return [c[0] for c in chunks]


def _prefer(chunks, keep=1):
    return prefer_latest(chunks, lambda c: c[1], NEWER, keep=keep)


def test_newer_versions_and_the_chain_after_a_document():
    assert NEWER == {"v1": "v2", "v2": "v3"}
    assert chain_after("v1", NEWER) == ["v2", "v3"] and chain_after("v3", NEWER) == []
    assert chain_after("a", {"a": "b", "b": "a"}) == ["b"]  # a loop stops


def test_a_superseded_chunk_sits_right_after_its_latest_version_and_only_keep_of_them_stay():
    chunks = [("c1", "v1"), ("c2", "other"), ("c3", "v3"), ("c4", "v1"), ("c5", "v2"), ("c6", "v3")]
    assert _ids(_prefer(chunks, keep=1)) == ["c2", "c3", "c1", "c6"]
    assert _ids(_prefer(chunks, keep=2)) == ["c2", "c3", "c1", "c4", "c6"]
    assert _ids(_prefer(chunks, keep=0)) == ["c2", "c3", "c6"]


def test_a_superseded_chunk_without_its_newer_version_present_stays_in_place():
    chunks = [("c1", "v1"), ("c2", "other"), ("c4", "v1")]
    assert _ids(_prefer(chunks)) == ["c1", "c2", "c4"]
    # v2 present but not v3: v1 goes after v2's best chunk; v2 itself stays (v3 is absent).
    chunks = [("c1", "v1"), ("c5", "v2"), ("c2", "other")]
    assert _ids(_prefer(chunks)) == ["c5", "c1", "c2"]


@pytest.mark.parametrize("keep", [0, 1, 3])
def test_applying_it_twice_changes_nothing(keep):
    chunks = [("c1", "v1"), ("c2", "other"), ("c3", "v3"), ("c4", "v1"), ("c5", "v2"), ("c6", "v3")]
    once = _prefer(chunks, keep)
    assert _prefer(once, keep) == once


def test_no_versions_keeps_the_order():
    chunks = [("a", "x"), ("b", "y"), ("c", "x")]
    assert prefer_latest(chunks, lambda c: c[1], {}) == chunks
