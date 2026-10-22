"""The timeout budget's rules (timeout budget PR, I9, ADR-0261; the PR #12 review, decision 9,
ADR-0269): every nested pair of docs/RESILIENCE.md's timeout budget whose two values are
configured, and every margin the code must use for a bound nested by construction to decide.

Pure functions over a dict of values (`values`, built by scripts/check_timeout_budget.py from the
files that set them), so a later endpoint that saves one of those files can run the same rules on
the values it is about to write. Standard library only.

- `checks(values)`: one Check per rule, by a stable name.
- EXPECTED: the names of the fixed rules the shipped configuration must produce, and `generated`:
  the rules each capability, tool and MCP server it declares must produce (ADR-0275); a missing one
  fails (`verify`), so a rule never drops out because a file changed shape.
- MUTATIONS: one violating change per fixed rule, and one generic mutation per generated family
  (`mutations`); `self_test` requires that each makes its own rule fail.

No capability, tool or MCP server is named here: one added or removed changes the rules produced,
never this module. A new nested pair needs a rule here, its name in EXPECTED (or a family), a
mutation and a row in docs/RESILIENCE.md."""

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
            r"deadline\.within\(self\.entry\.timeout_s, margin_s=_ATTEMPT_MARGIN_S\)",
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
    out += [rule(v, *key) for family, rule in _FAMILIES.items() for key in _keys(v, family)]
    return out


# The rules generated from what the configuration declares, one per capability, tool or server:
# no capability, tool or server is named here (review round 2 of PR #12, finding (3), ADR-0275).
# A capability added or removed changes the rules produced, never this module.


def _mcp_server(v: dict, tool: str) -> dict | None:
    """The config/mcp.yaml server of an MCP tool (`<server>.<tool>`), or None for an in-process one."""
    sid = tool.split(".", 1)[0] if "." in tool else None
    return next((s for s in v["mcp"]["servers"] if s["id"] == sid), None)


def _tool_bound(v: dict, tool: str) -> tuple[str, float] | None:
    """What bounds a tool the broker runs: an MCP tool's broker bound (2 × its server's timeout_s
    + _BROKER_MARGIN_S, ADR-0259), an in-process tool's own ToolSpec timeout_s; None when the
    configuration sets neither."""
    server = _mcp_server(v, tool)
    if server is not None:
        sid = server["id"]
        return f"broker bound 2 × {sid}.timeout_s + margin", 2 * float(server["timeout_s"]) + v["constants"][
            "broker_margin_s"
        ]
    if tool in v["tools"]:
        return f"{tool} timeout_s", float(v["tools"][tool])
    return None


def _facade_tools(v: dict, slug: str) -> list[str]:
    """The in-process tools of a capability whose manifest declares `documents`: the broker hands
    each the document facade (ADR-0231), so each must fit one facade search."""
    manifest = v["manifests"][slug]
    if not manifest.get("documents"):
        return []
    return [tool for tool in manifest.get("tools") or [] if _mcp_server(v, tool) is None]


def _document_server(v: dict) -> dict | None:
    return next((s for s in v["mcp"]["servers"] if s["id"] == v["docs"].get("mcp_server")), None)


def _keys(v: dict, family: str) -> list[tuple[str, ...]]:
    manifests = sorted(v["manifests"].items())
    if family == "capability-in-room":
        return [(slug,) for slug, _ in manifests]
    if family == "tool-in-capability":
        return [(slug, tool) for slug, m in manifests for tool in m.get("tools") or []]
    if family == "facade-search-in-tool":
        return [(slug, tool) for slug, _ in manifests for tool in _facade_tools(v, slug)]
    if family == "document-server-in-tool":
        if _document_server(v) is None:
            return []
        return [(slug, tool) for slug, _ in manifests for tool in _facade_tools(v, slug)]
    if family == "slot-and-attempt-in-broker":
        return [(server["id"],) for server in v["mcp"]["servers"]]
    raise KeyError(family)


def _room(v: dict) -> float:
    return v["turn"]["deadline_s"] - v["turn"]["answer_reserve_s"]


def _capability_in_room(v: dict, slug: str) -> Check:
    return _pair(
        f"capability-in-room:{slug}",
        f"Chat: capability {slug} in the turn before the answer",
        f"{slug} limits.timeout_s",
        float(v["manifests"][slug]["limits"]["timeout_s"]),
        "deadline_s − answer_reserve_s",
        _room(v),
    )


def _tool_in_capability(v: dict, slug: str, tool: str) -> Check:
    name, row = f"tool-in-capability:{slug}:{tool}", f"Chat: {tool} in capability {slug}"
    bound = _tool_bound(v, tool)
    if bound is None:  # a declared tool with no bound fails rather than drops out
        return Check(name, row, False, f"{tool}: no bound found (no ToolSpec timeout_s, no MCP server)")
    label, value = bound
    limit = float(v["manifests"][slug]["limits"]["timeout_s"])
    return _pair(name, row, label, value, f"{slug} limits.timeout_s", limit)


def _facade_search_in_tool(v: dict, slug: str, tool: str) -> Check:
    name, row = f"facade-search-in-tool:{slug}:{tool}", f"Chat: a query's embedding and rerank in {tool}"
    bound = _tool_bound(v, tool)
    if bound is None:
        return Check(name, row, False, f"{tool}: no bound found (no ToolSpec timeout_s)")
    timeouts = v["models"]["timeouts"]
    return _pair(
        name,
        row,
        "timeouts.embedding.query + timeouts.rerank",
        timeouts["embedding"]["query"] + timeouts["rerank"],
        bound[0],
        bound[1],
    )


