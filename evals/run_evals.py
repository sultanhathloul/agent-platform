# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27", "pyyaml>=6.0"]
# ///
"""Offline evaluation (README §12) against the live stack, through the
gateway, with real models.

Suites are evals/golden/*.jsonl plus services/agent/app/capabilities/*/evals/*.jsonl
(README §18.4); the suite name is the file stem. Every suite needs a threshold
in evals/thresholds.yaml, and a suite without one fails the run. Each suite is
a Langfuse dataset with one item per case; every run creates a dataset run
(`make-eval-<UTC time>`) linking each case to its turn's trace, and pushes an
`eval_pass` score on that trace (README §12, ADR-0045).

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
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from get_token import env, login  # noqa: E402

# A dedicated seed user whose memory is switched off before every run, so a case never
# reads what an earlier case or run stored; the analyst keeps memory for smoke (ADR-0074).
EVAL_USER = ("evaluator", "evaluator")
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


def langfuse(http: httpx.Client, cfg: dict, auth: tuple[str, str], path: str, body: dict) -> None:
    response = http.post(f"{cfg['LANGFUSE_PUBLIC_URL']}/api/public/{path}", auth=auth, json=body)
    response.raise_for_status()


def disable_memory(http: httpx.Client, gateway: str, token: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    response = http.put(f"{gateway}/v1/memory/settings", json={"enabled": False}, headers=headers)
    response.raise_for_status()
    listed = http.get(f"{gateway}/v1/memory", headers=headers)
    listed.raise_for_status()
    if listed.json()["data"]["enabled"] is not False:
        raise SystemExit("the eval user's memory is still on")


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
    token = login(*EVAL_USER)["access_token"]  # fail before any Langfuse write when the user cannot log in
    auth = (cfg["LANGFUSE_PUBLIC_KEY"], cfg["LANGFUSE_SECRET_KEY"])
    run_name = f"make-eval-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    rows, failed = [], False

    with httpx.Client(timeout=300) as http:
        disable_memory(http, cfg["GATEWAY_PUBLIC_URL"], token)
        for name, path in found.items():
            cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            # One Langfuse dataset per suite, one item per case, one run per `make eval` (README §12).
            langfuse(http, cfg, auth, "v2/datasets", {"name": name, "description": f"{path.relative_to(REPO)}"})
            passed = 0
            for case in cases:
                item_id = f"{name}:{case['id']}"
                langfuse(
                    http, cfg, auth, "dataset-items",
                    {"datasetName": name, "id": item_id,
                     "input": {"message": case["message"], "locale": case.get("locale", "ar")},
                     "expectedOutput": case.get("expect", {})},
                )
                # A fresh token per case: a suite outlasts the realm's 300 s access token (ADR-0065).
                token = login(*EVAL_USER)["access_token"]
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
                    langfuse(
                        http, cfg, auth, "dataset-run-items",
                        {"runName": run_name, "datasetItemId": item_id, "traceId": trace_id,
                         "metadata": {"passed": not problems, "problems": problems}},
                    )
                    langfuse(
                        http, cfg, auth, "scores",
                        {"traceId": trace_id, "name": "eval_pass", "value": 0 if problems else 1,
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
    print(f"\nLangfuse dataset run: {run_name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
