"""The router model (README §5.3, ADR-0015, ADR-0139, ADR-0145). Manifest rules decide
first; when they leave the choice open among capabilities that declare
`conversation_attachments`, the router role chooses among those and the fallback
capabilities. It reads the candidates' `routing_hints.description` (adding a capability
changes this list, not this code), the last exchange and the message, through Context
Assembly and LiteLLM like every model call. Its answer is JSON; anything outside the
candidates is ignored, and an unusable answer or a model failure keeps the candidates
the rules found (`router_default`).

A decision is cached in Redis for `cache_ttl_s`, keyed per tenant and per user (ADR-0116
precedent; owner decision S8) by the router prompts' versions, the model alias the router role
resolves to for this principal, the locale, the candidates, the conversation's file names, the
normalized message and the window it saw: one user's decision is never another's, and a new
alias or ceiling never reuses an old answer. The file names and the message reach the
model as delimited data in the user role (`router.request`, ADR-0068).

Phase 06 (ADR-0299): when skills are candidates (a keyword of theirs is in the message), their
slugs, descriptions and when-to-use lines enter `router.request`, and the model also returns
`skill`: one of them or null. The candidates (slug and version) are part of the cache key, and a
cached skill is kept only while it is still a candidate.

Reports PR (ADR-0378): when the turn is a report candidate (a report keyword is in the message, or the
conversation holds a report a follow-up may edit, D3, D4), the request says so, with the current report's
title, and the model also returns `output`: "report" or "text". The candidacy and the title are part of the
cache key. When the conversation holds a report, the same answer says whether the request is an `edit` of
that report (a change to it) or a request on another subject, which starts a new report (ADR-0400); the
composer's report picker asks the same call for that alone."""

import contextlib
import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

from opentelemetry import trace

from app.capabilities.registry import LoadedCapability
from app.context.messages import visible
from app.context.package import AssemblyRequest
from app.errors import TurnError
from app.graph.calls import call_model, prepare_call
from app.graph.deps import TurnContext
from app.graph.router_config import RouterConfig
from app.skills.registry import LoadedSkill
from app.text.arabic import normalize

_JSON = re.compile(r"\{.*\}", re.S)
_LEVELS = {"low", "medium", "high"}


@dataclass(frozen=True)
class RouterDecision:
    capabilities: list[str]
    complexity: str | None
    risk: str | None
    source: str  # model | cache | router_default
    # At most one skill among the turn's skill candidates (phase 06, ADR-0299); None: no skill.
    skill: str | None = None
    # A skill the model named that was not a candidate: dropped, reported as `skill_dropped`.
    dropped_skill: str | None = None
    # The answer's form on a report candidate (ADR-0378): "report" or "text"; None when not asked.
    output: str | None = None
    # With a current report (ADR-0400): whether the request changes it (True) or starts a new one (False).
    edit: bool | None = None


@dataclass(frozen=True)
class OutputCandidate:
    """The turn may be a report (ADR-0378): `current` is the title of the conversation's report, if any;
    `picked`: the composer's picker already chose a report, and only `edit` is asked (ADR-0400)."""

    current: str | None = None
    picked: bool = False


def parse_decision(
    text: str, allowed: Sequence[str], skills: Sequence[str] = (), *, output: bool = False, current: bool = False
) -> RouterDecision | None:
    """The model's JSON `{capabilities, skill, complexity, risk}`. Capabilities outside `allowed`
    are ignored; a `skill` outside `skills` (the candidates) is dropped and reported; any other
    value of `skill` (null, a list, a number) is no skill."""
    match = _JSON.search(text or "")
    if not match:
        return None
    try:
        raw = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("capabilities"), list):
        return None
    chosen: list[str] = []
    for slug in raw["capabilities"]:
        if isinstance(slug, str) and slug in allowed and slug not in chosen:
            chosen.append(slug)
    if not chosen:
        return None
    complexity = raw.get("complexity") if raw.get("complexity") in _LEVELS else None
    risk = raw.get("risk") if raw.get("risk") in _LEVELS else None
    named = raw.get("skill")
    skill = named if isinstance(named, str) and named in skills else None
    dropped = named if isinstance(named, str) and named and skill is None else None
    form = ("report" if raw.get("output") == "report" else "text") if output else None
    # Only an explicit true is an edit: anything else starts a new report (ADR-0400).
    edit = (raw.get("edit") is True) if output and current else None
    return RouterDecision(chosen, complexity, risk, "model", skill=skill, dropped_skill=dropped, output=form, edit=edit)


