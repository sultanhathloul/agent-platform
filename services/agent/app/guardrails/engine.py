"""Guardrail policy engine (README §10).

One call per checkpoint: `evaluate(checkpoint, payload, principal=..., context=...)`.

1. Applicable policies: enabled, naming the checkpoint, whose scope is one of the
   call's scopes (`global`, the principal's tenant, the turn's KBs, the tools or
   capabilities involved) and, at DATA, guarding the payload's target (A4).
2. Every applicable policy's checker runs, concurrently, each in its own
   `guard.check` span (policy id@version, action, latency). A checker that fails
   applies the policy's `on_error` (D8): `closed` makes the outcome unavailable and
   the call site ends the turn with `guard_unavailable`; `open` allows and logs.
3. Within a policy group (default: the policy's own id) a policy overrides another only
   when its scope is contained by the other's, and only `global` contains another scope
   (ADR-0108). The override is per span: a narrower policy that matched cancels the
   wider one's spans that its own spans cover (a presidio exception only for its
   configured entity types); the wider policy keeps its action on every other span, and
   is overridden whole only when none is left. A narrower policy that matched nothing
   overrides nothing. Policies whose scopes do not contain each other (tenant, role, kb,
   tool) all stand. Across everything that stands the most restrictive action wins
   (block > rewrite > redact > require_approval > warn > log_only > allow, ADR-0105),
   and every standing redaction's spans are replaced with `[REDACTED:{type}]`.
   A call that executes only some actions (GuardContext.actions) still resolves every
   group that holds one of them in full, so both answer modes resolve alike.
4. TOOL_LIST filters: each capability is evaluated with its own `tool:` scope, and a
   blocked one is removed. MODEL_TIER lowers the request's cap to the ceiling of
   every matching `tier_ceiling` policy (D2). POST_TOOL runs sanitize_untrusted first,
   always (README §10.3).

The engine holds no capability-specific logic (README §18.4) and records nothing
itself: it returns the events, and call sites put them in state, the stream, the
trace and the audit delta.
"""

import asyncio
import json
import time
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from app.guardrails.checkers.base import CheckContext, Checker
from app.guardrails.checkpoints import Checkpoint
from app.guardrails.sanitize import Sanitizer
from app.guardrails.schema import (
    ACTION_RANK,
    WHOLE_TEXT_CHECKERS,
    CheckResult,
    DataPayload,
    EvaluatedPolicy,
    GuardContext,
    GuardEventRecord,
    GuardOutcome,
    Policy,
    Span,
)
from app.identity import Principal
from app.observability.otel import tracer

NO_CONTEXT = GuardContext()
_SEVERITY = {"low": 0, "medium": 1, "high": 2, "critical": 3}


class PolicySource(Protocol):
    def active(self) -> Sequence[Policy]: ...


class _NoPolicies:
    def active(self) -> Sequence[Policy]:
        return ()


def redact(text: str, spans: Sequence[Span]) -> str:
    """Replaces every span with `[REDACTED:{type}]`; overlapping spans merge, typed by
    the first one. A marker glued to a letter or digit gets a space on that side, so
    `رقمه0501234567` never becomes one mixed-script token (ADR-0060)."""
    merged: list[tuple[int, int, str]] = []
    for span in sorted(spans, key=lambda s: (s.start, -s.end)):
        if merged and span.start < merged[-1][1]:
            start, end, entity = merged[-1]
            merged[-1] = (start, max(end, span.end), entity)
        else:
            merged.append((span.start, span.end, span.entity))
    for start, end, entity in reversed(merged):
        before = " " if start > 0 and text[start - 1].isalnum() else ""
        after = " " if end < len(text) and text[end].isalnum() else ""
        text = f"{text[:start]}{before}[REDACTED:{entity}]{after}{text[end:]}"
    return text


