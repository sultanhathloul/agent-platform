"""`llm_judge` (README §10.2): the primary topic filter. The `judge` role (pinned to
the fast tier, temperature 0) reads the policy's topic description and examples
and the text, through Context Assembly and models.call (CLAUDE.md rule 6), with
the versioned prompts `guardrails.judge.system` and `guardrails.judge.request`,
and answers JSON {violates, confidence, category}. The prompt locale follows the
text's script. Verdicts are cached in Redis for 24 hours (I4), keyed by a hash of
the principal's tenant, the policy's id@version, the judge prompts' id@version and the
model alias the call resolves to, and the normalized text (ADR-0110, ADR-0116): a policy
edit, a prompt edit or a new alias never reuses an old verdict, and one tenant never
reads a verdict another tenant's text produced. The admin test panel never reads or
writes the cache, for a stored policy and a draft alike: every panel test calls the
judge (ADR-0117). A judge call on a memory candidate or a summary is content-free in traces (D5, ADR-0110).
The judge call's tokens are the check's `usage`, never the turn's client usage (ADR-0118).
A malformed answer is a CheckerError: the policy's `on_error` decides (open by
default, D8)."""

import hashlib
import json
import logging
import re
from typing import Any, NamedTuple, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.guardrails.checkers.base import CheckContext, CheckerError
from app.guardrails.schema import CheckResult, CheckUsage, LlmJudgeConfig
from app.identity import Principal
from app.text.arabic import normalize

log = logging.getLogger(__name__)

_OBJECT = re.compile(r"\{.*\}", re.S)
CACHE_TTL_S = 24 * 3600


class Verdict(BaseModel):
    model_config = ConfigDict(extra="ignore")

    violates: bool
    confidence: float = Field(ge=0.0, le=1.0)
    category: str = ""


# DATA targets whose text must never reach a trace (D5).
CONTENT_FREE_TARGETS = frozenset({"memory_candidate", "summary"})


class JudgeAnswer(NamedTuple):
    text: str
    usage: CheckUsage


class JudgeModel(Protocol):
    """Runs the judge prompt through Context Assembly and LiteLLM (app/graph/judge.py)."""

    async def identity(self, *, principal: Principal, locale: str) -> str:
        """What else a verdict depends on: the judge prompts' id@version and the model alias."""
        ...

    async def judge(
        self,
        *,
        principal: Principal,
        locale: str,
        topic: str,
        examples: list[dict[str, Any]],
        text: str,
        turn: Any = None,
        content_free: bool = False,
    ) -> JudgeAnswer: ...


class VerdictCache(Protocol):
    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str, ttl_s: int) -> None: ...


def parse_verdict(output: str) -> Verdict:
    match = _OBJECT.search(output or "")
    if not match:
        raise CheckerError("judge answer holds no JSON object")
    try:
        return Verdict.model_validate(json.loads(match.group(0)))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise CheckerError(f"judge answer is not a verdict: {type(exc).__name__}") from exc


def cache_key(namespace: str, tenant_id: str, policy_ref: str, judge_identity: str, text: str) -> str:
    """The verdict's key (ADR-0116): one tenant's entries are never another tenant's."""
    material = "\n".join((tenant_id, policy_ref, judge_identity, normalize(text)))
    digest = hashlib.sha256(material.encode()).hexdigest()
    return f"{namespace}:guard:judge:{digest}"


def draft_ref(policy: Any) -> str:
    """A draft's stand-in for id@version: its id and a hash of its content."""
    content = json.dumps(policy.content(), ensure_ascii=False, sort_keys=True)
    return f"{policy.id}@draft-{hashlib.sha256(content.encode()).hexdigest()[:16]}"


def text_locale(text: str, fallback: str) -> str:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return fallback
    arabic = sum("؀" <= c <= "ۿ" for c in letters)
    return "ar" if arabic / len(letters) >= 0.3 else "en"


class LlmJudgeChecker:
    name = "llm_judge"

    def __init__(self, model: JudgeModel, cache: VerdictCache | None = None, *, namespace: str = "agent"):
        self._model = model
        self._cache = cache
        self._namespace = namespace

    async def check(self, payload: str, config: LlmJudgeConfig, ctx: CheckContext) -> CheckResult:
        if not payload.strip():
            return CheckResult(matched=False, explanation="empty text", reason="empty_text")
        locale = text_locale(payload, ctx.locale)
        policy_ref = draft_ref(ctx.policy) if ctx.draft else ctx.policy.ref
        identity = await self._model.identity(principal=ctx.principal, locale=locale)
        key = cache_key(self._namespace, ctx.principal.tenant_id, policy_ref, identity, payload)
        # The panel, stored policy or draft, never reads the cache: no cache marker (ADR-0117).
        verdict = None if ctx.panel or ctx.draft else await self._cached(key)
        cached = verdict is not None
        usage = None
        if verdict is None:
            answer = await self._model.judge(
                principal=ctx.principal,
                locale=locale,
                topic=config.topic_description,
                examples=[example.model_dump() for example in config.examples],
                text=payload,
                turn=ctx.turn,
                content_free=ctx.target in CONTENT_FREE_TARGETS,
            )
            usage = answer.usage
            verdict = parse_verdict(answer.text)
            if not ctx.panel:
                await self._store(key, verdict)
        matched = verdict.violates and verdict.confidence >= config.min_confidence
        return CheckResult(
            matched=matched,
            score=verdict.confidence,
            explanation=f"{'violates' if verdict.violates else 'clear'}"
            f" ({verdict.category or 'no category'}, confidence {verdict.confidence:.2f}"
            f"{', cached' if cached else ''})",
            reason=("judge_violates" if verdict.violates else "judge_clear") + ("_cached" if cached else ""),
            reason_args={"category": verdict.category, "confidence": f"{verdict.confidence:.2f}"},
            usage=usage,
        )

    async def _cached(self, key: str) -> Verdict | None:
        if self._cache is None:
            return None
        try:
            raw = await self._cache.get(key)
        except Exception as exc:  # a cache outage costs a model call, never the check
            log.warning("judge cache read failed: %s", type(exc).__name__)
            return None
        return Verdict.model_validate_json(raw) if raw else None

    async def _store(self, key: str, verdict: Verdict) -> None:
        if self._cache is None:
            return
        try:
            await self._cache.set(key, verdict.model_dump_json(), CACHE_TTL_S)
        except Exception as exc:
            log.warning("judge cache write failed: %s", type(exc).__name__)
