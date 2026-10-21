"""LiteLLM client (README §19.2). The only module that sends a model request.

`call` takes a ContextPackage, never raw messages, so a model call cannot
bypass Context Assembly (CLAUDE.md rule 6). It sends a tier alias, never a
model name; LiteLLM maps the alias to an endpoint (infra/litellm/config.yaml).

Bounds (timeout budget PR, I4, ADR-0257): a call runs under one wall-clock deadline from send to
the last chunk, min(its role's ceiling in config/models.yaml `timeouts`, what remains of the call
around it) (app/resilience/deadline.py). Each request tells LiteLLM to give up 1 s before that
deadline (its `timeout`), and the client's own per-operation timeout sits 1 s past it, so the
deadline decides. Whatever ends the call, its stream is closed, so the request to LiteLLM is
aborted (rule 12f). LiteLLM retries nothing (num_retries 0); the agent retries once, only before
the first streamed token, only on 429, 5xx or a connection error, and only when enough time is
left. Past its own ceiling the call is `ModelUnavailable`; cut by the deadline around it, it
raises DeadlineExceeded, which the owner of that deadline reports.
"""

import asyncio
import random
from collections.abc import Callable
from dataclasses import dataclass, field

import anyio
from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    DefaultAsyncHttpxClient,
)
from pydantic import BaseModel

from app.context.package import ContextPackage
from app.context.tokens import TokenCounter
from app.models.config import ModelsConfig
from app.models.routing import TierDecision
from app.models.transport import AnswerTooLarge, CappedTransport
from app.observability.langfuse import set_generation, set_metadata
from app.observability.otel import tracer
from app.observability.redaction import content_hash, is_content_free, messages_digest, observable_messages
from app.resilience import deadline
from app.settings import Settings

# The deployment LiteLLM routed the alias to (its `model` in infra/litellm/config.yaml,
# resolved from the environment). Stream chunks carry the alias only (ADR-0072).
SERVED_MODEL_HEADER = "x-litellm-model-name"


class ModelUnavailable(RuntimeError):
    pass


# The client's per-operation timeout past the call's deadline, and LiteLLM's per-request timeout
# before it (I4): the agent's deadline always decides, and LiteLLM lets go of the provider first.
CLIENT_MARGIN_S = 1.0
LITELLM_MARGIN_S = 1.0
# How long closing an abandoned stream may take before the caller goes on.
CLOSE_GRACE_S = 1.0
# A failure with no more than this left of the call's deadline is that deadline running out:
# LiteLLM was told to give up LITELLM_MARGIN_S before it, and it did.
DEADLINE_EDGE_S = LITELLM_MARGIN_S + 0.5


def request_bounds(budget: deadline.Budget) -> tuple[float, float]:
    """(the client's per-operation timeout, LiteLLM's per-request timeout) for one request."""
    remaining = max(budget.remaining, 0.0)
    return remaining + CLIENT_MARGIN_S, max(remaining - LITELLM_MARGIN_S, 0.1)


def retryable(exc: BaseException) -> bool:
    """429, 5xx or a connection error (a timeout of the client's own is past the deadline)."""
    if isinstance(exc, APIStatusError):
        return exc.status_code == 429 or exc.status_code >= 500
    return isinstance(exc, APIConnectionError) and not isinstance(exc, APITimeoutError)


async def backoff(config: ModelsConfig, budget: deadline.Budget, attempt: int, rng: random.Random) -> bool:
    """Waits before the one retry and says whether to make it: only after the first attempt and
    only when at least `retry.min_remaining_s` remain (I4). `rng` draws the jitter; a test seeds it."""
    retry = config.timeouts.retry
    if attempt > 0 or budget.remaining < retry.min_remaining_s:
        return False
    await asyncio.sleep(rng.uniform(*retry.backoff_s))
    return True


@dataclass
class _Streamed:
    parts: list[str] = field(default_factory=list)
    usage: object = None
    served: str | None = None
    first_token: bool = False


class ModelResult(BaseModel):
    text: str
    tier: str
    served_model: str | None
    tokens_in: int
    tokens_out: int
    usage_reported: bool


