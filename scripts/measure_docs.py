# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27"]
# ///
"""Phase 04 measurements (E3), n turns each, as the seed user `evaluator`:
- time to indexed of a 20-page Arabic PDF (evals/docs/twenty-pages.pdf), upload to terminal status;
- retrieval plus rerank latency: the `rag.retrieve` span of each docs turn (embedding, two
  searches, fusion, rerank), read from Langfuse;
- time to first token, and to the terminal event, of a docs answer on that document.
Prints p50 and p95 of each. Exits 2 when any upload or turn failed.

    uv run scripts/measure_docs.py 20
"""

import statistics
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from get_token import env, login  # noqa: E402

QUESTIONS = [
    "ما الذي يلتزم به الموظف في الإدارة رقم {n}؟",
    "كل كم أسبوعا يرفع الموظف تقريره في المادة {m}؟",
    "ماذا تنص المادة {m} من الفصل {n}؟",
]


def percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(share * (len(ordered) - 1))))
    return ordered[index]


def summary(name: str, values: list[float]) -> str:
    if not values:
        return f"{name}: no samples"
    return (f"{name}: n={len(values)} p50 {percentile(values, 0.5):.2f} s, p95 {percentile(values, 0.95):.2f} s, "
            f"mean {statistics.mean(values):.2f} s")


def span_seconds(http: httpx.Client, cfg: dict, trace_id: str, name: str) -> float | None:
    auth = (cfg["LANGFUSE_PUBLIC_KEY"], cfg["LANGFUSE_SECRET_KEY"])
    for _ in range(45):
        response = http.get(f"{cfg['LANGFUSE_PUBLIC_URL']}/api/public/traces/{trace_id}", auth=auth)
        if response.status_code == 200:
            for o in response.json().get("observations", []):
                if o.get("name") == name and o.get("endTime"):
                    start = datetime.fromisoformat(o["startTime"].replace("Z", "+00:00"))
                    end = datetime.fromisoformat(o["endTime"].replace("Z", "+00:00"))
                    return (end - start).total_seconds()
        time.sleep(2)
    return None


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    cfg = env()
    gateway = cfg["GATEWAY_PUBLIC_URL"]
    indexed, retrieval, ttft, total = [], [], [], []
    failures = 0
    with httpx.Client(timeout=300) as http:
        for i in range(n):
            token = login("evaluator", "evaluator")["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            conversation = str(uuid.uuid4())
            started = time.monotonic()
            with (REPO / "evals/docs/twenty-pages.pdf").open("rb") as handle:
                response = http.post(f"{gateway}/v1/conversations/{conversation}/attachments",
                                     files={"file": ("twenty-pages.pdf", handle)}, headers=headers)
            if response.status_code != 201:
                failures += 1
                print(f"  upload {i}: HTTP {response.status_code}")
                continue
            attachment = response.json()["data"]["id"]
            status = "pending"
            while status == "pending" and time.monotonic() - started < 300:
                time.sleep(0.25)
                status = http.get(f"{gateway}/v1/conversations/{conversation}/attachments/{attachment}",
                                  headers=headers).json()["data"]["status"]
            if status != "indexed":
                failures += 1
                print(f"  upload {i}: {status}")
                continue
            indexed.append(time.monotonic() - started)
            message = QUESTIONS[i % len(QUESTIONS)].format(n=i % 20 + 1, m=(i % 20 + 1) * 10 + i % 30 + 1)
            body = {"conversation_id": conversation, "message": message, "attachment_ids": [attachment],
                    "locale": "ar"}
            first = last = None
            sent = time.monotonic()
            with http.stream("POST", f"{gateway}/v1/chat/turns", json=body,
                             headers={**headers, "Accept": "text/event-stream"}) as stream:
                for line in stream.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    event = httpx.Response(200, content=line[6:]).json()
                    if event["type"] == "token" and first is None:
                        first = time.monotonic() - sent
                    if event["type"] in ("done", "error"):
                        last = event
            if not last or last["type"] != "done" or first is None:
                failures += 1
                print(f"  turn {i}: {last and last['data'].get('code')}")
                continue
            ttft.append(first)
            total.append(time.monotonic() - sent)
            seconds = span_seconds(http, cfg, last["data"]["trace_id"], "rag.retrieve")
            if seconds is not None:
                retrieval.append(seconds)
            print(f"  {i + 1}/{n}: indexed {indexed[-1]:.2f} s, ttft {first:.2f} s, retrieve {seconds}")
    print(summary("time to indexed (20-page Arabic PDF)", indexed))
    print(summary("retrieval + rerank (rag.retrieve span)", retrieval))
    print(summary("docs answer, time to first token", ttft))
    print(summary("docs answer, time to the terminal event", total))
    return 2 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
