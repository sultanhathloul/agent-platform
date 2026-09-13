"""OpenTelemetry setup (README §12). Spans go to the OTel collector, which
forwards traces to Langfuse. The OTLP exporter reads OTEL_EXPORTER_OTLP_ENDPOINT
and OTEL_SERVICE_NAME from the environment."""

import logging

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

log = logging.getLogger(__name__)

tracer = trace.get_tracer("agent")

_provider: TracerProvider | None = None


def setup_tracing(enabled: bool) -> None:
    """Installs the global tracer provider once. With tracing disabled, spans
    are no-ops, but code paths and attributes stay identical."""
    global _provider
    if _provider is not None or not enabled:
        return
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    _provider = TracerProvider(resource=Resource.create())
    _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(_provider)
    log.info("tracing enabled")


def shutdown_tracing() -> None:
    if _provider is not None:
        _provider.shutdown()


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None
