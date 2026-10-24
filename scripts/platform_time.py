"""A turn's platform-side time (phase 06, D6, ADR-0294): its wall time minus the union of its
model generation intervals, read from the turn's Langfuse trace. Smoke's G7 checks bound this
number, so provider slowness neither fails them nor hides a platform stall.

    python3 scripts/platform_time.py <trace id> <wall seconds>   # prints the platform seconds

The generations are the trace's `model.call` observations (Langfuse type GENERATION). The window
is the trace's own clock: from the turn's root span start, `wall` seconds long, so no clock is
compared with the host's; a generation outside the window (the memory writer after the stream)
counts only for the part inside it. Standard library only: smoke runs it with python3."""

import base64
import json
import os
import sys
import time
import urllib.request
from datetime import datetime


def _ts(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def union_seconds(intervals: list[tuple[float, float]], start: float, end: float) -> float:
    """Total length of the union of `intervals`, each clipped to [start, end]."""
    clipped = sorted((max(a, start), min(b, end)) for a, b in intervals if b > start and a < end)
    total, current_a, current_b = 0.0, None, None
    for a, b in clipped:
        if current_b is None or a > current_b:
            if current_b is not None:
                total += current_b - current_a
            current_a, current_b = a, b
        else:
            current_b = max(current_b, b)
    if current_b is not None:
        total += current_b - current_a
    return total


def platform_seconds(trace: dict, wall: float) -> tuple[float, int]:
    """(wall − generation time inside the turn's window, number of generations)."""
    observations = trace.get("observations") or []
    roots = [o for o in observations if o.get("name") == "turn" and o.get("startTime")]
    starts = [_ts(o["startTime"]) for o in (roots or observations) if o.get("startTime")]
    if not starts:
        raise ValueError("the trace has no timed observation")
    start = min(starts)
    generations = [
        (_ts(o["startTime"]), _ts(o["endTime"]))
        for o in observations
        if o.get("name") == "model.call" and o.get("startTime") and o.get("endTime")
    ]
    generated = union_seconds(generations, start, start + wall)
    return max(0.0, wall - generated), len(generations)


def fetch(trace_id: str, attempts: int = 30) -> dict:
    """The trace from Langfuse once every model call in it has ended (bounded: 2 s × attempts)."""
    url = f"{os.environ['LANGFUSE_PUBLIC_URL']}/api/public/traces/{trace_id}"
    auth = base64.b64encode(f"{os.environ['LANGFUSE_PUBLIC_KEY']}:{os.environ['LANGFUSE_SECRET_KEY']}".encode()).decode()
    last: dict = {}
    for _ in range(attempts):
        try:
            request = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}"})
            with urllib.request.urlopen(request, timeout=5) as response:
                last = json.loads(response.read())
            calls = [o for o in last.get("observations") or [] if o.get("name") == "model.call"]
            if calls and all(o.get("endTime") for o in calls) and any(o.get("name") == "turn" for o in last["observations"]):
                return last
        except (OSError, ValueError):
            pass
        time.sleep(2)
    if not last:
        raise SystemExit(f"trace {trace_id} not in Langfuse")
    return last


if __name__ == "__main__":
    seconds, count = platform_seconds(fetch(sys.argv[1]), float(sys.argv[2]))
    print(f"{seconds:.2f} {count}")
