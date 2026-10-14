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
model as delimited data in the user role (`router.request`, ADR-0068)."""

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
from app.text.arabic import normalize

_JSON = re.compile(r"\{.*\}", re.S)
_LEVELS = {"low", "medium", "high"}


@dataclass(frozen=True)
class RouterDecision:
    capabilities: list[str]
    complexity: str | None
    risk: str | None
    source: str  # model | cache | router_default


def parse_decision(text: str, allowed: Sequence[str]) -> RouterDecision | None:
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
    return RouterDecision(chosen, complexity, risk, "model")


def _window(state: dict, size: int) -> list[str]:
    history = visible(state.get("messages") or [])[state.get("summary_cursor") or 0 :]
    return [f"{role}:{content}" for role, content in (m for m in history[-size:] if m)] if size else []


def _files(state: dict) -> list[str]:
    """The names of the conversation's indexed files: user data, sent in the user role (ADR-0068)."""
    return [a["name"] for a in state.get("attachments") or [] if a.get("status") == "indexed"]


async def _cache_key(ctx: TurnContext, state: dict, allowed: Sequence[str], window: list[str]) -> str:
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
) -> RouterDecision:
    """The router model's choice among `candidates` and `fallback`, cached for an hour."""
    services = ctx.services
    span = trace.get_current_span()
    pool = [*candidates, *(c for c in fallback if c not in candidates)]
    allowed = [c.slug for c in pool]
    default = RouterDecision([c.slug for c in candidates], None, None, "router_default")
    window = _window(state, config.window_messages)
    key = await _cache_key(ctx, state, allowed, window)
    redis = services.redis
    if redis is not None:
        try:
            cached = await redis.get(key)
        except Exception:  # a cache outage costs a model call, never the route
            cached = None
        if cached:
            raw = json.loads(cached)
            span.set_attribute("route.router.cached", True)
            return RouterDecision(raw["capabilities"], raw.get("complexity"), raw.get("risk"), "cache")
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
        },
    )
    try:
        result = await call_model(ctx, await prepare_call(ctx, request, role="router"))
    except TurnError as exc:
        if exc.code != "model_unavailable":
            raise
        span.add_event("route.router.unavailable")
        return default
    decision = parse_decision(result.text, allowed)
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
                    }
                ),
                ex=config.cache_ttl_s,
            )
    return decision
