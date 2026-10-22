"""Reranking through LiteLLM's `/rerank` (README §2, ADR-0134). The alias comes from
config/models.yaml (`rerank.alias`); code never names a model. A rerank is not a chat
call, so it has no ContextPackage (ADR-0058 precedent); its inputs are the question and
the candidate chunks. A response that does not score every document exactly once with a
finite number is `RerankMalformed` (owner decision R2): it is never read as zeros."""

import json
import math
from collections.abc import Sequence

import httpx

from app.models.config import ModelsConfig
from app.models.embeddings import read_capped
from app.models.gateway import DEADLINE_EDGE_S, ModelUnavailable, request_bounds
from app.observability.otel import tracer
from app.platform_settings import effective
from app.resilience import deadline
from app.settings import Settings

# The largest answer accepted: a fixed allowance plus one result per document (Infinity's shape is
# about 60 bytes a result).
MAX_ANSWER_BYTES = 16 * 1024
MAX_BYTES_PER_DOCUMENT = 256


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
    """Bounded by `timeouts.rerank` (20 s), cut to what remains of the call around it, with
    httpx's own timeout 1 s past that and LiteLLM's 1 s before it (I4, ADR-0257). No retry."""

    def __init__(self, settings: Settings, config: ModelsConfig, *, alias: str | None = None):
        self._url = settings.litellm_base_url.rstrip("/") + "/rerank"
        self._key = settings.litellm_api_key or "unset"
        self._alias = alias or config.rerank["alias"]
        self._timeouts = config.timeouts

    async def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        """One relevance score per document, in the documents' order."""
        if not documents:
            return []
        with tracer.start_as_current_span("model.rerank") as span:
            span.set_attribute("model.alias", self._alias)
            span.set_attribute("rerank.documents", len(documents))
            try:
                with deadline.within(effective.timeouts(self._timeouts).rerank) as budget:
                    client_timeout, litellm_timeout = request_bounds(budget)
                    try:
                        async with (
                            httpx.AsyncClient(timeout=client_timeout) as client,
                            client.stream(
                                "POST",
                                self._url,
                                headers={"Authorization": f"Bearer {self._key}"},
                                json={
                                    "model": self._alias,
                                    "query": query,
                                    "documents": list(documents),
                                    "timeout": litellm_timeout,
                                },
                            ) as response,
                        ):
                            status = response.status_code
                            # Refused unread past its cap (rule 12d): scores for these documents only.
                            cap = MAX_ANSWER_BYTES + MAX_BYTES_PER_DOCUMENT * len(documents)
                            body = await read_capped(response, cap, "rerank")
                    except httpx.HTTPError as exc:
                        if budget.remaining <= DEADLINE_EDGE_S:
                            raise deadline.DeadlineExceeded(budget.inherited) from None
                        raise ModelUnavailable(f"LiteLLM rerank unreachable: {type(exc).__name__}") from exc
                    if status != 200 and budget.remaining <= DEADLINE_EDGE_S:
                        raise deadline.DeadlineExceeded(budget.inherited)  # LiteLLM gave up, as told
            except deadline.DeadlineExceeded as exc:
                span.set_attribute("model.timed_out", True)
                if exc.inherited:
                    raise
                ceiling = effective.timeouts(self._timeouts).rerank
                raise ModelUnavailable(f"rerank past its {ceiling:.0f} s bound") from None
            if status != 200:
                raise ModelUnavailable(f"LiteLLM rerank returned {status}")
            try:
                answer = json.loads(body)
            except ValueError as exc:
                raise RerankMalformed("not JSON") from exc
            return parse_scores(answer, len(documents))
