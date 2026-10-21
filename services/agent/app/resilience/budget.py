"""The timeout budget's rules (timeout budget PR, I9, ADR-0261; the PR #12 review, decision 9,
ADR-0269): every nested pair of docs/RESILIENCE.md's timeout budget whose two values are
configured, and every margin the code must use for a bound nested by construction to decide.

Pure functions over a dict of values (`values`, built by scripts/check_timeout_budget.py from the
files that set them), so a later endpoint that saves one of those files can run the same rules on
the values it is about to write. Standard library only.

- `checks(values)`: one Check per rule, by a stable name.
- EXPECTED: the names the shipped configuration must produce; a missing one fails (`verify`), so a
  rule never drops out because a file changed shape.
- MUTATIONS: one violating change per rule; `self_test` requires that each makes its own rule fail.

A new nested pair needs a rule here, its name in EXPECTED, a mutation and a row in
docs/RESILIENCE.md."""

import copy
import re
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Check:
    name: str
    row: str
    holds: bool
    detail: str


def _pair(
    name: str, row: str, inner: str, inner_v: float, outer: str, outer_v: float, *, strict=True
) -> Check:
    holds = inner_v < outer_v if strict else inner_v <= outer_v
    sign = "<" if strict else "≤"
    return Check(name, row, holds, f"{inner} {inner_v:g} {sign} {outer} {outer_v:g}")


def _uses(name: str, row: str, source: str, pattern: str, constant: str, value: float) -> Check:
    """The code uses `constant` (> 0) where `pattern` says, so the bound it nests decides."""
    found = re.search(pattern, source) is not None
    return Check(
        name,
        row,
        found and value > 0,
        f"{constant} {value:g} > 0 and used ({'found' if found else 'NOT found'}: {pattern})",
    )