class GuardEngine:
    def __init__(
        self,
        policies: PolicySource | None = None,
        checkers: Mapping[str, Checker] | None = None,
        *,
        tier_order: Sequence[str] = ("fast", "standard", "heavy"),
        sanitizer: Sanitizer | None = None,
    ):
        self.policies = policies or _NoPolicies()
        self.checkers = dict(checkers or {})
        self._rank = {tier: rank for rank, tier in enumerate(tier_order)}
        self.sanitizer = sanitizer

    # ------------------------------------------------------------------ queries

    def applicable(self, checkpoint: Checkpoint, scopes: set[str], target: str | None = None) -> list[Policy]:
        return [
            p
            for p in self.policies.active()
            if p.enabled and checkpoint in p.checkpoints and p.scope in scopes and p.guards_target(target)
        ]

    def requires_buffering(self, principal: Principal, context: GuardContext = NO_CONTEXT) -> bool:
        """D3: the answer is buffered whole only when an enabled OUTPUT policy that can
        block or rewrite applies to the principal's scope. An OUTPUT redaction runs per
        sentence on the stream, like DATA (ADR-0106)."""
        return any(
            p.action in ("block", "rewrite")
            for p in self.applicable(Checkpoint.OUTPUT, context.scopes(principal))
        )

    # ------------------------------------------------------------------ evaluation

    async def evaluate(
        self,
        checkpoint: Checkpoint,
        payload: Any,
        *,
        principal: Principal | None = None,
        context: GuardContext = NO_CONTEXT,
    ) -> GuardOutcome:
        principal = principal or Principal(user_id="anonymous")
        with tracer.start_as_current_span("guard.evaluate") as span:
            span.set_attribute("guard.checkpoint", checkpoint.value)
            if checkpoint == Checkpoint.TOOL_LIST:
                outcome = await self._tool_list(list(payload), principal, context)
            else:
                outcome = await self._evaluate(checkpoint, payload, principal, context)
            span.set_attribute("guard.action", outcome.action)
            span.set_attribute("guard.policies_evaluated", sum(1 for e in outcome.events if e.policy_id))
            if outcome.unavailable:
                span.set_attribute("guard.unavailable", outcome.unavailable)
            return outcome

    async def _tool_list(self, slugs: list[str], principal: Principal, context: GuardContext) -> GuardOutcome:
        kept, events, unavailable = [], [], None
        for slug in slugs:
            scoped = GuardContext(
                kbs=context.kbs,
                tools=(*context.tools, slug),
                locale=context.locale,
                turn=context.turn,
                only=context.only,
                draft=context.draft,
                actions=context.actions,
            )
            outcome = await self._evaluate(Checkpoint.TOOL_LIST, slug, principal, scoped)
            events += [
                e.model_copy(update={"detail": f"{slug}: {e.detail or ''}".strip()}) for e in outcome.events
            ]
            unavailable = unavailable or outcome.unavailable
            if not outcome.blocked:
                kept.append(slug)
        return GuardOutcome(action="allow", payload=kept, events=events, unavailable=unavailable)

    async def _evaluate(
        self, checkpoint: Checkpoint, payload: Any, principal: Principal, context: GuardContext
    ) -> GuardOutcome:
        target = payload.target if isinstance(payload, DataPayload) else None
        notes: list[GuardEventRecord] = []
        if checkpoint == Checkpoint.POST_TOOL and self.sanitizer is not None:
            payload, note = self._sanitize(payload, context.locale)
            if note:
                notes.append(note)

        if context.only is not None:
            # The test panel: this one policy, on or off, but still only for the targets it guards.
            policies = [context.only] if context.only.guards_target(target) else []
        else:
            policies = self.applicable(checkpoint, context.scopes(principal), target)
            if context.actions is not None:
                # Whole groups: a group takes part when one of its policies may act here.
                groups = {p.effective_group for p in policies if p.action in context.actions}
                policies = [p for p in policies if p.effective_group in groups]
        if not policies:
            return GuardOutcome(
                payload=payload,
                events=[*notes, GuardEventRecord(checkpoint=checkpoint, action="allow", detail="no policy")],
            )

        subject = _subject(checkpoint, payload)
        evaluated = await asyncio.gather(
            *(self._check(policy, checkpoint, subject, principal, context, target) for policy in policies)
        )
        return self._combine(checkpoint, payload, list(evaluated), notes, target, context.actions)

    def _sanitize(self, payload: Any, locale: str) -> tuple[Any, GuardEventRecord | None]:
        result = self.sanitizer.sanitize(payload.content, locale)
        payload = payload.model_copy(update={"content": result.text})
        if not (result.removed_instructions or result.truncated):
            return payload, None
        detail = f"sanitize_untrusted: {result.removed_instructions} instruction-like string(s) removed" + (
            ", truncated" if result.truncated else ""
        )
        return payload, GuardEventRecord(checkpoint=Checkpoint.POST_TOOL, action="log_only", detail=detail)

    async def _check(
        self,
        policy: Policy,
        checkpoint: Checkpoint,
        subject: Any,
        principal: Principal,
        context: GuardContext,
        target: str | None,
    ) -> EvaluatedPolicy:
        started = time.monotonic()
        with tracer.start_as_current_span("guard.check") as span:
            span.set_attribute("guard.checkpoint", checkpoint.value)
            span.set_attribute("guard.policy", policy.ref)
            span.set_attribute("guard.checker", policy.checker)
            span.set_attribute("guard.scope", policy.scope)
            if target:
                span.set_attribute("guard.target", target)
            ctx = CheckContext(
                principal=principal,
                policy=policy,
                locale=context.locale,
                turn=context.turn,
                target=target,
                panel=context.only is not None,
                draft=context.draft,
            )
            try:
                checker = self.checkers[policy.checker]
                result: CheckResult | None = await checker.check(subject, policy.typed_config(), ctx)
                error = None
            except Exception as exc:  # any failure is the policy's on_error, never a crash
                result, error = None, type(exc).__name__
            latency = int((time.monotonic() - started) * 1000)
            span.set_attribute("guard.latency_ms", latency)
            span.set_attribute("guard.matched", bool(result and result.matched))
            if error:
                span.set_attribute("guard.error", error)
                span.set_attribute("guard.on_error", policy.effective_on_error)
            span.set_attribute("guard.action", _event_action(policy, result, error))
        return EvaluatedPolicy(policy=policy, result=result, error=error, latency_ms=latency)

    def _combine(
        self,
        checkpoint: Checkpoint,
        payload: Any,
        evaluated: list[EvaluatedPolicy],
        notes: list[GuardEventRecord],
        target: str | None,
        actions: frozenset[str] | None = None,
    ) -> GuardOutcome:
        unavailable = next(
            (e.policy.ref for e in evaluated if e.error and e.policy.effective_on_error == "closed"), None
        )
        matched = [e for e in evaluated if e.result and e.result.matched]
        for e in matched:
            e.spans = list(e.result.spans)
        for group in {e.policy.effective_group for e in matched}:
            _resolve_group([e for e in matched if e.policy.effective_group == group])
        standing = [e for e in matched if e.overridden_by is None]
        if actions is not None:
            # Only these actions execute; the rest of each group weighed in its resolution.
            standing = [e for e in standing if e.policy.action in actions]
            acting = [e for e in evaluated if e.policy.action in actions]
            overriders = {ref for e in acting for ref in [e.overridden_by, *e.partly_overridden_by] if ref}
            evaluated = [e for e in evaluated if e in acting or e.policy.ref in overriders]

        action = max((e.policy.action for e in standing), key=ACTION_RANK.__getitem__, default="allow")
        if unavailable:
            action = "block"
        outcome = GuardOutcome(
            action=action,
            payload=payload,
            unavailable=unavailable,
            results={e.policy.ref: e.result for e in evaluated if e.result is not None},
        )
        deciding = [e for e in standing if e.policy.action == action]
        if deciding:
            decider = max(
                deciding, key=lambda e: (_SEVERITY[e.policy.severity], e.policy.parsed_scope.precedence)
            )
            outcome.decided_by = decider.policy.ref
            if action == "block":
                outcome.message_ar, outcome.message_en = decider.policy.message_ar, decider.policy.message_en

        if checkpoint == Checkpoint.MODEL_TIER:
            outcome.payload = self._ceiling(payload, standing)
        elif action != "block":
            spans = [s for e in standing if e.policy.action == "redact" for s in e.spans]
            if spans:
                outcome.payload = _with_text(
                    checkpoint, payload, redact(_subject(checkpoint, payload), spans)
                )

        events = list(notes)
        for e in evaluated:
            if not (e.error or (e.result and e.result.matched)):
                continue
            events.append(
                GuardEventRecord(
                    checkpoint=checkpoint,
                    policy_id=e.policy.id,
                    policy_version=e.policy.version,
                    action=_event_action(e.policy, e.result, e.error, overridden=e.overridden_by is not None),
                    detail=_detail(e),
                    target=target,
                    entities=sorted({s.entity for s in (e.result.spans if e.overridden_by else e.spans)})
                    if e.result
                    else [],
                    latency_ms=e.latency_ms,
                )
            )
        if len(events) == len(notes):
            events.append(
                GuardEventRecord(
                    checkpoint=checkpoint,
                    action="allow",
                    detail=f"{len(evaluated)} policies, none matched",
                    target=target,
                )
            )
        outcome.events = events
        return outcome

    def _ceiling(self, payload: dict, winners: list[EvaluatedPolicy]) -> dict:
        cap = payload["cap"]
        for e in winners:
            ceiling = e.policy.typed_config().max_tier
            if self._rank[ceiling] < self._rank[cap]:
                cap = ceiling
        if cap == payload["cap"]:
            return payload
        return {**payload, "cap": cap, "capped_by": ",".join(e.policy.ref for e in winners)}


