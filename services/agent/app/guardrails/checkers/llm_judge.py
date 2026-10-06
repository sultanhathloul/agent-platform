"""`llm_judge` (README §10.2): the primary topic filter. The `judge` role (pinned to
the fast tier, temperature 0) reads the policy's topic description and examples
and the text, through Context Assembly and models.call (CLAUDE.md rule 6), with
the versioned prompts `guardrails.judge.system` and `guardrails.judge.request`,
and answers JSON {violates, confidence, category}. The prompt locale follows the
text's script. Verdicts are cached in Redis for 24 hours, keyed by a hash of the
policy's id@version and the normalized text (I4), so a policy edit never reuses
an old verdict. A malformed answer is a CheckerError: the policy's `on_error`
decides (open by default, D8)."""

import hashlib
import json
import logging
import re
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.guardrails.checkers.base import CheckContext, CheckerError
from app.guardrails.schema import CheckResult, LlmJudgeConfig
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


class JudgeModel(Protocol):
    """Runs the judge prompt through Context Assembly and LiteLLM (app/graph/judge.py)."""

    async def judge(
        self,
        *,
        principal: Principal,
        locale: str,
        topic: str,
        examples: list[dict[str, Any]],
        text: str,
        turn: Any = None,
    ) -> str: ...


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


def cache_key(namespace: str, policy_ref: str, text: str) -> str:
    digest = hashlib.sha256(f"{policy_ref}\n{normalize(text)}".encode()).hexdigest()
    return f"{namespace}:guard:judge:{digest}"


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
            return CheckResult(matched=False, explanation="empty text")
        key = cache_key(self._namespace, ctx.policy.ref, payload)
        verdict = await self._cached(key)
        cached = verdict is not None
        if verdict is None:
            raw = await self._model.judge(
                principal=ctx.principal,
                locale=text_locale(payload, ctx.locale),
                topic=config.topic_description,
                examples=[example.model_dump() for example in config.examples],
                text=payload,
                turn=ctx.turn,
            )
            verdict = parse_verdict(raw)
            await self._store(key, verdict)
        matched = verdict.violates and verdict.confidence >= config.min_confidence
        return CheckResult(
            matched=matched,
            score=verdict.confidence,
            explanation=f"{'violates' if verdict.violates else 'clear'}"
            f" ({verdict.category or 'no category'}, confidence {verdict.confidence:.2f}"
            f"{', cached' if cached else ''})",
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
