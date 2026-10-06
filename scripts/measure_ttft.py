# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27"]
# ///
"""Time to first token through the gateway (ADR-0039, phase 03 C2).

Runs N general turns as the eval user (memory off, ADR-0074), each in a new
conversation, and measures from sending the request to the first `token` event,
and to the terminal event. Prints p50 and p95 and writes one JSON line per turn.

    uv run scripts/measure_ttft.py <label> [turns] [out.jsonl] [--long]

The label names the mode being measured (e.g. phase02, streaming, buffered); which
mode the agent runs in is decided by the enabled policies, not by this script.
`--long` asks for five-sentence answers instead of one sentence, where streaming
and buffered mode differ most.
"""

import json
import statistics
import sys
import time
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from get_token import env, login  # noqa: E402

QUESTIONS = [
    "ما عاصمة المملكة العربية السعودية؟ أجب بجملة واحدة.",
    "ما أكبر محيط في العالم؟ أجب بجملة واحدة.",
    "كم عدد أيام الأسبوع؟ أجب بجملة واحدة.",
    "ما عاصمة مصر؟ أجب بجملة واحدة.",
    "ما لون علم المملكة العربية السعودية؟ أجب بجملة واحدة.",
]


LONG_QUESTIONS = [
    "اكتب خمس جمل عن تاريخ مدينة الرياض.",
    "اكتب خمس جمل عن أهمية التعليم.",
    "اكتب خمس جمل عن المحيط الهادئ.",
    "اكتب خمس جمل عن النخلة في الثقافة العربية.",
    "اكتب خمس جمل عن فوائد القراءة.",
]


def percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(share * (len(ordered) - 1))))
    return ordered[index]


def turn(http: httpx.Client, gateway: str, token: str, message: str) -> dict:
    body = {"conversation_id": str(uuid.uuid4()), "message": message, "locale": "ar"}
    headers = {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}
    started = time.monotonic()
    first = end = None
    kind = "none"
    tokens = 0
    with http.stream("POST", f"{gateway}/v1/chat/turns", json=body, headers=headers) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            if event["type"] == "token":
                tokens += 1
                if first is None:
                    first = time.monotonic()
            if event["type"] in ("done", "error"):
                end = time.monotonic()
                kind = event["type"] if event["type"] == "done" else event["data"]["code"]
    return {
        "ttft_s": None if first is None else round(first - started, 3),
        "total_s": None if end is None else round(end - started, 3),
        "token_events": tokens,
        "outcome": kind,
    }


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--long"]
    questions = LONG_QUESTIONS if "--long" in sys.argv else QUESTIONS
    label = args[0]
    count = int(args[1]) if len(args) > 1 else 20
    out = Path(args[2]) if len(args) > 2 else None
    cfg = env()
    gateway = cfg["GATEWAY_PUBLIC_URL"]
    rows = []
    with httpx.Client(timeout=300) as http:
        for i in range(count):
            token = login("evaluator", "evaluator")["access_token"]
            row = {"label": label, "i": i, **turn(http, gateway, token, questions[i % len(questions)])}
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    if out:
        with out.open("a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    ttft = [r["ttft_s"] for r in rows if r["ttft_s"] is not None]
    total = [r["total_s"] for r in rows if r["total_s"] is not None]
    if not ttft:
        print(f"{label}: no turn streamed a token")
        return 1
    print(
        f"{label}: n={len(ttft)}/{count} ttft p50={percentile(ttft, 0.5):.2f}s p95={percentile(ttft, 0.95):.2f}s "
        f"mean={statistics.mean(ttft):.2f}s; total p50={percentile(total, 0.5):.2f}s p95={percentile(total, 0.95):.2f}s"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
