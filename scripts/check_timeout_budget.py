# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml>=6.0"]
# ///
"""The timeout budget, checked (timeout budget PR, I9; ADR-0261; docs/RESILIENCE.md).

Reads the files that set the platform's bounds — config/turn.yaml, config/models.yaml,
config/docs.yaml, config/mcp.yaml, every capability manifest (and docs_search's tool bound),
infra/litellm/config.yaml, the gateway's application.yml and the agent's Dockerfile — and checks
every nested pair of docs/RESILIENCE.md's timeout budget table whose two values are configured:
the inner bound must be shorter than the one enclosing it. Bounds that are nested by construction
(an inner call takes min(its ceiling, what remains of the outer), app/resilience/deadline.py) are
checked where a configured value still decides: a ceiling that could never apply, a backstop, a
margin. Exits 1 and names each pair with inner ≥ outer.

    uv run scripts/check_timeout_budget.py             # make test runs it
    uv run scripts/check_timeout_budget.py --self-test # each check fails on a violating value"""

import copy
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
AGENT = REPO / "services/agent"


def _duration_s(text: str) -> float:
    """A Spring Boot duration (`60s`, `500ms`, `2m`) in seconds."""
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m)", str(text).strip())
    if not match:
        raise ValueError(f"not a duration: {text!r}")
    value, unit = float(match[1]), match[2]
    return value / 1000 if unit == "ms" else value * 60 if unit == "m" else value


def _constant(path: Path, name: str) -> float:
    match = re.search(rf"^{name}\s*=\s*([0-9.]+)", path.read_text(encoding="utf-8"), re.MULTILINE)
    if not match:
        raise ValueError(f"{path.relative_to(REPO)}: no {name}")
    return float(match[1])


def load(repo: Path = REPO) -> dict:
    """Every value the checks read, by the file it comes from."""
    agent = repo / "services/agent"
    config = repo / "config"
    manifests = {
        path.parent.name: yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in sorted((agent / "app/capabilities").glob("*/manifest.yaml"))
    }
    tools = {}
    for path in sorted((agent / "app/capabilities").glob("*/tools.py")):
        for name, bound in re.findall(r'name="([a-z_]+)".*?timeout_s=(\d+(?:\.\d+)?)', path.read_text(), re.DOTALL):
            tools[name] = float(bound)
    gateway = yaml.safe_load((repo / "services/gateway/src/main/resources/application.yml").read_text())
    dockerfile = (agent / "Dockerfile").read_text(encoding="utf-8")
    keep_alive = re.search(r'"--timeout-keep-alive",\s*"(\d+)"', dockerfile)
    return {
        "turn": yaml.safe_load((config / "turn.yaml").read_text(encoding="utf-8")),
        "models": yaml.safe_load((config / "models.yaml").read_text(encoding="utf-8")),
        "docs": yaml.safe_load((config / "docs.yaml").read_text(encoding="utf-8")),
        "mcp": yaml.safe_load((config / "mcp.yaml").read_text(encoding="utf-8")),
        "manifests": manifests,
        "tools": tools,
        "litellm": yaml.safe_load((repo / "infra/litellm/config.yaml").read_text(encoding="utf-8")),
        "gateway": {
            "response_timeout_s": _duration_s(gateway["gateway"]["agent"]["response-timeout"]),
            "keep_alive_s": _duration_s(gateway["gateway"]["agent"]["keep-alive"]),
            "connection_upload_timeout_s": _duration_s(gateway["server"]["tomcat"]["connection-upload-timeout"]),
        },
        "agent_keep_alive_s": float(keep_alive[1]) if keep_alive else 5.0,  # uvicorn's own default
        "constants": {
            "sdk_read_margin_s": _constant(agent / "app/mcp/client.py", "_SDK_READ_MARGIN_S"),
            "attempt_margin_s": _constant(agent / "app/mcp/client.py", "_ATTEMPT_MARGIN_S"),
            "broker_margin_s": _constant(agent / "app/mcp/tools.py", "_BROKER_MARGIN_S"),
            "client_margin_s": _constant(agent / "app/models/gateway.py", "CLIENT_MARGIN_S"),
            "litellm_margin_s": _constant(agent / "app/models/gateway.py", "LITELLM_MARGIN_S"),
        },
    }