def checks(v: dict) -> list[Check]:
    turn, models, docs, lite = v["turn"], v["models"], v["docs"], v["litellm"]
    timeouts, c, src, gw = models["timeouts"], v["constants"], v["sources"], v["gateway"]
    roles = timeouts["roles"]
    room = turn["deadline_s"] - turn["answer_reserve_s"]
    relay = turn["deadline_s"] + turn["gateway_margin_s"]
    out = [
        _pair(
            "turn-in-relay",
            "Chat: agent turn in the gateway's relay",
            "deadline_s",
            turn["deadline_s"],
            "deadline_s + gateway_margin_s",
            relay,
        ),
        _pair(
            "headers-in-relay",
            "Chat: the gateway's header bound in its relay bound",
            "gateway_headers_s",
            turn["gateway_headers_s"],
            "deadline_s + gateway_margin_s",
            relay,
        ),
        _pair(
            "admission-in-headers",
            "Chat: ownership and lock before the gateway's header bound",
            "admission_s",
            turn["admission_s"],
            "gateway_headers_s",
            turn["gateway_headers_s"],
        ),
        _pair(
            "answer-reserve-in-turn",
            "Chat: the answer's reserve in the turn",
            "answer_reserve_s",
            turn["answer_reserve_s"],
            "deadline_s",
            turn["deadline_s"],
        ),
        _pair(
            "post-answer-in-answer-reserve",
            "Chat: the post-answer reserve in the answer's",
            "post_answer_reserve_s",
            turn["post_answer_reserve_s"],
            "answer_reserve_s",
            turn["answer_reserve_s"],
        ),
        _pair(
            "persist-in-post-answer",
            "Chat: a checkpoint write in the post-answer reserve",
            "persist_s",
            turn["persist_s"],
            "post_answer_reserve_s",
            turn["post_answer_reserve_s"],
        ),
        _pair(
            "judge-and-persist-in-post-answer",
            "Chat: a whole-answer judge check, then the write",
            "timeouts.roles.judge + persist_s",
            roles["judge"] + turn["persist_s"],
            "post_answer_reserve_s",
            turn["post_answer_reserve_s"],
            strict=False,
        ),
        _pair(
            "evidence-in-room",
            "Chat: evidence fetch in the turn before the answer",
            "evidence_fetch_s",
            turn["evidence_fetch_s"],
            "deadline_s − answer_reserve_s",
            room,
        ),
        _pair(
            "reasoning-in-turn",
            "Chat: the answer's model call in the turn",
            "timeouts.roles.reasoning",
            roles["reasoning"],
            "deadline_s",
            turn["deadline_s"],
        ),
        _pair(
            "litellm-backstop-in-longest-call",
            "Chat: LiteLLM's backstop in the agent's longest model call",
            "litellm request_timeout",
            float(lite["litellm_settings"]["request_timeout"]),
            "longest role ceiling",
            max(roles.values()),
        ),
        _pair(
            "litellm-no-retries",
            "Chat: LiteLLM retries none (the agent's retry is inside the deadline)",
            "litellm num_retries",
            float(lite["litellm_settings"].get("num_retries", 0)),
            "none",
            0.0,
            strict=False,
        ),
        _uses(
            "litellm-margin-used",
            "Chat: LiteLLM's per-request timeout before the agent's deadline",
            src["models/gateway.py"],
            r"remaining\s*-\s*LITELLM_MARGIN_S",
            "LITELLM_MARGIN_S",
            c["litellm_margin_s"],
        ),
        _uses(
            "client-margin-used",
            "Chat: the client's per-operation timeout past the agent's deadline",
            src["models/gateway.py"],
            r"remaining\s*\+\s*CLIENT_MARGIN_S",
            "CLIENT_MARGIN_S",
            c["client_margin_s"],
        ),
        _pair(
            "helper-in-shortest-capability",
            "Chat: a helper call in the shortest capability",
            "longest helper role ceiling",
            max(roles[r] for r in ("router", "summarizer", "judge", "extractor")),
            "shortest capability limits.timeout_s",
            min(m["limits"]["timeout_s"] for m in v["manifests"].values()),
        ),
        _pair(
            "register-in-gateway-call",
            "Upload: the agent's answer in the gateway's agent call",
            "register_response_s",
            float(docs["register_response_s"]),
            "gateway response-timeout",
            gw["response_timeout_s"],
        ),
        _pair(
            "upload-stall-in-deadline",
            "Upload: a stalled body read in the whole body's deadline",
            "tomcat connection-upload-timeout",
            gw["connection_upload_timeout_s"],
            "limits.upload_deadline_seconds",
            float(docs["limits"]["upload_deadline_seconds"]),
        ),
        _pair(
            "keep-alive",
            "Shared: the gateway's idle connection under the agent's (Dockerfile)",
            "gateway.agent.keep-alive",
            gw["keep_alive_s"],
            "uvicorn --timeout-keep-alive",
            v["agent_keep_alive_s"],
        ),
        _pair(
            "keep-alive-dev",
            "Shared: the gateway's idle connection under the agent's (dev overlay)",
            "gateway.agent.keep-alive",
            gw["keep_alive_s"],
            "uvicorn --timeout-keep-alive (dev)",
            v["agent_keep_alive_dev_s"],
        ),
        _uses(
            "sdk-read-margin-used",
            "MCP: the transport's deadline before the SDK's read timeout",
            src["mcp/client.py"],
            r"read_timeout_seconds=timedelta\(seconds=entry\.timeout_s \+ _SDK_READ_MARGIN_S\)",
            "_SDK_READ_MARGIN_S",
            c["sdk_read_margin_s"],
        ),
        _uses(
            "attempt-margin-used",
            "MCP: an attempt inside its caller's deadline",
            src["mcp/client.py"],
            r"deadline\.within\(ceiling, margin_s=_ATTEMPT_MARGIN_S\)",
            "_ATTEMPT_MARGIN_S",
            c["attempt_margin_s"],
        ),
    ]
    sandbox = docs["ingest"]["sandbox"]
    for service, workers in sorted(v["sandbox_workers"].items()):
        budget = workers * sandbox["memory_mb"] + sandbox["parent_reserve_mb"]
        out.append(
            _pair(
                f"sandbox-memory:{service}",
                f"Upload: {service}'s sandbox children in its container",
                f"workers {workers} × memory_mb + parent_reserve_mb (MiB)",
                float(budget),
                "mem_limit (MiB)",
                float(v["mem_limits_mb"][service]),
                strict=False,
            )
        )
    for slug, manifest in sorted(v["manifests"].items()):
        out.append(
            _pair(
                f"capability-in-room:{slug}",
                f"Chat: capability {slug} in the turn before the answer",
                f"{slug} limits.timeout_s",
                float(manifest["limits"]["timeout_s"]),
                "deadline_s − answer_reserve_s",
                room,
            )
        )
    if "docs_search" in v["tools"] and "docs" in v["manifests"]:
        search = v["tools"]["docs_search"]
        out += [
            _pair(
                "docs-search-in-docs",
                "Chat: docs_search in the docs capability",
                "docs_search timeout_s",
                search,
                "docs limits.timeout_s",
                float(v["manifests"]["docs"]["limits"]["timeout_s"]),
            ),
            _pair(
                "embed-rerank-in-docs-search",
                "Chat: a query's embedding and rerank in docs_search",
                "timeouts.embedding.query + timeouts.rerank",
                timeouts["embedding"]["query"] + timeouts["rerank"],
                "docs_search timeout_s",
                search,
            ),
        ]
    for server in v["mcp"]["servers"]:
        sid, t = server["id"], float(server["timeout_s"])
        bound = 2 * t + c["broker_margin_s"]
        out.append(
            _pair(
                f"slot-and-attempt-in-broker:{sid}",
                f"MCP: {sid}'s slot wait and attempt in the broker bound",
                f"slot wait {sid}.timeout_s + attempt {sid}.timeout_s",
                2 * t,
                f"broker bound 2 × {sid}.timeout_s + _BROKER_MARGIN_S",
                bound,
            )
        )
        for slug, manifest in sorted(v["manifests"].items()):
            if any(tool.startswith(f"{sid}.") for tool in manifest.get("tools") or []):
                out.append(
                    _pair(
                        f"broker-in-capability:{sid}:{slug}",
                        f"MCP: {sid} tools in capability {slug}",
                        f"broker bound 2 × {sid}.timeout_s + margin",
                        bound,
                        f"{slug} limits.timeout_s",
                        float(manifest["limits"]["timeout_s"]),
                    )
                )
        if sid == docs.get("mcp_server") and "docs_search" in v["tools"]:
            out.append(
                _pair(
                    f"server-call-in-docs-search:{sid}",
                    f"MCP: one {sid} call (search) in docs_search",
                    f"{sid}.timeout_s",
                    t,
                    "docs_search timeout_s",
                    v["tools"]["docs_search"],
                )
            )
    return out


