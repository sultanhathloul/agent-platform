# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27"]
# ///
"""Phase 05 measurements (G3), n directions turns as the seed user `evaluator`, through the gateway:
- time to the first token, and to the terminal event, of a directions answer (router model,
  extract call, three MCP calls through the broker, answer);
- each MCP tool's latency as the broker reports it in `tool_end` (PRE_TOOL, the call over
  Streamable HTTP to the reference routing server, POST_TOOL); the reference server's own work is
  a lookup in a fixed graph, so this is the platform's overhead per call.
Prints p50 and p95 of each. Exits 2 when any turn failed. Docs measurements on each backend are
scripts/measure_docs.py, run once per DOCS_BACKEND.

    uv run scripts/measure_mcp.py 20
"""

import statistics
import sys
import time
import uuid
from collections import defaultdict
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from get_token import env, login  # noqa: E402

PAIRS = [
    ("برج المملكة", "بوليفارد رياض سيتي"),
    ("مطار الملك خالد الدولي", "قصر المصمك"),
    ("مركز الملك عبدالله المالي", "الدرعية"),
    ("استاد الملك فهد الدولي", "غرناطة مول"),
    ("جامعة الأميرة نورة", "واجهة الرياض"),
]


def percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(share * (len(ordered) - 1))))
    return ordered[index]


def summary(name: str, values: list[float], unit: str = "s") -> str:
    if not values:
        return f"{name}: no samples"
    return (f"{name}: n={len(values)} p50 {percentile(values, 0.5):.2f} {unit}, "
            f"p95 {percentile(values, 0.95):.2f} {unit}, mean {statistics.mean(values):.2f} {unit}")


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    cfg = env()
    gateway = cfg["GATEWAY_PUBLIC_URL"]
    ttft, total = [], []
    tools: dict[str, list[float]] = defaultdict(list)
    failures = 0
    with httpx.Client(timeout=300) as http:
        for i in range(n):
            token = login("evaluator", "evaluator")["access_token"]
            origin, destination = PAIRS[i % len(PAIRS)]
            body = {"conversation_id": str(uuid.uuid4()), "message": f"كم المسافة من {origin} إلى {destination} بالسيارة؟",
                    "locale": "ar"}
            first = last = None
            latencies: dict[str, list[float]] = defaultdict(list)
            sent = time.monotonic()
            with http.stream("POST", f"{gateway}/v1/chat/turns", json=body,
                             headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}) as stream:
                for line in stream.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    event = httpx.Response(200, content=line[6:]).json()
                    if event["type"] == "token" and first is None:
                        first = time.monotonic() - sent
                    if event["type"] == "tool_end" and event["data"]["status"] == "ok":
                        latencies[event["data"]["tool"]].append(event["data"]["latency_ms"])
                    if event["type"] in ("done", "error"):
                        last = event
            capabilities = (last or {}).get("data", {}).get("state_digest", {}).get("capabilities")
            if not last or last["type"] != "done" or first is None or capabilities != ["directions"]:
                failures += 1
                print(f"  turn {i}: {last and (last['data'].get('code') or capabilities)}")
                continue
            ttft.append(first)
            total.append(time.monotonic() - sent)
            for tool, values in latencies.items():
                tools[tool].extend(values)
            print(f"  {i + 1}/{n}: ttft {first:.2f} s, tools {dict(latencies)}")
    print(summary("directions answer, time to first token", ttft))
    print(summary("directions answer, time to the terminal event", total))
    for tool, values in sorted(tools.items()):
        print(summary(f"{tool} through the broker (tool_end latency)", values, "ms"))
    return 2 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
