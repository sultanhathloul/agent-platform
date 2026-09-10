"""Span attribute names that Langfuse's OTLP endpoint maps onto traces,
observations and generations. Kept in one place so call sites stay readable."""

import json
from collections.abc import Mapping
from typing import Any

from opentelemetry.trace import Span


def set_trace_attributes(
    span: Span, *, name: str, session_id: str, user_hash: str, tenant_id: str, metadata: Mapping[str, Any]
) -> None:
    span.set_attribute("langfuse.trace.name", name)
    span.set_attribute("langfuse.session.id", session_id)
    span.set_attribute("langfuse.user.id", user_hash)
    span.set_attribute("conversation_id", session_id)
    span.set_attribute("tenant_id", tenant_id)
    for key, value in metadata.items():
        span.set_attribute(f"langfuse.trace.metadata.{key}", _scalar(value))


def set_metadata(span: Span, metadata: Mapping[str, Any]) -> None:
    for key, value in metadata.items():
        span.set_attribute(f"langfuse.observation.metadata.{key}", _scalar(value))


def set_generation(
    span: Span,
    *,
    model: str | None,
    tokens_in: int,
    tokens_out: int,
    input_messages: list[dict] | None,
    output_text: str | None,
) -> None:
    span.set_attribute("langfuse.observation.type", "generation")
    if model:
        span.set_attribute("gen_ai.response.model", model)
        span.set_attribute("langfuse.observation.model.name", model)
    span.set_attribute("gen_ai.usage.input_tokens", tokens_in)
    span.set_attribute("gen_ai.usage.output_tokens", tokens_out)
    if input_messages is not None:
        span.set_attribute("langfuse.observation.input", json.dumps(input_messages, ensure_ascii=False))
    if output_text is not None:
        span.set_attribute("langfuse.observation.output", output_text)


def _scalar(value: Any) -> str | int | float | bool:
    if isinstance(value, str | int | float | bool):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)