def _resolve_group(members: list[EvaluatedPolicy]) -> None:
    """ADR-0108, ADR-0115, inside one group of matched policies: each policy cancels, in every
    policy whose scope contains its own, the spans it covers. A whole-text checker
    (WHOLE_TEXT_CHECKERS) cancels only a wider whole-text verdict, never span-based actions;
    span matches never cancel a wider whole-text verdict. A policy left with nothing is
    overridden."""
    for wide in members:
        cancelled_by: list[str] = []
        wide_whole = wide.policy.checker in WHOLE_TEXT_CHECKERS
        for narrow in members:
            if narrow is wide or not wide.policy.parsed_scope.contains(narrow.policy.parsed_scope):
                continue
            before = len(wide.spans)
            narrow_whole = narrow.policy.checker in WHOLE_TEXT_CHECKERS
            if narrow_whole and wide_whole:
                wide.spans, whole = [], True
            elif narrow_whole or wide_whole:
                continue  # a whole-text verdict and span matches never cancel each other
            else:
                wide.spans, whole = [s for s in wide.spans if not _covers(narrow, s)], False
            if whole or len(wide.spans) < before:
                cancelled_by.append(narrow.policy.ref)
        if not cancelled_by:
            continue
        if wide.spans:
            wide.partly_overridden_by = cancelled_by
        else:
            wide.overridden_by = cancelled_by[0]