# The rules the shipped configuration produces; a missing one fails.
EXPECTED: tuple[str, ...] = (
    "turn-in-relay",
    "headers-in-relay",
    "admission-in-headers",
    "answer-reserve-in-turn",
    "post-answer-in-answer-reserve",
    "persist-in-post-answer",
    "judge-and-persist-in-post-answer",
    "evidence-in-room",
    "reasoning-in-turn",
    "litellm-backstop-in-longest-call",
    "litellm-no-retries",
    "litellm-margin-used",
    "client-margin-used",
    "helper-in-shortest-capability",
    "register-in-gateway-call",
    "upload-stall-in-deadline",
    "keep-alive",
    "keep-alive-dev",
    "sdk-read-margin-used",
    "attempt-margin-used",
    "sandbox-memory:ingest",
    "sandbox-memory:mcp-rag-ref",
    "capability-in-room:directions",
    "capability-in-room:docs",
    "capability-in-room:general",
    "docs-search-in-docs",
    "embed-rerank-in-docs-search",
    "slot-and-attempt-in-broker:routing",
    "broker-in-capability:routing:directions",
    "slot-and-attempt-in-broker:rag",
    "server-call-in-docs-search:rag",
)


def _server(v: dict, sid: str) -> dict:
    return next(s for s in v["mcp"]["servers"] if s["id"] == sid)


def _sub(file: str, pattern: str, repl: str) -> Callable[[dict], None]:
    def mutate(v: dict) -> None:
        v["sources"][file] = re.sub(pattern, repl, v["sources"][file])

    return mutate