@dataclass(frozen=True)
class Pair:
    """One nested pair: `inner` must be shorter than `outer` (or, `strict=False`, no longer)."""

    row: str
    inner: str
    inner_s: float
    outer: str
    outer_s: float
    strict: bool = True
    unit: str = " s"

    @property
    def holds(self) -> bool:
        return self.inner_s < self.outer_s if self.strict else self.inner_s <= self.outer_s


def pairs(v: dict) -> list[Pair]:
    turn, models, docs, lite = v["turn"], v["models"], v["docs"], v["litellm"]
    timeouts, c = models["timeouts"], v["constants"]
    capability_room = turn["deadline_s"] - turn["answer_reserve_s"]
    out = [
        Pair("Chat: agent turn in the gateway's relay", "turn deadline_s", turn["deadline_s"],
             "relay bound deadline_s + gateway_margin_s", turn["deadline_s"] + turn["gateway_margin_s"]),
        Pair("Chat: the answer's reserve in the turn", "answer_reserve_s", turn["answer_reserve_s"],
             "deadline_s", turn["deadline_s"]),
        Pair("Chat: evidence fetch in the turn before the answer", "evidence_fetch_s", turn["evidence_fetch_s"],
             "deadline_s − answer_reserve_s", capability_room),
        Pair("Chat: the answer's model call in the turn", "timeouts.roles.reasoning", timeouts["roles"]["reasoning"],
             "deadline_s", turn["deadline_s"]),
        Pair("Chat: LiteLLM's backstop in the agent's longest model call", "litellm request_timeout",
             float(lite["litellm_settings"]["request_timeout"]), "longest role ceiling",
             max(timeouts["roles"].values())),
        Pair("Chat: LiteLLM retries none (the agent's retry is inside the call's deadline)",
             "litellm num_retries", float(lite["litellm_settings"].get("num_retries", 0)), "none", 0.0,
             strict=False, unit=""),
        Pair("Chat: LiteLLM's per-request timeout before the agent's deadline", "0 (remaining − margin)", 0.0,
             "LITELLM_MARGIN_S", c["litellm_margin_s"]),
        Pair("Chat: the client's per-operation timeout past the agent's deadline", "0 (remaining + margin)", 0.0,
             "CLIENT_MARGIN_S", c["client_margin_s"]),
        Pair("Chat: a helper call in the shortest capability", "longest helper role ceiling",
             max(timeouts["roles"][r] for r in ("router", "summarizer", "judge", "extractor")),
             "shortest capability limits.timeout_s",
             min(m["limits"]["timeout_s"] for m in v["manifests"].values())),
        Pair("Upload: the agent's answer in the gateway's agent call", "register_response_s",
             float(docs["register_response_s"]), "gateway response-timeout", v["gateway"]["response_timeout_s"]),
        Pair("Upload: a stalled body read in the whole body's deadline", "tomcat connection-upload-timeout",
             v["gateway"]["connection_upload_timeout_s"], "limits.upload_deadline_seconds",
             float(docs["limits"]["upload_deadline_seconds"])),
        Pair("Shared: the gateway's idle connection under the agent's", "gateway.agent.keep-alive",
             v["gateway"]["keep_alive_s"], "uvicorn --timeout-keep-alive", v["agent_keep_alive_s"]),
        Pair("MCP: the transport's deadline before the SDK's read timeout", "0 (timeout_s)", 0.0,
             "_SDK_READ_MARGIN_S", c["sdk_read_margin_s"]),
        Pair("MCP: an attempt inside its caller's deadline", "0 (remaining − margin)", 0.0,
             "_ATTEMPT_MARGIN_S", c["attempt_margin_s"]),
    ]
    for slug, manifest in v["manifests"].items():
        out.append(Pair(f"Chat: capability {slug} in the turn before the answer", f"{slug} limits.timeout_s",
                        float(manifest["limits"]["timeout_s"]), "deadline_s − answer_reserve_s", capability_room))
    if "docs_search" in v["tools"] and "docs" in v["manifests"]:
        search = v["tools"]["docs_search"]
        out += [
            Pair("Chat: docs_search in the docs capability", "docs_search timeout_s", search,
                 "docs limits.timeout_s", float(v["manifests"]["docs"]["limits"]["timeout_s"])),
            Pair("Chat: a query's embedding and rerank in docs_search",
                 "timeouts.embedding.query + timeouts.rerank",
                 timeouts["embedding"]["query"] + timeouts["rerank"], "docs_search timeout_s", search),
        ]
    for server in v["mcp"]["servers"]:
        bound = 2 * server["timeout_s"] + c["broker_margin_s"]
        for slug, manifest in v["manifests"].items():
            declared = [t for t in manifest.get("tools") or [] if t.startswith(f"{server['id']}.")]
            if declared:
                out.append(Pair(f"MCP: {server['id']} tools in capability {slug}",
                                f"broker bound 2 × {server['id']}.timeout_s + margin", bound,
                                f"{slug} limits.timeout_s", float(manifest["limits"]["timeout_s"])))
        if server["id"] == docs.get("mcp_server") and "docs_search" in v["tools"]:
            out.append(Pair(f"MCP: one {server['id']} call (search) in docs_search", f"{server['id']}.timeout_s",
                            float(server["timeout_s"]), "docs_search timeout_s", v["tools"]["docs_search"]))
    return out