def _covers(narrow: EvaluatedPolicy, span: Span) -> bool:
    """Whether a narrower policy's match cancels this span of a wider one: its own spans
    overlap it and, for presidio, the span is of an entity type the narrower one names."""
    entities = set(narrow.policy.config.get("entities") or ())
    if narrow.policy.checker == "presidio" and span.entity not in entities:
        return False
    return any(s.start < span.end and span.start < s.end for s in narrow.result.spans)


def _event_action(
    policy: Policy, result: CheckResult | None, error: str | None, *, overridden: bool = False
) -> str:
    if error:
        return "block" if policy.effective_on_error == "closed" else "log_only"
    if overridden or not (result and result.matched):
        return "allow"
    return policy.action


def _detail(e: EvaluatedPolicy) -> str:
    if e.error:
        return f"checker error ({e.error}); on_error={e.policy.effective_on_error}"
    if e.overridden_by:
        return f"overridden by {e.overridden_by}"
    if e.partly_overridden_by:
        return f"{e.result.explanation}; partly overridden by {', '.join(e.partly_overridden_by)}"
    return e.result.explanation


def _subject(checkpoint: Checkpoint, payload: Any) -> Any:
    """What the checkers read at each checkpoint."""
    if checkpoint == Checkpoint.MODEL_TIER:
        return payload
    if isinstance(payload, DataPayload):
        return payload.text
    if isinstance(payload, str):
        return payload
    if checkpoint == Checkpoint.INPUT:
        return payload["message"]
    if checkpoint == Checkpoint.OUTPUT:
        return payload["answer"] or ""
    if checkpoint == Checkpoint.PRE_TOOL:
        return f"{payload['tool']} {json.dumps(payload['args'], ensure_ascii=False, sort_keys=True)}"
    if checkpoint == Checkpoint.POST_TOOL:
        return payload.content
    raise TypeError(f"no text for {checkpoint} payload {type(payload).__name__}")


def _with_text(checkpoint: Checkpoint, payload: Any, text: str) -> Any:
    if isinstance(payload, DataPayload):
        return payload.model_copy(update={"text": text})
    if isinstance(payload, str):
        return text
    if checkpoint == Checkpoint.INPUT:
        return {**payload, "message": text}
    if checkpoint == Checkpoint.OUTPUT:
        return {**payload, "answer": text}
    if checkpoint == Checkpoint.POST_TOOL:
        return payload.model_copy(update={"content": text})
    raise TypeError(f"cannot redact a {checkpoint} payload")
