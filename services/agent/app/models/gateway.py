"""LiteLLM client (README §19.2). The only module that sends a model request.

`call` takes a ContextPackage, never raw messages, so a model call cannot
bypass Context Assembly (CLAUDE.md rule 6). It sends a tier alias, never a
model name; LiteLLM maps the alias to an endpoint (infra/litellm/config.yaml).
"""

from collections.abc import Callable

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
from pydantic import BaseModel

from app.context.package import ContextPackage
from app.context.tokens import TokenCounter
from app.models.config import ModelsConfig
from app.models.routing import TierDecision
from app.observability.langfuse import set_generation, set_metadata
from app.observability.otel import tracer
from app.settings import Settings

# The deployment LiteLLM routed the alias to (its `model` in infra/litellm/config.yaml,
# resolved from the environment). Stream chunks carry the alias only (ADR-0072).
SERVED_MODEL_HEADER = "x-litellm-model-name"


class ModelUnavailable(RuntimeError):
    pass


class ModelResult(BaseModel):
    text: str
    tier: str
    served_model: str | None
    tokens_in: int
    tokens_out: int
    usage_reported: bool


class ModelGateway:
    def __init__(self, settings: Settings, config: ModelsConfig, counter: TokenCounter):
        self._client = AsyncOpenAI(
            base_url=settings.litellm_base_url,
            api_key=settings.litellm_api_key or "unset",
            timeout=settings.model_timeout_s,
            max_retries=0,
        )
        self._config = config
        self._counter = counter
        self._capture = settings.observability_capture_content

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
            parts: list[str] = []
            usage = None
            try:
                raw = await self._client.chat.completions.with_raw_response.create(
                    model=tier.alias,
                    messages=messages,
                    stream=True,
                    stream_options={"include_usage": True},
                    max_tokens=tier.max_output,
                    temperature=temperature,
                )
                served = raw.headers.get(SERVED_MODEL_HEADER) or None
                stream = raw.parse()
                async for chunk in stream:
                    if chunk.usage:
                        usage = chunk.usage
                    for choice in chunk.choices:
                        delta = choice.delta.content if choice.delta else None
                        if delta:
                            parts.append(delta)
                            if on_token:
                                on_token(delta)
            except (APIConnectionError, APITimeoutError) as exc:
                raise ModelUnavailable(f"LiteLLM unreachable: {exc}") from exc
            except APIStatusError as exc:
                raise ModelUnavailable(f"LiteLLM returned {exc.status_code}") from exc

            text = "".join(parts)
            tokens_in = usage.prompt_tokens if usage else context.total_tokens()
            tokens_out = usage.completion_tokens if usage else self._counter.count(text)
            span.set_attribute("model.usage_reported", usage is not None)
            # The upstream model when LiteLLM names it; otherwise the alias, marked unknown.
            span.set_attribute("model.served_known", served is not None)
            set_generation(
                span,
                model=served or tier.alias,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                input_messages=messages if self._capture else None,
                output_text=text if self._capture else None,
            )
            return ModelResult(
                text=text,
                tier=decision.final,
                served_model=served,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                usage_reported=usage is not None,
            )
