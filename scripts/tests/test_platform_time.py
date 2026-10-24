"""Smoke's platform-side time (D6, ADR-0294): wall time minus the union of the turn's generation
intervals inside its window, on the trace's own clock."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import platform_time  # noqa: E402


def _obs(name: str, start: float, end: float) -> dict:
    stamp = lambda t: f"2026-10-24T10:00:{t:06.3f}Z"  # noqa: E731
    return {"name": name, "startTime": stamp(start), "endTime": stamp(end)}


def test_overlapping_generations_count_once():
    assert platform_time.union_seconds([(1, 3), (2, 4), (6, 7)], 0, 10) == 4


def test_a_generation_outside_the_window_counts_only_inside_it():
    assert platform_time.union_seconds([(8, 12)], 0, 10) == 2
    assert platform_time.union_seconds([(11, 12)], 0, 10) == 0


def test_platform_time_is_wall_minus_generation_time():
    trace = {"observations": [_obs("turn", 1, 9), _obs("model.call", 2, 4), _obs("model.call", 3, 6),
                              _obs("context.assemble", 1.5, 1.6), _obs("model.call", 20, 25)]}
    seconds, count = platform_time.platform_seconds(trace, wall=8.5)
    assert count == 3 and abs(seconds - 4.5) < 1e-6  # 8.5 − (2→6); the memory call after the stream is outside


def test_a_platform_stall_is_not_hidden_by_a_fast_model():
    trace = {"observations": [_obs("turn", 0, 15), _obs("model.call", 13, 14)]}
    assert platform_time.platform_seconds(trace, wall=15)[0] == 14
