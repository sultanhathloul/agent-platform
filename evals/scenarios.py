"""Scenario suites (report library PR, ADR-0438): conversations scripted turn by turn against the live stack,
each step asserted, the whole transcript saved. A suite is a folder evals/scenarios/<name>/ with
`scenario.yaml`; its name is the folder's. run_evals.py runs it like any suite and gates it per
evals/thresholds.yaml.

A scenario names the seed user it runs as and its conversations; each step is a turn (`message`, optional
`selected_kbs`, `output`, `as` another seed user, `conversation`) or a check (`check: library | report |
memory`). A step's `expect` keys:

- `form`: text | report; `version`: the artifact's version; `report`: a name for the artifact's id, and
  `same_report` / `new_report`: the id equals, or differs from, a named one; `based_on`: the new report was
  made from a named one (its reply says so);
- `answer_any` / `answer_all` / `answer_none`: phrases (Arabic-folded) the answer holds or not;
  `numbers_of`: the answer shares a number with a named step's answer; `nothing_found`: the reply says the
  selected sources hold nothing; `cites_report`: the answer cites «من التقرير»;
- `tools_any` / `tools_none`: tool names among the turn's `tool_start` events;
- `summary`: the conversation has a rolling summary after this turn (read from its checkpoint);
- `mentions_reports`: named reports whose titles the answer names;
- `cites_file`: the answer cites «من ملفك» / "from your file"; `cited`: a named upload is among the done
  event's citations (my-files PR, ADR-0443).

A turn step may carry `upload: <file>` (a file of the scenario's folder) and `upload_name`: the file is uploaded
to the step's conversation and indexed before the turn, which carries it (my-files PR).

A check step: `files` (GET /v1/files as the step's user: `all_of` / `none_of` named uploads); `delete_file`
(DELETE the named upload as the step's user: `status`, 204 by default); `memory_on` (switches the user's memory to `enabled`); `library` (GET /v1/reports as the step's user: none of the named reports); `report` (GET
/v1/reports/{id} as the step's user: `status` and, when 200, `latest_version`); `memory` (the user's memory
items added since the scenario began: each grounded in one of the user's own messages of the scenario, none
holding `memory_none` phrases; `memory_any`: phrases one of them holds).

Measured per turn: wall time, the done event's usage, the answer's context tokens (the `context.assemble`
span of the answer or report node, from Langfuse) and, at `measure_at` turns, the checkpoint's size."""

import json
import re
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import yaml

PROBE = r"""
import asyncio, json, sys
from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from app.settings import Settings
async def main(cid):
    s = Settings()
    async with AsyncConnectionPool(s.database_url, kwargs={"autocommit": True, "row_factory": dict_row,
            "prepare_threshold": 0, "options": "-c search_path=agent"}, open=False) as pool:
        t = await AsyncPostgresSaver(pool).aget_tuple({"configurable": {"thread_id": cid}})
        v = t.checkpoint["channel_values"] if t else {}
        print(json.dumps({"summary_chars": len(v.get("summary") or ""), "summary_cursor": v.get("summary_cursor") or 0,
                          "messages": len(v.get("messages") or [])}))
asyncio.run(main(sys.argv[1]))
"""


def _fold(text: str) -> str:
    table = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه", "٫": ".", **{a: str(i) for i, a in enumerate("٠١٢٣٤٥٦٧٨٩")}})
    return re.sub("[ً-ٰٟـ]", "", (text or "").translate(table)).lower()


_NUMBER = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)(?![\d.])")


def _numbers(text: str) -> set[float]:
    return {float(n) for n in _NUMBER.findall(re.sub(r"(?<=\d)[,٬](?=\d{3})", "", _fold(text)))}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"\w+", _fold(text)) if len(w) >= 3}