def _document_server_in_tool(v: dict, slug: str, tool: str) -> Check:
    server = _document_server(v)
    sid = server["id"]
    name, row = (
        f"document-server-in-tool:{slug}:{tool}",
        f"MCP: one {sid} call (the document backend's) in {tool}",
    )
    bound = _tool_bound(v, tool)
    if bound is None:
        return Check(name, row, False, f"{tool}: no bound found (no ToolSpec timeout_s)")
    return _pair(name, row, f"{sid}.timeout_s", float(server["timeout_s"]), bound[0], bound[1])


def _slot_and_attempt_in_broker(v: dict, sid: str) -> Check:
    t = float(next(s for s in v["mcp"]["servers"] if s["id"] == sid)["timeout_s"])
    return _pair(
        f"slot-and-attempt-in-broker:{sid}",
        f"MCP: {sid}'s slot wait and attempt in the broker bound",
        f"slot wait {sid}.timeout_s + attempt {sid}.timeout_s",
        2 * t,
        f"broker bound 2 × {sid}.timeout_s + _BROKER_MARGIN_S",
        2 * t + v["constants"]["broker_margin_s"],
    )


_FAMILIES: dict[str, Callable[..., Check]] = {
    "capability-in-room": _capability_in_room,
    "tool-in-capability": _tool_in_capability,
    "facade-search-in-tool": _facade_search_in_tool,
    "document-server-in-tool": _document_server_in_tool,
    "slot-and-attempt-in-broker": _slot_and_attempt_in_broker,
}


def _limit_to_bound(v: dict, slug: str, tool: str) -> None:
    bound = _tool_bound(v, tool)
    v["manifests"][slug]["limits"]["timeout_s"] = bound[1] if bound else 0.0


def _tool_to(v: dict, tool: str, value: float) -> None:
    v["tools"][tool] = value


# One generic violating change per family, applied to each rule it generates: the value just at
# its bound, which the rule (strict) must refuse.
_FAMILY_MUTATIONS: dict[str, Callable[..., None]] = {
    "capability-in-room": lambda v, slug: v["manifests"][slug]["limits"].update(timeout_s=_room(v)),
    "tool-in-capability": _limit_to_bound,
    "facade-search-in-tool": lambda v, slug, tool: _tool_to(
        v, tool, v["models"]["timeouts"]["embedding"]["query"] + v["models"]["timeouts"]["rerank"]
    ),
    "document-server-in-tool": lambda v, slug, tool: _tool_to(
        v, tool, float(_document_server(v)["timeout_s"])
    ),
    "slot-and-attempt-in-broker": lambda v, sid: v["constants"].update(broker_margin_s=0.0),
}


def generated(values: dict) -> list[str]:
    """The names of the rules the configuration's capabilities, tools and servers must produce."""
    return [":".join((family, *key)) for family in _FAMILIES for key in _keys(values, family)]


def mutations(values: dict) -> dict[str, Callable[[dict], None]]:
    """Every rule's mutation: the fixed rules' (MUTATIONS) and each generated rule's, from its
    family's one mutation."""
    out = dict(MUTATIONS)
    for family, mutate in _FAMILY_MUTATIONS.items():
        for key in _keys(values, family):
            out[":".join((family, *key))] = lambda v, mutate=mutate, key=key: mutate(v, *key)
    return out


# The fixed rules the shipped configuration produces; a missing one fails. The generated ones
# (`generated`) are expected from what the configuration declares.
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
)


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
    "helper-in-shortest-capability": lambda v: v["models"]["timeouts"]["roles"].update(
        router=min(float(m["limits"]["timeout_s"]) for m in v["manifests"].values())
    ),
    "register-in-gateway-call": lambda v: v["docs"].update(register_response_s=62),
    "upload-stall-in-deadline": lambda v: v["gateway"].update(connection_upload_timeout_s=120),
    "keep-alive": lambda v: v.update(agent_keep_alive_s=5.0),
    "keep-alive-dev": lambda v: v.update(agent_keep_alive_dev_s=5.0),
    "sdk-read-margin-used": _sub(
        "mcp/client.py", r"entry\.timeout_s \+ _SDK_READ_MARGIN_S", "entry.timeout_s"
    ),
    "attempt-margin-used": _sub(
        "mcp/client.py",
        r"deadline\.within\(self\.entry\.timeout_s, margin_s=_ATTEMPT_MARGIN_S\)",
        "deadline.within(self.entry.timeout_s)",
    ),
    "sandbox-memory:ingest": lambda v: v["sandbox_workers"].update(ingest=3),
    "sandbox-memory:mcp-rag-ref": lambda v: v["mem_limits_mb"].update({"mcp-rag-ref": 1024}),
}


def verify(values: dict) -> tuple[list[Check], list[str]]:
    """Every check, and the names expected (EXPECTED, and the generated rules the configuration
    declares) that no check produced."""
    found = checks(values)
    names = {check.name for check in found}
    return found, [name for name in (*EXPECTED, *generated(values)) if name not in names]


def self_test(values: dict) -> list[str]:
    """What the self-test found wrong: a rule without a mutation, or a mutation that left its own
    rule holding."""
    every = mutations(values)
    problems = [f"{name}: no mutation" for name in (*EXPECTED, *generated(values)) if name not in every]
    for name, mutate in every.items():
        changed = copy.deepcopy(values)
        mutate(changed)
        own = [check for check in checks(changed) if check.name == name]
        if not own:
            problems.append(f"{name}: the mutation removed the rule instead of failing it")
        elif own[0].holds:
            problems.append(f"{name}: a violation went unnoticed ({own[0].detail})")
    return problems
