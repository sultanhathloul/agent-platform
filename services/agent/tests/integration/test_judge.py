"""The llm_judge checker on its real path (CLAUDE.md rule 6): GraphJudge, Context Assembly,
the tier resolver and the mock LiteLLM, with the judge's text replaced after the real call
(ADR-0075) to give a canned verdict. The verdict cache is the test session's Redis
namespace; each test uses one of its own."""

import json
import uuid

from app.graph.judge import GraphJudge
from app.guardrails.cache import RedisVerdictCache
from app.guardrails.checkers.base import CheckContext
from app.guardrails.checkers.llm_judge import LlmJudgeChecker
from app.guardrails.checkpoints import Checkpoint
from app.guardrails.engine import GuardEngine
from app.guardrails.panel import try_policy
from app.guardrails.schema import LlmJudgeConfig, Policy
from app.identity import Principal
from tests.fixtures.replace import replacing

PRINCIPAL = Principal(user_id="u-judge")
VIOLATES = json.dumps({"violates": True, "confidence": 0.8, "category": "politics"})
CLEAR = json.dumps({"violates": False, "confidence": 0.95, "category": ""})


def judge_policy(version: int = 1, topic: str = "آراء سياسية حول الأحزاب أو الانتخابات") -> Policy:
    return Policy(
        id=f"judge-{uuid.uuid4().hex[:8]}",
        version=version,
        name="politics",
        scope="global",
        checkpoints=["INPUT"],
        checker="llm_judge",
        config={"topic_description": topic, "min_confidence": 0.6},
        action="block",
        message_ar="لا أستطيع الخوض في هذا الموضوع.",
        message_en="I cannot discuss this topic.",
    )


def judged(services, verdict: str):
    """A GraphJudge whose real LiteLLM call answers `verdict` (ADR-0075), and the gateway counting calls."""
    replaced, gateway = replacing(services, {"judge": verdict})
    judge = GraphJudge()
    judge.bind(replaced)
    return judge, gateway


class Static:
    def __init__(self, *policies):
        self.policies = list(policies)

    def active(self):
        return self.policies


def _namespace() -> str:
    return f"judge-test-{uuid.uuid4().hex[:8]}"


async def test_the_judge_is_called_once_per_policy_version_and_normalized_text(services, litellm):
    judge, gateway = judged(services, VIOLATES)
    checker = LlmJudgeChecker(judge, RedisVerdictCache(services.redis), namespace=_namespace())
    policy = judge_policy()
    ctx = CheckContext(principal=PRINCIPAL, policy=policy, locale="ar")
    config = LlmJudgeConfig.model_validate(policy.config)

    first = await checker.check("ما رأيك في الأحزاب؟", config, ctx)
    again = await checker.check("ما رأيُك في الاحزاب؟", config, ctx)  # same after normalization
    assert first.matched and again.matched and gateway.calls.count("judge") == 1
    assert "cached" in again.explanation

    edited = CheckContext(principal=PRINCIPAL, policy=policy.model_copy(update={"version": 2}), locale="ar")
    await checker.check("ما رأيك في الأحزاب؟", config, edited)
    assert gateway.calls.count("judge") == 2  # a new version never reuses an old verdict
    identity = await judge.identity(principal=PRINCIPAL, locale="ar")
    assert "guardrails.judge.system@" in identity and "guardrails.judge.request@" in identity
    assert ";alias=" in identity


async def test_low_confidence_does_not_match(services, litellm):
    judge, _ = judged(services, json.dumps({"violates": True, "confidence": 0.4, "category": "politics"}))
    policy = judge_policy()
    result = await LlmJudgeChecker(judge).check(
        "سؤال", LlmJudgeConfig.model_validate(policy.config), CheckContext(principal=PRINCIPAL, policy=policy)
    )
    assert not result.matched and result.score == 0.4


async def test_a_cache_outage_costs_a_model_call_not_the_check(services, litellm):
    class Broken:
        async def get(self, key):
            raise ConnectionError

        async def set(self, key, value, ttl_s):
            raise ConnectionError

    judge, gateway = judged(services, CLEAR)
    policy = judge_policy()
    result = await LlmJudgeChecker(judge, Broken()).check(
        "ما عاصمة الأردن؟",
        LlmJudgeConfig.model_validate(policy.config),
        CheckContext(principal=PRINCIPAL, policy=policy),
    )
    assert not result.matched and gateway.calls.count("judge") == 1


async def test_a_panel_test_of_an_edited_draft_does_not_change_the_live_verdict(services, litellm):
    """ADR-0110: the panel never writes the cache, and a draft never reads it."""
    namespace, cache = _namespace(), RedisVerdictCache(services.redis)
    stored = judge_policy()
    text = "ما رأيك في الأحزاب السياسية؟"
    payload = {"message": text, "attachments": []}

    live_judge, live_gateway = judged(services, VIOLATES)
    live = GuardEngine(Static(stored), {"llm_judge": LlmJudgeChecker(live_judge, cache, namespace=namespace)})
    assert (await live.evaluate(Checkpoint.INPUT, payload, principal=PRINCIPAL)).blocked
    assert live_gateway.calls.count("judge") == 1  # the verdict is now cached for stored@1

    panel_judge, panel_gateway = judged(services, CLEAR)
    panel = GuardEngine(Static(), {"llm_judge": LlmJudgeChecker(panel_judge, cache, namespace=namespace)})
    # The draft has exactly the stored content: it still asks the model (it never reads the cache) ...
    same = await try_policy(panel, stored, text, principal=PRINCIPAL, draft=True)
    assert same.action == "allow" and panel_gateway.calls.count("judge") == 1
    # ... and an edited draft's clear verdict is written nowhere a live turn reads.
    edited = stored.model_copy(update={"config": {**stored.config, "topic_description": "موضوع آخر تمامًا"}})
    assert (await try_policy(panel, edited, text, principal=PRINCIPAL, draft=True)).action == "allow"
    # A panel test of the stored policy may read the cache but never writes it.
    assert (await try_policy(panel, stored, text, principal=PRINCIPAL)).action == "block"

    again = await live.evaluate(Checkpoint.INPUT, payload, principal=PRINCIPAL)
    assert again.blocked and live_gateway.calls.count("judge") == 1  # the cached live verdict, unchanged