def grounded(item: str, sources: list[str], share: float = 0.5) -> bool:
    """At least `share` of a memory item's content words are in one of the user's own messages."""
    words = _words(item) - {"المستخدم", "user", "the", "and"}
    if not words:
        return False
    stems = lambda ws: {w[2:] if w.startswith("ال") and len(w) > 4 else w for w in ws}  # noqa: E731
    target = stems(words)
    return any(len(target & stems(_words(s))) / len(target) >= share for s in sources)


def sql(query: str, repo: Path) -> str:
    out = subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "postgres", "-d", "app", "-tAc", query],
                         cwd=repo, capture_output=True, text=True, timeout=60)
    return out.stdout.strip()


def probe(conversation: str, repo: Path) -> dict:
    out = subprocess.run(["docker", "compose", "exec", "-T", "-w", "/app", "agent", "env", "PYTHONPATH=.", "python", "-",
                          conversation], cwd=repo, input=PROBE, capture_output=True, text=True, timeout=120)
    try:
        return json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (out.stderr or "")[-300:]}


def checkpoint_size(conversation: str, repo: Path) -> dict:
    row = sql("select coalesce(sum(length(blob)),0) || ',' || count(*) || ',' || "
              f"coalesce((select max(length(blob)) from agent.checkpoint_blobs where thread_id = '{conversation}' "
              f"and channel = 'messages'),0) || ',' || (select count(*) from agent.checkpoints where thread_id = "
              f"'{conversation}') from agent.checkpoint_blobs where thread_id = '{conversation}'", repo)
    try:
        total, blobs, messages, checkpoints = (int(x) for x in row.split(","))
    except ValueError:
        return {"error": row[-200:]}
    return {"blob_bytes": total, "blobs": blobs, "messages_blob_bytes": messages, "checkpoints": checkpoints}


def stream_turn(http: httpx.Client, gateway: str, token: str, body: dict, backoff) -> tuple[list[dict], float]:
    headers = {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}

    def once() -> list[dict]:
        events: list[dict] = []
        try:
            with http.stream("POST", f"{gateway}/v1/chat/turns", json=body, headers=headers) as response:
                if response.status_code >= 400:
                    response.read()
                    return [{"type": "error", "data": {"code": f"http_{response.status_code}"}}]
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        events.append(json.loads(line[6:]))
        except httpx.TransportError as exc:
            return [{"type": "error", "data": {"code": f"transport_{type(exc).__name__}"}}]
        return events

    started = time.monotonic()
    events = backoff(once, lambda evs: bool(evs) and evs[-1].get("type") == "error"
                     and evs[-1].get("data", {}).get("code") == "model_unavailable")
    return events, time.monotonic() - started


def context_tokens(http: httpx.Client, cfg: dict, auth, trace_id: str | None) -> int | None:
    """The answer's (or report writer's) assembled context, in tokens, from the turn's trace."""
    if not trace_id:
        return None
    for _ in range(30):
        response = http.get(f"{cfg['LANGFUSE_PUBLIC_URL']}/api/public/traces/{trace_id}", auth=auth)
        if response.status_code == 200:
            totals = [
                ((o.get("metadata") or {}).get("context") or {}).get("total_tokens")
                for o in response.json().get("observations", [])
                if o.get("name") == "context.assemble"
                and ((o.get("metadata") or {}).get("context") or {}).get("node") in ("answer", "report")
            ]
            totals = [t for t in totals if isinstance(t, int)]
            if totals:
                return max(totals)
        time.sleep(2)
    return None