def _window(state: dict, size: int) -> list[str]:
    history = visible(state.get("messages") or [])[state.get("summary_cursor") or 0 :]
    return [f"{role}:{content}" for role, content in (m for m in history[-size:] if m)] if size else []


def _files(state: dict) -> list[str]:
    """The names of the conversation's indexed files: user data, sent in the user role (ADR-0068)."""
    return [a["name"] for a in state.get("attachments") or [] if a.get("status") == "indexed"]


async def _cache_key(
    ctx: TurnContext,
    state: dict,
    allowed: Sequence[str],
    window: list[str],
    skills: Sequence[LoadedSkill] = (),
    output: OutputCandidate | None = None,
) -> str:
    services = ctx.services
    prompts = [services.prompts.ref(p, state["locale"]) for p in ("router.system", "router.request")]
    decision = await services.resolver.resolve("router", principal=ctx.principal)
    material = json.dumps(
        [
            ctx.principal.tenant_id,
            ctx.principal.user_id,
            services.models_config.tiers[decision.final].alias,
            prompts,
            state["locale"],
            list(allowed),
            _files(state),
            normalize(state["user_message"]),
            window,
            [s.manifest.ref for s in skills],
            None if output is None else ["report", output.current, output.picked],
        ],
        ensure_ascii=False,
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"{services.settings.redis_namespace}:route:{digest}"


async def choose(
    ctx: TurnContext,
    state: dict,
    candidates: Sequence[LoadedCapability],
    fallback: Sequence[LoadedCapability],
    config: RouterConfig,
    skills: Sequence[LoadedSkill] = (),
    output: OutputCandidate | None = None,
) -> RouterDecision:
    """The router model's choice among `candidates` and `fallback`, of at most one skill among `skills`,
    and, for a report candidate, of the answer's form; cached for an hour. An unusable answer or a model
    failure: the candidates, no skill, a text answer."""
    services = ctx.services
    span = trace.get_current_span()
    pool = [*candidates, *(c for c in fallback if c not in candidates)]
    allowed = [c.slug for c in pool]
    skill_slugs = [s.slug for s in skills]
    default = RouterDecision(
        [c.slug for c in candidates], None, None, "router_default", output="text" if output else None,
        edit=False if output and output.current else None,
    )
    window = _window(state, config.window_messages)
    key = await _cache_key(ctx, state, allowed, window, skills, output)
    redis = services.redis
    if redis is not None:
        try:
            cached = await redis.get(key)
        except Exception:  # a cache outage costs a model call, never the route
            cached = None
        if cached:
            raw = json.loads(cached)
            span.set_attribute("route.router.cached", True)
            skill = raw.get("skill") if raw.get("skill") in skill_slugs else None
            form = (raw.get("output") or "text") if output else None
            edit = (raw.get("edit") is True) if output and output.current else None
            return RouterDecision(
                raw["capabilities"], raw.get("complexity"), raw.get("risk"), "cache", skill=skill, output=form, edit=edit
            )
    span.set_attribute("route.router.cached", False)
    locale = state["locale"]
    node = services.assembler.node("router")
    request = AssemblyRequest(
        node="router",
        needs=node.needs,
        system_prompt=node.system_prompt,
        user_prompt=node.request_prompt,
        locale=locale,
        state={k: state.get(k) for k in ("user_message", "messages", "summary_cursor")},
        window_messages=config.window_messages,
        prompt_vars={
            "files": _files(state),
            "message": state["user_message"],
            "capabilities": [
                {"slug": c.slug, "description": getattr(c.manifest.routing_hints.description, locale)}
                for c in pool
            ],
            "skills": [
                {"slug": s.slug, "description": getattr(s.manifest.description, locale)}
                for s in skills
            ],
            "report_candidate": output is not None,
            "current_report": output.current if output else None,
            "report_picked": bool(output and output.picked),
        },
    )
    try:
        result = await call_model(ctx, await prepare_call(ctx, request, role="router"))
    except TurnError as exc:
        if exc.code != "model_unavailable":
            raise
        span.add_event("route.router.unavailable")
        return default
    decision = parse_decision(
        result.text, allowed, skill_slugs, output=output is not None, current=bool(output and output.current)
    )
    if decision is None:
        span.add_event("route.router.unusable_answer")
        return default
    if redis is not None:
        with contextlib.suppress(Exception):
            await redis.set(
                key,
                json.dumps(
                    {
                        "capabilities": decision.capabilities,
                        "complexity": decision.complexity,
                        "risk": decision.risk,
                        "skill": decision.skill,
                        "output": decision.output,
                        "edit": decision.edit,
                    }
                ),
                ex=config.cache_ttl_s,
            )
    return decision
