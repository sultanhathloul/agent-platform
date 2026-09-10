# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27", "pyyaml>=6.0"]
# ///
"""Offline evaluation (README §12) against the live stack, through the
gateway, with real models.

Suites are evals/golden/*.jsonl plus services/agent/app/capabilities/*/evals/*.jsonl
(README §18.4); the suite name is the file stem. Every suite needs a threshold
in evals/thresholds.yaml, and a suite without one fails the run. Each case
result is pushed to Langfuse as an `eval_pass` score on the turn's trace.

Case checks so far: contains_any (answer contains one of the strings, compared
after Arabic normalization), excludes_any (answer contains none of them; catches
a right keyword inside a wrong answer) and script (arabic | latin: the answer
is mostly in that script). Suites for later phases add their own checks.
"""

import json
import re
import sys
import unicodedata
import uuid
from pathlib import Path

import httpx
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from get_token import env, login  # noqa: E402

EVAL_USER = ("analyst", "analyst")
_FOLD = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه", **{a: str(i) for i, a in enumerate("٠١٢٣٤٥٦٧٨٩")}})
_DIACRITICS = re.compile(r"[ً-ٰٟ]")


def fold(text: str) -> str:
    return _DIACRITICS.sub("", unicodedata.normalize("NFKC", text).replace("ـ", "")).translate(_FOLD).casefold()


def script_share(text: str, script: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    arabic = sum("؀" <= c <= "ۿ" for c in letters)
    return (arabic if script == "arabic" else len(letters) - arabic) / len(letters)


def check(case: dict, answer: str) -> list[str]:
    expect, failures = case.get("expect", {}), []
    if "contains_any" in expect and not any(fold(s) in fold(answer) for s in expect["contains_any"]):
        failures.append(f"none of {expect['contains_any']}")
    wrong = [s for s in expect.get("excludes_any", []) if fold(s) in fold(answer)]
    if wrong:
        failures.append(f"contains {wrong}")
    if "script" in expect and script_share(answer, expect["script"]) < 0.6:
        failures.append(f"not mostly {expect['script']}")
    return failures


def run_turn(http: httpx.Client, gateway: str, token: str, case: dict) -> dict:
    body = {"conversation_id": str(uuid.uuid4()), "message": case["message"], "locale": case.get("locale", "ar")}
    headers = {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}
    last: dict = {}
    with http.stream("POST", f"{gateway}/v1/chat/turns", json=body, headers=headers) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if line.startswith("data: "):
                last = json.loads(line[6:])
    return last


def suites() -> dict[str, Path]:
    files = sorted((REPO / "evals" / "golden").glob("*.jsonl"))
    files += sorted((REPO / "services" / "agent" / "app" / "capabilities").glob("*/evals/*.jsonl"))
    return {path.stem: path for path in files}


def main() -> int:
    cfg = env()
    thresholds = yaml.safe_load((REPO / "evals" / "thresholds.yaml").read_text()) or {}
    found = suites()
    if not found:
        print("no eval suites found")
        return 1
    token = login(*EVAL_USER)["access_token"]
    auth = (cfg["LANGFUSE_PUBLIC_KEY"], cfg["LANGFUSE_SECRET_KEY"])
    rows, failed = [], False

    with httpx.Client(timeout=300) as http:
        for name, path in found.items():
            cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            passed = 0
            for case in cases:
                event = run_turn(http, cfg["GATEWAY_PUBLIC_URL"], token, case)
                if event.get("type") == "done":
                    answer = event["data"]["answer"]
                    problems = check(case, answer)
                    trace_id = event["data"].get("trace_id")
                else:
                    answer, problems, trace_id = "", [f"no done event ({event.get('data', {}).get('code')})"], None
                passed += not problems
                mark = "pass" if not problems else "FAIL"
                print(f"  {mark}  {name}/{case['id']}  {answer[:80]!r}  {'; '.join(problems)}")
                if trace_id:
                    http.post(
                        f"{cfg['LANGFUSE_PUBLIC_URL']}/api/public/scores",
                        auth=auth,
                        json={"traceId": trace_id, "name": "eval_pass", "value": 0 if problems else 1,
                              "dataType": "NUMERIC", "comment": f"{name}/{case['id']}"},
                    )
            rate = passed / len(cases)
            threshold = thresholds.get(name)
            ok = threshold is not None and rate >= threshold
            failed |= not ok
            rows.append((name, passed, len(cases), rate, threshold, ok))

    print(f"\n{'suite':<14}{'passed':>9}{'rate':>8}{'threshold':>11}  result")
    for name, passed, total, rate, threshold, ok in rows:
        shown = "missing" if threshold is None else f"{threshold:.2f}"
        print(f"{name:<14}{passed:>4}/{total:<4}{rate:>8.2f}{shown:>11}  {'PASS' if ok else 'FAIL'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