class Runner:
    def __init__(self, http, cfg, auth, login, backoff, repo: Path, users: dict):
        self.http, self.cfg, self.auth, self.login, self.backoff, self.repo = http, cfg, auth, login, backoff, repo
        self.users = users
        self.gateway = cfg["GATEWAY_PUBLIC_URL"]
        self.tokens: dict[str, tuple[str, float]] = {}
        self.reports: dict[str, str] = {}  # name → report id
        self.answers: dict[str, str] = {}  # step id → answer
        self.user_messages: dict[str, list[str]] = {}
        self.conversations: dict[str, str] = {}
        self.turns: dict[str, int] = {}
        self.uploads: dict[str, tuple[str, str]] = {}  # name → (conversation id, attachment id)
        self.folder: Path | None = None

    def token(self, user: str) -> str:
        token, at = self.tokens.get(user, ("", 0.0))
        if time.monotonic() - at > 200:  # the realm's access tokens live 300 s
            token = self.login(*self.users[user])["access_token"]
            self.tokens[user] = (token, time.monotonic())
        return token

    def get(self, user: str, path: str, **params) -> httpx.Response:
        return self.http.get(f"{self.gateway}{path}", params=params, headers={"Authorization": f"Bearer {self.token(user)}"})

    def memory(self, user: str) -> list[dict]:
        response = self.get(user, "/v1/memory")
        response.raise_for_status()
        return response.json()["data"]["items"]

    def upload(self, user: str, conversation: str, file: str) -> str:
        """Uploads a file of the scenario's folder to `conversation` and waits until it is indexed."""
        path = (self.folder or Path(".")) / file
        headers = {"Authorization": f"Bearer {self.token(user)}"}
        for _ in range(5):
            with path.open("rb") as handle:
                response = self.http.post(f"{self.gateway}/v1/conversations/{conversation}/attachments",
                                          files={"file": (path.name, handle)}, headers=headers)
            if response.status_code != 429:
                break
            time.sleep(61)  # the per-user upload allowance is per minute (ADR-0177)
        response.raise_for_status()
        attachment = response.json()["data"]["id"]
        for _ in range(120):
            status = self.http.get(f"{self.gateway}/v1/conversations/{conversation}/attachments/{attachment}",
                                   headers=headers)
            status.raise_for_status()
            if status.json()["data"]["status"] == "indexed":
                return attachment
            if status.json()["data"]["status"] == "failed":
                raise RuntimeError(f"{file} failed to index")
            time.sleep(1)
        raise RuntimeError(f"{file} was not indexed within 120 s")

    # ------------------------------------------------------------------ steps

    def turn(self, step: dict, user: str, measure_at: set[int]) -> dict:
        name = step.get("conversation", "A")
        conversation = self.conversations.setdefault(f"{user}:{name}", str(uuid.uuid4()))
        body = {"conversation_id": conversation, "message": step["message"], "locale": step.get("locale", "ar"),
                "attachment_ids": []}
        if step.get("upload"):
            attachment = self.upload(user, conversation, step["upload"])
            self.uploads[step.get("upload_name") or step["upload"]] = (conversation, attachment)
            body["attachment_ids"] = [attachment]
        if step.get("selected_kbs"):
            body["selected_kbs"] = step["selected_kbs"]
        if step.get("output"):
            body["output"] = step["output"]
        if step.get("skill"):
            body["skill"] = step["skill"]
        self.user_messages.setdefault(user, []).append(step["message"])
        events, seconds = stream_turn(self.http, self.gateway, self.token(user), body, self.backoff)
        last = events[-1] if events else {"type": "error", "data": {"code": "no_event"}}
        data = last.get("data") or {}
        number = self.turns[f"{user}:{name}"] = self.turns.get(f"{user}:{name}", 0) + 1
        record = {
            "id": step["id"], "user": user, "conversation": name, "turn": number, "message": step["message"],
            "selected_kbs": step.get("selected_kbs") or [], "output_picked": step.get("output"),
            "end": last.get("type"), "error": data.get("code") if last.get("type") == "error" else None,
            "answer": data.get("answer", ""), "form": (data.get("state_digest") or {}).get("output"),
            "capabilities": (data.get("state_digest") or {}).get("capabilities"),
            "artifact": data.get("artifact"), "tools": [e["data"].get("tool") for e in events if e.get("type") == "tool_start"],
            "seconds": round(seconds, 2), "usage": data.get("usage"), "trace_id": data.get("trace_id"),
            "citations": [{"document_id": c.get("document_id"), "title": c.get("title"), "pages": c.get("pages")}
                          for c in data.get("citations") or []],
        }
        record["context_tokens"] = context_tokens(self.http, self.cfg, self.auth, data.get("trace_id"))
        if number in measure_at:
            record["checkpoint"] = checkpoint_size(conversation, self.repo)
        if step.get("expect", {}).get("summary") is not None or number in measure_at:
            record["state"] = probe(conversation, self.repo)
        self.answers[step["id"]] = record["answer"]
        record["problems"] = self.assert_turn(step.get("expect") or {}, record)
        return record

    def assert_turn(self, expect: dict, r: dict) -> list[str]:
        problems: list[str] = []
        if r["end"] != "done":
            return [f"ended {r['end']} {r['error']}"]
        answer = _fold(r["answer"])
        artifact = r["artifact"] or {}
        if "form" in expect and r["form"] != expect["form"]:
            problems.append(f"form {r['form']} != {expect['form']}")
        if "version" in expect and artifact.get("version") != expect["version"]:
            problems.append(f"version {artifact.get('version')} != {expect['version']}")
        if "report" in expect and artifact.get("id"):
            self.reports[expect["report"]] = artifact["id"]
        if "same_report" in expect and artifact.get("id") != self.reports.get(expect["same_report"]):
            problems.append(f"report {artifact.get('id')} is not {expect['same_report']}")
        if "new_report" in expect and (not artifact.get("id") or artifact.get("id") in self.reports.values()
                                       and self.reports.get(expect.get("report")) != artifact.get("id")):
            problems.append("not a new report")
        if expect.get("no_artifact") and artifact:
            problems.append(f"an artifact was written ({artifact.get('id')})")
        if "based_on" in expect and "نسخه جديده من" not in answer and "new version of" not in answer:
            problems.append("the reply does not say it is a new version of an earlier report")
        anyp = expect.get("answer_any") or []
        if anyp and not any(_fold(p) in answer for p in anyp):
            problems.append(f"none of {anyp}")
        for p in expect.get("answer_all") or []:
            if _fold(p) not in answer:
                problems.append(f"missing {p!r}")
        for p in expect.get("answer_none") or []:
            if _fold(p) in answer:
                problems.append(f"holds {p!r}")
        if expect.get("nothing_found") and "لا تحوي المصادر المختاره" not in answer and "hold no data" not in answer:
            problems.append("no nothing-found reply")
        if expect.get("cites_report") and "من التقرير" not in answer and "from the report" not in answer:
            problems.append("no «من التقرير» citation")
        if expect.get("cites_file") and "من ملفك" not in answer and "from your file" not in answer:
            problems.append("no «من ملفك» citation")
        if "cited" in expect:
            wanted = (self.uploads.get(expect["cited"]) or ("", ""))[1]
            if wanted not in {c.get("document_id") for c in r.get("citations") or []}:
                problems.append(f"{expect['cited']} is not among the citations")
        if "numbers_of" in expect:
            shared = _numbers(self.answers.get(expect["numbers_of"], "")) & _numbers(r["answer"])
            if not shared:
                problems.append(f"no number shared with {expect['numbers_of']}")
        tools = set(r["tools"])
        if expect.get("tools_any") and not tools & set(expect["tools_any"]):
            problems.append(f"no tool of {expect['tools_any']} (ran {sorted(tools)})")
        for tool in expect.get("tools_none") or []:
            if tool in tools:
                problems.append(f"ran {tool}")
        if expect.get("summary") is not None:
            has = (r.get("state") or {}).get("summary_chars", 0) > 0
            if has != bool(expect["summary"]):
                problems.append(f"summary present: {has}")
        for name in expect.get("mentions_reports") or []:
            title = self.titles.get(name) if hasattr(self, "titles") else None
            if title is None:
                title = self.title_of(name)
            words = _words(title)
            if not words or len(words & _words(r["answer"])) / len(words) < 0.5:
                problems.append(f"does not name {name} ({title})")
        return problems

    def title_of(self, name: str) -> str:
        if not hasattr(self, "titles"):
            self.titles = {}
        report = self.reports.get(name)
        if report and name not in self.titles:
            response = self.get(self.owner, f"/v1/reports/{report}")
            self.titles[name] = response.json()["data"]["title"] if response.status_code == 200 else ""
        return self.titles.get(name, "")

    def check(self, step: dict, user: str) -> dict:
        kind = step["check"]
        record: dict = {"id": step["id"], "user": user, "check": kind, "problems": []}
        expect = step.get("expect") or {}
        if kind == "library":
            response = self.get(user, "/v1/reports", q=step.get("query", ""))
            ids = [r["id"] for r in response.json().get("data", {}).get("reports", [])] if response.status_code == 200 else []
            record.update(status=response.status_code, listed=len(ids))
            if response.status_code != 200:
                record["problems"].append(f"HTTP {response.status_code}")
            for name in expect.get("none_of") or []:
                if self.reports.get(name) in ids:
                    record["problems"].append(f"lists {name}")
            for name in expect.get("all_of") or []:
                if self.reports.get(name) not in ids:
                    record["problems"].append(f"does not list {name}")
        elif kind == "files":
            response = self.get(user, "/v1/files")
            ids = [f["id"] for f in response.json().get("data", {}).get("files", [])] if response.status_code == 200 else []
            record.update(status=response.status_code, listed=len(ids))
            if response.status_code != 200:
                record["problems"].append(f"HTTP {response.status_code}")
            for name in expect.get("none_of") or []:
                if (self.uploads.get(name) or ("", ""))[1] in ids:
                    record["problems"].append(f"lists {name}")
            for name in expect.get("all_of") or []:
                if (self.uploads.get(name) or ("", ""))[1] not in ids:
                    record["problems"].append(f"does not list {name}")
        elif kind == "delete_file":
            conversation, attachment = self.uploads.get(step["file"]) or ("", "")
            response = self.http.delete(f"{self.gateway}/v1/conversations/{conversation}/attachments/{attachment}",
                                        headers={"Authorization": f"Bearer {self.token(user)}"})
            record.update(status=response.status_code)
            if response.status_code != expect.get("status", 204):
                record["problems"].append(f"HTTP {response.status_code} != {expect.get('status', 204)}")
        elif kind == "report":
            report = self.reports.get(step["report"])
            response = self.get(user, f"/v1/reports/{report}")
            record.update(status=response.status_code)
            if response.status_code != expect.get("status", 200):
                record["problems"].append(f"HTTP {response.status_code} != {expect.get('status', 200)}")
            elif response.status_code == 200 and "latest_version" in expect:
                latest = response.json()["data"]["latest_version"]
                record["latest_version"] = latest
                if latest != expect["latest_version"]:
                    record["problems"].append(f"latest_version {latest} != {expect['latest_version']}")
        elif kind == "memory_on":
            if step.get("clear"):
                # A run starts from an empty memory: an earlier run's items would deduplicate this run's
                # candidates (dedupe_similarity), so nothing would count as added.
                headers = {"Authorization": f"Bearer {self.token(user)}"}
                for item in self.memory(user):
                    self.http.delete(f"{self.gateway}/v1/memory/{item['id']}", headers=headers)
                self.memory_before[user] = {i["id"] for i in self.memory(user)}
                record["cleared"] = True
            response = self.http.put(f"{self.gateway}/v1/memory/settings", json={"enabled": bool(step.get("enabled"))},
                                     headers={"Authorization": f"Bearer {self.token(user)}"})
            record.update(status=response.status_code)
            if response.status_code != 200:
                record["problems"].append(f"HTTP {response.status_code}")
        elif kind == "memory":
            items = [i for i in self.memory(user) if i["id"] not in self.memory_before.get(user, set())]
            record["added"] = [{"kind": i["kind"], "text": i["text"]} for i in items]
            sources = self.user_messages.get(user, [])
            for item in items:
                if not grounded(item["text"], sources):
                    record["problems"].append(f"not stated by the user: {item['text']!r}")
                for phrase in expect.get("memory_none") or []:
                    if _fold(phrase) in _fold(item["text"]):
                        record["problems"].append(f"holds {phrase!r}: {item['text']!r}")
            wanted = expect.get("memory_any") or []
            if wanted and not any(_fold(p) in _fold(i["text"]) for i in items for p in wanted):
                record["problems"].append(f"no item holds any of {wanted}")
        return record

    def run(self, scenario: dict) -> list[dict]:
        self.owner = scenario["user"]
        measure_at = set(scenario.get("measure_at") or [])
        users = {self.owner, *(s.get("as") for s in scenario["steps"] if s.get("as"))}
        self.memory_before = {u: {i["id"] for i in self.memory(u)} for u in users}
        records = []
        for step in scenario["steps"]:
            user = step.get("as") or self.owner
            record = self.check(step, user) if "check" in step else self.turn(step, user, measure_at)
            mark = "pass" if not record["problems"] else "FAIL"
            shown = (record.get("answer") or "")[:70].replace("\n", " ")
            print(f"  {mark}  {step['id']:<6} {user:<8} {record.get('seconds', ''):>7}  {shown!r}  {'; '.join(record['problems'])}")
            records.append(record)
        return records


