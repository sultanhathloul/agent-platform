# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27"]
# ///
"""Phase 06 measurements (L), n memo requests through the gateway, each in a new conversation:
- as the seed user `evaluator`, who holds the memo skill: the message holds a skill keyword, so
  the route node calls the router model for the skill (ADR-0299), and the answer is written with
  the skill's block;
- the same message as `viewer`, who holds no skill: no candidate, no router call, no skill block.
For each: time to the first token and to the terminal event; and, from each turn's Langfuse
trace, the route node's duration and the router model call inside it (the call the skill adds).
The topics vary per request, so no router decision comes from the cache. Prints p50 and p95.
A provider 429 is retried with the eval harness's backoff and counted. Exits 2 when any turn failed
or a skill turn was not written with the skill.

    uv run scripts/measure_skills.py 20
"""

import json
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

TOPICS = [
    "تدريب الموظفين الجدد على أمن المعلومات", "مواعيد الدوام في شهر رمضان", "صيانة المصاعد في المبنى الرئيسي",
    "تحديث بيانات الموظفين في النظام", "موعد الاجتماع الشهري للإدارات", "ترشيد استهلاك الطاقة في المكاتب",
    "تنظيم مواقف السيارات للزوار", "خطة الإخلاء في حالات الطوارئ", "تسليم تقارير الأداء الربعية",
    "استخدام قاعات الاجتماعات وحجزها", "تجديد عقود النظافة", "المشاركة في معرض التوظيف",
    "إجراءات طلب الإجازات إلكترونيًا", "حضور ورشة العمل عن الجودة", "تزويد الإدارة بأجهزة حاسب جديدة",
    "جدولة النسخ الاحتياطي للبيانات", "تحديث سياسة كلمات المرور", "استقبال وفد هيئة الواحة",
    "توزيع المهام في مشروع الأرشفة", "مواعيد صرف بدل النقل",
]


def percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(share * (len(ordered) - 1))))]


def summary(name: str, values: list[float]) -> str:
    if not values:
        return f"{name}: no samples"
    return (f"{name}: n={len(values)} p50 {percentile(values, 0.5):.2f} s, p95 {percentile(values, 0.95):.2f} s, "
            f"mean {statistics.mean(values):.2f} s")


def turn(http: httpx.Client, gateway: str, token: str, message: str) -> tuple[float | None, float, dict]:
    body = {"conversation_id": str(uuid.uuid4()), "message": message, "locale": "ar"}
    headers = {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}
    started = time.monotonic()
    first, last = None, {}
    with http.stream("POST", f"{gateway}/v1/chat/turns", json=body, headers=headers) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            if event.get("type") == "token" and first is None:
                first = time.monotonic() - started
            last = event
    return first, time.monotonic() - started, last


def _seconds(observation: dict) -> float | None:
    try:
        start = datetime.fromisoformat(observation["startTime"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(observation["endTime"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    return (end - start).total_seconds()


def route_times(http: httpx.Client, cfg: dict, trace_id: str) -> tuple[float | None, float | None]:
    """The route node's duration and the router model call inside it, from the turn's trace."""
    auth = (cfg["LANGFUSE_PUBLIC_KEY"], cfg["LANGFUSE_SECRET_KEY"])
    for _ in range(30):
        response = http.get(f"{cfg['LANGFUSE_PUBLIC_URL']}/api/public/traces/{trace_id}", auth=auth)
        if response.status_code == 200:
            observations = response.json().get("observations") or []
            route = next((o for o in observations if o.get("name") == "node.route" and o.get("endTime")), None)
            if route is not None:
                inner = [o for o in observations if o.get("name") == "model.call" and o.get("endTime")
                         and o.get("parentObservationId") == route["id"]]
                return _seconds(route), (_seconds(inner[0]) if inner else None)
        time.sleep(2)
    return None, None


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    cfg = env()
    gateway = cfg["GATEWAY_PUBLIC_URL"]
    stamp = uuid.uuid4().hex[:4]
    samples = {"skill": {"ttft": [], "total": [], "route": [], "router": []},
               "none": {"ttft": [], "total": [], "route": [], "router": []}}
    failures = retries = 0
    with httpx.Client(timeout=300) as http:
        for i in range(n):
            # A distinct message per request and run: no router decision from the cache.
            message = f"اكتب مذكرة داخلية إلى مديري الإدارات بشأن {TOPICS[i % len(TOPICS)]} (طلب {stamp}-{i})."
            for who, user in (("skill", ("evaluator", "evaluator")), ("none", ("viewer", "viewer"))):
                token = login(*user)["access_token"]
                # A provider 429 ends a turn model_unavailable: retried after 5, 10, 20, 40 and 60 s, as
                # make eval does; only the attempt that completes is timed.
                for delay in (5, 10, 20, 40, 60, None):
                    first, total, last = turn(http, gateway, token, message)
                    code = (last.get("data") or {}).get("code")
                    if last.get("type") == "done" or code != "model_unavailable" or delay is None:
                        break
                    retries += 1
                    time.sleep(delay)
                applied = (last.get("data") or {}).get("state_digest", {}).get("skill")
                if last.get("type") != "done" or (who == "skill") != bool(applied):
                    failures += 1
                    print(f"  {who} turn {i}: {last.get('type')} {last.get('data', {}).get('code')} skill={applied}")
                    continue
                samples[who]["total"].append(total)
                if first is not None:
                    samples[who]["ttft"].append(first)
                route, router = route_times(http, cfg, last["data"]["trace_id"])
                if route is not None:
                    samples[who]["route"].append(route)
                if router is not None:
                    samples[who]["router"].append(router)
    for who, label in (("skill", "with the memo skill (evaluator)"), ("none", "without a skill (viewer)")):
        print(label)
        for key, name in (("ttft", "time to first token"), ("total", "terminal event"), ("route", "route node"),
                          ("router", "router model call")):
            print("  " + summary(name, samples[who][key]))
    print(f"provider retries: {retries}")
    print(f"failures: {failures}")
    return 2 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
