"""Embeddings through LiteLLM (README §2, §8). The alias comes from config/models.yaml
(`embedding.alias`); code never names a model. An embedding is not a chat call, so
it has no ContextPackage; its text is the message or memory item being matched.

Bounds (I4, ADR-0257): `embed` is a query (`timeouts.embedding.query`, 15 s) and `embed_many` a
batch of document chunks (`timeouts.embedding.ingest`, 60 s), each cut to what remains of the call
around it, with the model gateway's per-request timeouts and its one retry. The answer is read
incrementally and refused past MAX_BYTES_PER_INPUT per text before it is parsed (rule 12d): a
provider that floods never costs the event loop more than a capped parse. An error answer is
capped at 1 MiB under the SDK, before it reads or parses it (app/models/transport.py, ADR-0266)."""

import json
import random
from typing import Literal

import httpx
from openai import APIError, AsyncOpenAI, DefaultAsyncHttpxClient

from app.models.config import ModelsConfig
from app.models.gateway import DEADLINE_EDGE_S, ModelUnavailable, backoff, request_bounds, retryable
from app.models.transport import AnswerTooLarge, CappedTransport
from app.observability.otel import tracer
from app.resilience import deadline
from app.settings import Settings

# The largest answer per input text: a 1024-dimension vector (bge-m3, the shipped embedder) is
# about 21 KiB of JSON; this leaves room for 2048 dimensions. Over it, the answer is refused unread.
MAX_BYTES_PER_INPUT = 48 * 1024


async def read_capped(response: httpx.Response, cap: int, what: str) -> bytes:
    """The body, read as it arrives and refused past `cap` bytes before anything parses it."""
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body += chunk
        if len(body) > cap:
            raise ModelUnavailable(f"{what} answer over its {cap} byte cap")
    return bytes(body)


class EmbeddingGateway:
    def __init__(
        self,
        settings: Settings,
        config: ModelsConfig,
        *,
        alias: str | None = None,
        rng: random.Random | None = None,
    ):
        # An error answer is byte-capped while it is read (app/models/transport.py); a 2xx answer
        # is capped per input text below, before it is parsed.
        self._client = AsyncOpenAI(
            base_url=settings.litellm_base_url,
            api_key=settings.litellm_api_key or "unset",
            timeout=settings.model_timeout_s,
            max_retries=0,
            http_client=DefaultAsyncHttpxClient(transport=CappedTransport(cap_success=False)),
        )
        self._alias = alias or config.embedding["alias"]
        self._config = config
        self._rng = rng or random.Random()  # the retry's jitter (I4); injectable for a seeded test

    async def embed(self, text: str) -> list[float]:
        """A question or a memory item, matched now: the query bound."""
        return (await self.embed_many([text], purpose="query"))[0]

    async def embed_many(
        self, texts: list[str], *, purpose: Literal["query", "ingest"] = "ingest"
    ) -> list[list[float]]:
        """One request for several texts (document chunks at ingestion, phase 04), in order."""
        with tracer.start_as_current_span("model.embed") as span:
            span.set_attribute("model.alias", self._alias)
            span.set_attribute("model.embedding.inputs", len(texts))
            ceiling = self._config.timeouts.embedding[purpose]
            try:
                with deadline.within(ceiling) as budget:
                    for attempt in range(2):
                        client_timeout, litellm_timeout = request_bounds(budget)
                        try:
                            async with self._client.embeddings.with_streaming_response.create(
                                model=self._alias,
                                input=list(texts),
                                encoding_format="float",
                                timeout=client_timeout,
                                extra_body={"timeout": litellm_timeout},
                            ) as answer:
                                cap = MAX_BYTES_PER_INPUT * len(texts)
                                body = await read_capped(answer.http_response, cap, "embedding")
                            break
                        except AnswerTooLarge as exc:  # refused unparsed: never retried
                            raise ModelUnavailable(f"LiteLLM embedding answer refused: {exc}") from None
                        except APIError as exc:
                            if budget.remaining <= DEADLINE_EDGE_S:
                                raise deadline.DeadlineExceeded(budget.inherited) from None
                            retry = retryable(exc) and await backoff(self._config, budget, attempt, self._rng)
                            if not retry:
                                raise ModelUnavailable(
                                    f"LiteLLM embedding failed: {type(exc).__name__}"
                                ) from exc
            except deadline.DeadlineExceeded as exc:
                span.set_attribute("model.timed_out", True)
                if exc.inherited:
                    raise
                raise ModelUnavailable(f"embedding past its {ceiling:.0f} s bound") from None
            try:
                data = json.loads(body)["data"]
                ordered = sorted(data, key=lambda item: item["index"])
                vectors = [[float(value) for value in item["embedding"]] for item in ordered]
            except (ValueError, KeyError, TypeError) as exc:
                raise ModelUnavailable("LiteLLM returned an embedding answer that is not one") from exc
            if len(vectors) != len(texts):
                raise ModelUnavailable(f"LiteLLM returned {len(vectors)} embeddings for {len(texts)} texts")
            span.set_attribute("model.embedding.dimensions", len(vectors[0]) if vectors else 0)
            return vectors