def is_scenario(path: Path, repo: Path) -> bool:
    return path.resolve().is_relative_to((repo / "evals" / "scenarios").resolve())


def suites(repo: Path) -> list[Path]:
    return sorted((repo / "evals" / "scenarios").glob("*/scenario.yaml"))


def run_scenario(http, cfg, auth, login, backoff, repo: Path, users: dict, name: str, path: Path, threshold, run_name: str):
    """Returns (passed, total, ok, record). Gated per `steps` (the share of steps passed) and `negative` (the
    share of steps marked `negative: true` passed) in evals/thresholds.yaml."""
    scenario = yaml.safe_load(path.read_text(encoding="utf-8"))
    runner = Runner(http, cfg, auth, login, backoff, repo, users)
    runner.folder = path.parent
    print(f"\n{name}: {len(scenario['steps'])} steps as {scenario['user']}")
    records = runner.run(scenario)
    negative = [r for r, s in zip(records, scenario["steps"], strict=True) if s.get("negative")]
    passed = sum(not r["problems"] for r in records)
    scores = {"steps": passed / len(records),
              "negative": (sum(not r["problems"] for r in negative) / len(negative)) if negative else 1.0}
    threshold = threshold or {}
    ok = all(scores.get(metric, 0) >= value for metric, value in threshold.items()) if threshold else False
    turns = [r for r in records if "turn" in r]
    measures = [
        {k: r.get(k) for k in ("id", "conversation", "turn", "seconds", "context_tokens", "usage", "checkpoint", "state")}
        for r in turns if r.get("checkpoint") or r.get("state")
    ]
    transcript = repo / "build" / "evals" / f"{run_name}-{name}.json"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(json.dumps({"suite": name, "scenario": str(path.relative_to(repo)), "scores": scores,
                                      "measures": measures, "steps": records}, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    print(f"  {name}: steps {scores['steps']:.2f} ({passed}/{len(records)}), negative {scores['negative']:.2f} "
          f"({sum(not r['problems'] for r in negative)}/{len(negative)})")
    for m in measures:
        print(f"    measure {m['conversation']} turn {m['turn']}: {m['seconds']} s, context {m['context_tokens']} tokens, "
              f"checkpoint {m.get('checkpoint')}, state {m.get('state')}")
    print(f"  transcript: {transcript.relative_to(repo)}")
    return passed, len(records), ok, {"passed": passed, "total": len(records), "scores": scores, "ok": ok,
                                      "threshold": threshold, "transcript": str(transcript.relative_to(repo)),
                                      "cases": [{"id": r["id"], "passed": not r["problems"], "problems": r["problems"]}
                                                for r in records], "measures": measures}