def report(checked: list[Pair]) -> int:
    failed = [p for p in checked if not p.holds]
    for p in checked:
        sign = "<" if p.strict else "≤"
        print(f"  {'ok  ' if p.holds else 'FAIL'}  {p.row}: {p.inner} {p.inner_s:g}{p.unit} {sign} "
              f"{p.outer} {p.outer_s:g}{p.unit}")
    print(f"timeout budget {len(checked) - len(failed)} / {len(failed)} / 0")
    return 1 if failed else 0


# Each mutation must make at least one pair fail: the checks catch what they claim to.
MUTATIONS: list[tuple[str, Callable[[dict], None]]] = [
    ("relay bound no longer than the turn", lambda v: v["turn"].update(gateway_margin_s=0)),
    ("a capability past the room before the answer", lambda v: v["manifests"]["general"]["limits"].update(timeout_s=500)),
    ("LiteLLM's backstop past the longest call", lambda v: v["litellm"]["litellm_settings"].update(request_timeout=300)),
    ("LiteLLM retrying again", lambda v: v["litellm"]["litellm_settings"].update(num_retries=1)),
    ("registration past the gateway's call", lambda v: v["docs"].update(register_response_s=62)),
    ("the gateway's keep-alive past the agent's", lambda v: v.update(agent_keep_alive_s=5.0)),
    ("the SDK's read timeout equal to the transport's deadline", lambda v: v["constants"].update(sdk_read_margin_s=0)),
    ("an MCP tool's bound past its capability", lambda v: v["mcp"]["servers"][0].update(timeout_s=100)),
    ("embedding and rerank past docs_search", lambda v: v["models"]["timeouts"]["embedding"].update(query=45)),
]


def self_test(values: dict) -> int:
    assert all(p.holds for p in pairs(values)), "the shipped values must pass before the self-test"
    missed = []
    for name, mutate in MUTATIONS:
        changed = copy.deepcopy(values)
        mutate(changed)
        if all(p.holds for p in pairs(changed)):
            missed.append(name)
    for name in missed:
        print(f"  FAIL  self-test: a violation went unnoticed: {name}")
    print(f"timeout budget self-test {len(MUTATIONS) - len(missed)} / {len(missed)} / 0")
    return 1 if missed else 0


if __name__ == "__main__":
    values = load()
    if "--self-test" in sys.argv[1:]:
        sys.exit(self_test(values))
    sys.exit(report(pairs(values)))