class ModelGateway:
    def __init__(
        self,
        settings: Settings,
        config: ModelsConfig,
        counter: TokenCounter,
        *,
        rng: random.Random | None = None,
    ):
        # Each request sets its own timeouts from its deadline (`request_bounds`); this default
        # applies to none of them.
        # Every answer is byte-capped while it is read, any status (app/models/transport.py).
        self._client = AsyncOpenAI(
            base_url=settings.litellm_base_url,
            api_key=settings.litellm_api_key or "unset",
            timeout=settings.model_timeout_s,
            max_retries=0,
            http_client=DefaultAsyncHttpxClient(transport=CappedTransport()),
        )
        self._config = config
        self._counter = counter
        self._rng = rng or random.Random()  # the retry's jitter (I4); injectable for a seeded test
        self._capture = settings.observability_capture_content
        # Checked at startup (app.main.start_services), so the spec export can build without it.
        self._hash_key = settings.content_hash_key

    async def call(
        self,
        decision: TierDecision,
        context: ContextPackage,
        *,
        on_token: Callable[[str], None] | None = None,
        temperature: float | None = None,
    ) -> ModelResult:
        """`temperature` overrides the role's only for a retry whose temperature comes
        from config/validation.yaml (ADR-0061); no call site passes a literal."""
        if context.tier != decision.final:
            raise ValueError(
                f"context was assembled for tier {context.tier}, call resolved to {decision.final}"
            )
        tier = self._config.tiers[decision.final]
        # Decoding temperature belongs to the role, not to the call site (ADR-0032).
        if temperature is None:
            temperature = self._config.temperature[decision.role]
        messages = context.to_messages()

        with tracer.start_as_current_span("model.call") as span:
            decision.record(span)
            span.set_attribute("model.alias", tier.alias)
            span.set_attribute("model.temperature", temperature)
            set_metadata(
                span,
                {
                    "prompts": context.prompt_refs,
                    "tier_reason": decision.reason,
                    "context_tokens": context.total_tokens(),
                },
            )
            ceiling = self._config.timeouts.roles[decision.role]
            retries = 0
            try:
                with deadline.within(ceiling) as budget:
                    span.set_attribute("model.deadline_s", round(budget.remaining, 3))
                    for attempt in range(2):
                        streamed = _Streamed()
                        try:
                            await self._stream(tier, messages, temperature, budget, streamed, on_token)
                            break
                        except AnswerTooLarge as exc:  # refused unparsed: never retried
                            raise ModelUnavailable(f"LiteLLM answer refused: {exc}") from None
                        except APIError as exc:  # a status, a connection error, an error mid-stream
                            if budget.remaining <= DEADLINE_EDGE_S:
                                raise deadline.DeadlineExceeded(budget.inherited) from None
                            if (
                                streamed.first_token
                                or not retryable(exc)
                                or not await backoff(self._config, budget, attempt, self._rng)
                            ):
                                raise _unavailable(exc) from exc
                            retries += 1
            except deadline.DeadlineExceeded as exc:
                span.set_attribute("model.timed_out", True)
                if exc.inherited:
                    raise  # the deadline around the call ended it: its owner reports it
                raise ModelUnavailable(f"model call past its {ceiling:.0f} s bound") from None
            finally:
                span.set_attribute("model.retries", retries)
            parts, usage, served = streamed.parts, streamed.usage, streamed.served

            text = "".join(parts)
            tokens_in = usage.prompt_tokens if usage else context.total_tokens()
            tokens_out = usage.completion_tokens if usage else self._counter.count(text)
            span.set_attribute("model.usage_reported", usage is not None)
            # The upstream model when LiteLLM names it; otherwise the alias, marked unknown.
            span.set_attribute("model.served_known", served is not None)
            # D5: no memory, summary or extractor content reaches Langfuse; keyed hashes stand in.
            content_free = is_content_free(context, decision.role)
            span.set_attribute("model.content_free", content_free)
            set_metadata(
                span,
                {
                    "input_hmac": messages_digest(messages, self._hash_key),
                    "output_hmac": content_hash(text, self._hash_key),
                },
            )
            set_generation(
                span,
                model=served or tier.alias,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                input_messages=observable_messages(context, self._hash_key)
                if self._capture and not content_free
                else None,
                output_text=text if self._capture and not content_free else None,
            )
            return ModelResult(
                text=text,
                tier=decision.final,
                served_model=served,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                usage_reported=usage is not None,
            )

    async def _stream(
        self,
        tier,
        messages,
        temperature: float,
        budget: deadline.Budget,
        streamed: _Streamed,
        on_token: Callable[[str], None] | None,
    ) -> None:
        """One request, streamed into `streamed`. Its response is closed however it ends."""
        client_timeout, litellm_timeout = request_bounds(budget)
        raw = await self._client.chat.completions.with_raw_response.create(
            model=tier.alias,
            messages=messages,
            stream=True,
            stream_options={"include_usage": True},
            max_tokens=tier.max_output,
            temperature=temperature,
            timeout=client_timeout,
            extra_body={"timeout": litellm_timeout},
        )
        streamed.served = raw.headers.get(SERVED_MODEL_HEADER) or None
        stream = raw.parse()
        try:
            async for chunk in stream:
                if chunk.usage:
                    streamed.usage = chunk.usage
                for choice in chunk.choices:
                    delta = choice.delta.content if choice.delta else None
                    if delta:
                        streamed.first_token = True
                        streamed.parts.append(delta)
                        if on_token:
                            on_token(delta)
        finally:
            # Shielded: a call its deadline cancelled still closes its response, which aborts
            # the request at LiteLLM (rule 12f).
            with anyio.CancelScope(shield=True), anyio.move_on_after(CLOSE_GRACE_S):
                await stream.close()


def _unavailable(exc: BaseException) -> ModelUnavailable:
    if isinstance(exc, APIStatusError):
        return ModelUnavailable(f"LiteLLM returned {exc.status_code}")
    return ModelUnavailable(f"LiteLLM unreachable: {type(exc).__name__}")