# One violating change per rule, by the rule's name: `self_test` requires that rule to fail.
MUTATIONS: dict[str, Callable[[dict], None]] = {
    "turn-in-relay": lambda v: v["turn"].update(gateway_margin_s=0),
    "headers-in-relay": lambda v: v["turn"].update(gateway_headers_s=500),
    "admission-in-headers": lambda v: v["turn"].update(admission_s=15),
    "answer-reserve-in-turn": lambda v: v["turn"].update(answer_reserve_s=120),
    "post-answer-in-answer-reserve": lambda v: v["turn"].update(post_answer_reserve_s=45),
    "persist-in-post-answer": lambda v: v["turn"].update(persist_s=30),
    "judge-and-persist-in-post-answer": lambda v: v["models"]["timeouts"]["roles"].update(judge=25),
    "evidence-in-room": lambda v: v["turn"].update(evidence_fetch_s=80),
    "reasoning-in-turn": lambda v: v["models"]["timeouts"]["roles"].update(reasoning=120),
    "litellm-backstop-in-longest-call": lambda v: v["litellm"]["litellm_settings"].update(
        request_timeout=300
    ),
    "litellm-no-retries": lambda v: v["litellm"]["litellm_settings"].update(num_retries=1),
    "litellm-margin-used": _sub("models/gateway.py", r"remaining\s*-\s*LITELLM_MARGIN_S", "remaining"),
    "client-margin-used": _sub("models/gateway.py", r"remaining\s*\+\s*CLIENT_MARGIN_S", "remaining"),
    "helper-in-shortest-capability": lambda v: v["models"]["timeouts"]["roles"].update(router=30),
    "register-in-gateway-call": lambda v: v["docs"].update(register_response_s=62),
    "upload-stall-in-deadline": lambda v: v["gateway"].update(connection_upload_timeout_s=120),
    "keep-alive": lambda v: v.update(agent_keep_alive_s=5.0),
    "keep-alive-dev": lambda v: v.update(agent_keep_alive_dev_s=5.0),
    "sdk-read-margin-used": _sub(
        "mcp/client.py", r"entry\.timeout_s \+ _SDK_READ_MARGIN_S", "entry.timeout_s"
    ),
    "attempt-margin-used": _sub(
        "mcp/client.py",
        r"deadline\.within\(ceiling, margin_s=_ATTEMPT_MARGIN_S\)",
        "deadline.within(ceiling)",
    ),
    "sandbox-memory:ingest": lambda v: v["sandbox_workers"].update(ingest=3),
    "sandbox-memory:mcp-rag-ref": lambda v: v["mem_limits_mb"].update({"mcp-rag-ref": 1024}),
    "capability-in-room:directions": lambda v: v["manifests"]["directions"]["limits"].update(timeout_s=500),
    "capability-in-room:docs": lambda v: v["manifests"]["docs"]["limits"].update(timeout_s=500),
    "capability-in-room:general": lambda v: v["manifests"]["general"]["limits"].update(timeout_s=500),
    "docs-search-in-docs": lambda v: v["tools"].update(docs_search=90),
    "embed-rerank-in-docs-search": lambda v: v["models"]["timeouts"]["embedding"].update(query=45),
    "slot-and-attempt-in-broker:routing": lambda v: v["constants"].update(broker_margin_s=-1.0),
    "broker-in-capability:routing:directions": lambda v: _server(v, "routing").update(timeout_s=100),
    "slot-and-attempt-in-broker:rag": lambda v: v["constants"].update(broker_margin_s=-1.0),
    "server-call-in-docs-search:rag": lambda v: _server(v, "rag").update(timeout_s=60),
}


def verify(values: dict) -> tuple[list[Check], list[str]]:
    """Every check, and the names EXPECTED lists that no check produced."""
    found = checks(values)
    names = {check.name for check in found}
    return found, [name for name in EXPECTED if name not in names]


def self_test(values: dict) -> list[str]:
    """What the self-test found wrong: a rule without a mutation, or a mutation that left its own
    rule holding."""
    problems = [f"{name}: no mutation" for name in EXPECTED if name not in MUTATIONS]
    for name, mutate in MUTATIONS.items():
        changed = copy.deepcopy(values)
        mutate(changed)
        own = [check for check in checks(changed) if check.name == name]
        if not own:
            problems.append(f"{name}: the mutation removed the rule instead of failing it")
        elif own[0].holds:
            problems.append(f"{name}: a violation went unnoticed ({own[0].detail})")
    return problems
