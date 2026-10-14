"""Reranking through LiteLLM's `/rerank` (README §2, ADR-0134). The alias comes from
config/models.yaml (`rerank.alias`); code never names a model. A rerank is not a chat
call, so it has no ContextPackage (ADR-0058 precedent); its inputs are the question and
the candidate chunks. A response that does not score every document exactly once with a
finite number is `RerankMalformed` (owner decision R2): it is never read as zeros."""

import math
from collections.abc import Sequence

import httpx

from app.models.config import ModelsConfig
from app.models.gateway import ModelUnavailable
from app.observability.otel import tracer
from app.settings import Settings


class RerankMalformed(ValueError):
    """The rerank answer could not be read as one score per document."""


def parse_scores(body: object, count: int) -> list[float]:
    results = body.get("results") if isinstance(body, dict) else None
    if not isinstance(results, list) or len(results) != count:
        raise RerankMalformed("not one result per document")
    scores: list[float | None] = [None] * count
    for result in results:
        index = result.get("index") if isinstance(result, dict) else None
        score = result.get("relevance_score") if isinstance(result, dict) else None
        if not isinstance(index, int) or not 0 <= index < count or scores[index] is not None:
            raise RerankMalformed("a missing, repeated or out-of-range index")
        if isinstance(score, bool) or not isinstance(score, int | float) or not math.isfinite(score):
            raise RerankMalformed("a missing or non-finite relevance score")
        scores[index] = float(score)
    return [float(s) for s in scores]


class Reranker:
    def __init__(self, settings: Settings, config: ModelsConfig, *, timeout_s: float = 60.0):
        self._url = settings.litellm_base_url.rstrip("/") + "/rerank"
        self._key = settings.litellm_api_key or "unset"
        self._alias = config.rerank["alias"]
        self._timeout = timeout_s

    async def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        """One relevance score per document, in the documents' order."""
        if not documents:
            return []
        with tracer.start_as_current_span("model.rerank") as span:
            span.set_attribute("model.alias", self._alias)
            span.set_attribute("rerank.documents", len(documents))
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(
                        self._url,
                        headers={"Authorization": f"Bearer {self._key}"},
                        json={"model": self._alias, "query": query, "documents": list(documents)},
                    )
            except httpx.HTTPError as exc:
                raise ModelUnavailable(f"LiteLLM rerank unreachable: {type(exc).__name__}") from exc
            if response.status_code != 200:
                raise ModelUnavailable(f"LiteLLM rerank returned {response.status_code}")
            try:
                body = response.json()
            except ValueError as exc:
                raise RerankMalformed("not JSON") from exc
            return parse_scores(body, len(documents))
