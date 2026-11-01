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
    """One rule's result. A nested pair also carries its two sides, so the settings page can show
    the timeout tree (ADR-0278): `unit` is `s` for a timeout pair, `MiB` for memory, `count` for a
    count, and None for a rule on the code (a margin it must use) or a range."""

    name: str
    row: str
    holds: bool
    detail: str
    inner: str | None = None
    inner_v: float | None = None
    outer: str | None = None
    outer_v: float | None = None
    strict: bool = True
    unit: str | None = None


def _pair(
    name: str,
    row: str,
    inner: str,
    inner_v: float,
    outer: str,
    outer_v: float,
    *,
    strict=True,
    unit: str = "s",
) -> Check:
    holds = inner_v < outer_v if strict else inner_v <= outer_v
    sign = "<" if strict else "≤"
    return Check(
        name,
        row,
        holds,
        f"{inner} {inner_v:g} {sign} {outer} {outer_v:g}",
        inner,
        float(inner_v),
        outer,
        float(outer_v),
        strict,
        unit,
    )


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
            unit="count",
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
    # Deadline profiles (reports PR, ADR-0379): each profile's turn in the gateway's relay, its answer
    # in its turn with the post-answer reserve kept, and its role's model call in its answer.
    for name, profile in sorted((turn.get("profiles") or {}).items()):
        out += [
            _pair(
                f"profile-turn-in-relay:{name}",
                f"Chat ({name} turns): the agent's turn in the gateway's relay",
                f"profiles.{name}.deadline_s",
                profile["deadline_s"],
                f"profiles.{name}.deadline_s + gateway_margin_s",
                profile["deadline_s"] + turn["gateway_margin_s"],
            ),
            _pair(
                f"profile-answer-in-turn:{name}",
                f"Chat ({name} turns): the answer in the turn, the post-answer reserve kept",
                f"profiles.{name}.answer_s",
                profile["answer_s"],
                f"profiles.{name}.deadline_s − post_answer_reserve_s",
                profile["deadline_s"] - turn["post_answer_reserve_s"],
                strict=False,
            ),
            _pair(
                f"profile-role-in-answer:{name}",
                f"Chat ({name} turns): the {profile['role']} call in the answer",
                f"timeouts.roles.{profile['role']}",
                roles[profile["role"]],
                f"profiles.{name}.answer_s",
                profile["answer_s"],
                strict=False,
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
                unit="MiB",
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


# The bounds a pair's outer side derives from another bound (ADR-0286): the turn's room before the
# answer lies in the turn; the longest and shortest of several are that one; the slot-and-attempt
# rule names the broker bound with its constant, the tool rules with "margin".
_BROKER_OUTER = re.compile(r"broker bound 2 × (\S+)\.timeout_s \+ _BROKER_MARGIN_S")
_PER_CAPABILITY = (
    "capability-in-room",
    "tool-in-capability",
    "facade-search-in-tool",
    "document-server-in-tool",
)


def _capability_of(name: str) -> str | None:
    family, _, rest = name.partition(":")
    return rest.split(":", 1)[0] if family in _PER_CAPABILITY else None


def parents(v: dict, found: list[Check]) -> dict[str, list[str]]:
    """The timeout tree (ADR-0286): for each timeout pair, the pairs whose inner bound is its outer
    one, so a bound is shown nested under the bound that encloses it (turn → capability → tool →
    MCP server; the gateway's relay → turn → answer). A pair of one capability nests only under a
    pair of the same capability; an MCP server's pair under each tool on it. A pair with no parent
    is a root."""
    pairs = [c for c in found if c.unit == "s" and c.inner is not None]
    roles, manifests = v["models"]["timeouts"]["roles"], v["manifests"]
    derived = {
        "deadline_s − answer_reserve_s": "deadline_s",
        "longest role ceiling": f"timeouts.roles.{max(roles, key=lambda r: roles[r])}",
        "shortest capability limits.timeout_s": (
            f"{min(manifests, key=lambda s: manifests[s]['limits']['timeout_s'])} limits.timeout_s"
        ),
    }
    out: dict[str, list[str]] = {}
    for pair in pairs:
        outer = derived.get(pair.outer, pair.outer)
        outer = _BROKER_OUTER.sub(r"broker bound 2 × \1.timeout_s + margin", outer)
        mine = _capability_of(pair.name)
        out[pair.name] = [
            q.name
            for q in pairs
            if q is not pair and q.inner == outer and (mine is None or _capability_of(q.name) in (None, mine))
        ]
    return out


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
    "profile-turn-in-relay:report",
    "profile-answer-in-turn:report",
    "profile-role-in-answer:report",
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
    "profile-turn-in-relay:report": lambda v: v["turn"].update(gateway_margin_s=0),
    "profile-answer-in-turn:report": lambda v: v["turn"]["profiles"]["report"].update(
        answer_s=v["turn"]["profiles"]["report"]["deadline_s"]
    ),
    "profile-role-in-answer:report": lambda v: v["models"]["timeouts"]["roles"].update(
        report_writer=v["turn"]["profiles"]["report"]["answer_s"] + 1
    ),
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


# ------------------------------------------------------------------ settings an administrator may override
#
# The platform settings page (owner decision 2026-10-22, ADR-0276): the files stay the defaults, and
# an override is checked here before it is saved, with the same rules as the files (ADR-0278). Each
# editable setting has a range; every rule above then runs on the values the overrides produce.
# `values` is the dict `checks` reads, with "platform": {"lock_pool_size": N} for the ranges that the
# platform's capacity bounds.

TENANT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
HELPER_ROLES = ("router", "summarizer", "judge", "extractor")
REASONING_ROLES = ("reasoning", "reflection")


@dataclass(frozen=True)
class Setting:
    """One editable value. `read` gives its default from the files' values, `write` sets it into
    a copy of them; `maximum` is computed from the files' values (a server's max_concurrency may
    only be lowered, so its maximum is the file's). `nullable`: None is a value (no limit of its
    own). `per_tenant`: the value is a map from tenant id to a count."""

    key: str
    group: str
    source: str
    unit: str
    integer: bool
    minimum: float
    maximum: Callable[[dict], float]
    read: Callable[[dict], object]
    write: Callable[[dict, object], None]
    nullable: bool = False
    per_tenant: bool = False


def _at(*path: str) -> tuple[Callable[[dict], object], Callable[[dict, object], None]]:
    def read(v: dict) -> object:
        node = v
        for part in path:
            node = node[part]
        return node

    def write(v: dict, value: object) -> None:
        node = v
        for part in path[:-1]:
            node = node[part]
        node[path[-1]] = value

    return read, write


def _roles(roles: tuple[str, ...]) -> tuple[Callable[[dict], object], Callable[[dict, object], None]]:
    """A ceiling shared by several roles: its default is their longest, and it sets them all."""

    def read(v: dict) -> object:
        return max(float(v["models"]["timeouts"]["roles"][r]) for r in roles)

    def write(v: dict, value: object) -> None:
        for r in roles:
            v["models"]["timeouts"]["roles"][r] = value

    return read, write


def _fixed(value: float) -> Callable[[dict], float]:
    return lambda v: value


def _capacity(v: dict) -> float:
    return float(v["platform"]["lock_pool_size"])


def _uploads_total(v: dict) -> float:
    return float(v["docs"]["limits"]["max_concurrent_uploads_total"])


def _setting(key, group, source, unit, integer, minimum, maximum, at, **flags) -> Setting:
    read, write = at
    return Setting(key, group, source, unit, integer, minimum, maximum, read, write, **flags)


_FIXED_SETTINGS: tuple[Setting, ...] = (
    _setting(
        "turn.per_user_max",
        "turns",
        "config/turn.yaml per_user_max",
        "count",
        True,
        1,
        _capacity,
        _at("turn", "per_user_max"),
    ),
    _setting(
        "turn.per_tenant_max",
        "turns",
        "config/turn.yaml per_tenant_max",
        "count",
        True,
        1,
        _capacity,
        _at("turn", "per_tenant_max"),
        nullable=True,
    ),
    _setting(
        "turn.per_tenant_overrides",
        "turns",
        "config/turn.yaml per_tenant_overrides",
        "count",
        True,
        1,
        _capacity,
        _at("turn", "per_tenant_overrides"),
        per_tenant=True,
    ),
    _setting(
        "turn.deadline_s",
        "turns",
        "config/turn.yaml deadline_s",
        "s",
        False,
        1,
        _fixed(600),
        _at("turn", "deadline_s"),
    ),
    _setting(
        "turn.report_deadline_s",
        "turns",
        "config/turn.yaml profiles.report.deadline_s",
        "s",
        False,
        1,
        _fixed(900),
        _at("turn", "profiles", "report", "deadline_s"),
    ),
    _setting(
        "turn.report_answer_s",
        "turns",
        "config/turn.yaml profiles.report.answer_s",
        "s",
        False,
        1,
        _fixed(900),
        _at("turn", "profiles", "report", "answer_s"),
    ),
    _setting(
        "turn.answer_reserve_s",
        "turns",
        "config/turn.yaml answer_reserve_s",
        "s",
        False,
        1,
        _fixed(600),
        _at("turn", "answer_reserve_s"),
    ),
    _setting(
        "turn.post_answer_reserve_s",
        "turns",
        "config/turn.yaml post_answer_reserve_s",
        "s",
        False,
        1,
        _fixed(600),
        _at("turn", "post_answer_reserve_s"),
    ),
    _setting(
        "turn.evidence_fetch_s",
        "turns",
        "config/turn.yaml evidence_fetch_s",
        "s",
        False,
        1,
        _fixed(120),
        _at("turn", "evidence_fetch_s"),
    ),
    _setting(
        "models.helper_s",
        "models",
        "config/models.yaml timeouts.roles (router, summarizer, judge, extractor)",
        "s",
        False,
        1,
        _fixed(120),
        _roles(HELPER_ROLES),
    ),
    _setting(
        "models.reasoning_s",
        "models",
        "config/models.yaml timeouts.roles (reasoning, reflection)",
        "s",
        False,
        1,
        _fixed(600),
        _roles(REASONING_ROLES),
    ),
    _setting(
        "models.report_writer_s",
        "models",
        "config/models.yaml timeouts.roles.report_writer",
        "s",
        False,
        1,
        _fixed(900),
        _roles(("report_writer",)),
    ),
    _setting(
        "models.embed_query_s",
        "models",
        "config/models.yaml timeouts.embedding.query",
        "s",
        False,
        1,
        _fixed(120),
        _at("models", "timeouts", "embedding", "query"),
    ),
    _setting(
        "models.embed_batch_s",
        "models",
        "config/models.yaml timeouts.embedding.ingest",
        "s",
        False,
        1,
        _fixed(600),
        _at("models", "timeouts", "embedding", "ingest"),
    ),
    _setting(
        "models.rerank_s",
        "models",
        "config/models.yaml timeouts.rerank",
        "s",
        False,
        1,
        _fixed(120),
        _at("models", "timeouts", "rerank"),
    ),
    _setting(
        "docs.max_concurrent_uploads",
        "uploads",
        "config/docs.yaml limits.max_concurrent_uploads",
        "count",
        True,
        1,
        _uploads_total,
        _at("docs", "limits", "max_concurrent_uploads"),
    ),
    _setting(
        "docs.max_concurrent_uploads_per_tenant",
        "uploads",
        "config/docs.yaml limits.max_concurrent_uploads_per_tenant",
        "count",
        True,
        1,
        _uploads_total,
        _at("docs", "limits", "max_concurrent_uploads_per_tenant"),
        nullable=True,
    ),
    _setting(
        "docs.tenant_concurrent_uploads",
        "uploads",
        "config/docs.yaml limits.tenant_concurrent_uploads",
        "count",
        True,
        1,
        _uploads_total,
        _at("docs", "limits", "tenant_concurrent_uploads"),
        per_tenant=True,
    ),
    _setting(
        "docs.uploads_per_minute",
        "uploads",
        "config/docs.yaml limits.uploads_per_minute (UPLOADS_PER_MINUTE in .env overrides it)",
        "per_minute",
        True,
        1,
        _fixed(1000),
        _at("docs", "limits", "uploads_per_minute"),
    ),
    _setting(
        "docs.register_response_s",
        "uploads",
        "config/docs.yaml register_response_s",
        "s",
        False,
        1,
        _fixed(55),
        _at("docs", "register_response_s"),
    ),
)


def _server_setting(index: int, server: dict) -> Setting:
    file_value = float(server["max_concurrency"])
    return _setting(
        f"mcp.{server['id']}.max_concurrency",
        "mcp",
        f"config/mcp.yaml servers[{server['id']}].max_concurrency",
        "count",
        True,
        1,
        _fixed(file_value),  # lowered only, never above the file's (the ICD figure)
        _at("mcp", "servers", index, "max_concurrency"),
    )


def editable(values: dict) -> list[Setting]:
    """Every value an administrator may override: the fixed ones and one per MCP server."""
    return [*_FIXED_SETTINGS, *(_server_setting(i, s) for i, s in enumerate(values["mcp"]["servers"]))]


def _problem(
    key: str, reason: str, detail: str, value: float | None = None, maximum: float | None = None
) -> Check:
    """A failed range check: `row` is the reason (required, number, integer, range, tenant,
    unknown, map), `inner_v` the value, `outer_v` the maximum."""
    family = "unknown" if reason == "unknown" else "range"
    return Check(f"{family}:{key}", reason, False, detail, inner_v=value, outer_v=maximum)


def _range(setting: Setting, maximum: float, value: object, where: str) -> Check | None:
    """None when `value` fits the setting; otherwise the failed range check."""
    key = setting.key
    if value is None:
        return None if setting.nullable else _problem(key, "required", f"{where}: a value is required")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _problem(key, "number", f"{where}: not a number")
    if setting.integer and float(value) != int(value):
        return _problem(key, "integer", f"{where}: {value:g} is not a whole number", float(value), maximum)
    if not setting.minimum <= float(value) <= maximum:
        return _problem(
            key,
            "range",
            f"{where}: {value:g} outside {setting.minimum:g}..{maximum:g}",
            float(value),
            maximum,
        )
    return None


def range_checks(values: dict, overrides: dict) -> list[Check]:
    """The overrides against their settings' ranges, computed on the files' values: one failed
    Check per problem; an empty list when every override fits."""
    known = {s.key: s for s in editable(values)}
    out: list[Check] = []
    for key, value in sorted(overrides.items()):
        setting = known.get(key)
        if setting is None:
            out.append(_problem(key, "unknown", f"{key}: not an editable setting"))
            continue
        maximum = setting.maximum(values)
        if setting.per_tenant:
            if not isinstance(value, dict):
                out.append(_problem(key, "map", f"{key}: a map from tenant id to a count"))
                continue
            for tenant, count in sorted(value.items()):
                if not isinstance(tenant, str) or not TENANT_ID.match(tenant):
                    out.append(_problem(key, "tenant", f"{key}: {tenant!r} is not a tenant id"))
                elif count is None:
                    out.append(_problem(key, "required", f"{key}[{tenant}]: a value is required"))
                elif problem := _range(setting, maximum, count, f"{key}[{tenant}]"):
                    out.append(problem)
            continue
        if problem := _range(setting, maximum, value, key):
            out.append(problem)
    return out


def apply(values: dict, overrides: dict) -> dict:
    """A copy of `values` with `overrides` written in (each known key; the caller checks ranges)."""
    out = copy.deepcopy(values)
    known = {s.key: s for s in editable(values)}
    for key, value in overrides.items():
        if key in known:
            known[key].write(out, copy.deepcopy(value))
    return out


def defaults(values: dict) -> dict[str, object]:
    """Every editable setting's default, from the files' values."""
    return {s.key: copy.deepcopy(s.read(values)) for s in editable(values)}


def evaluate(values: dict, overrides: dict) -> tuple[dict, list[Check]]:
    """What a set of overrides would produce: the values with the overrides in, and every problem
    with it (ranges first, then each rule of `checks` that fails, then each expected rule that went
    missing). An empty list means the overrides may be saved."""
    problems = range_checks(values, overrides)
    if problems:
        return copy.deepcopy(values), problems
    effective = apply(values, overrides)
    found, missing = verify(effective)
    problems += [check for check in found if not check.holds]
    problems += [Check(name, name, False, f"{name}: expected, but no such rule") for name in missing]
    return effective, problems
