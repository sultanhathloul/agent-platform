"""Reranking through LiteLLM's `/rerank` (README §2, ADR-0134). The alias comes from
config/models.yaml (`rerank.alias`); code never names a model. A rerank is not a chat
call, so it has no ContextPackage (ADR-0058 precedent); its inputs are the question and
the candidate chunks."""

from collections.abc import Sequence

import httpx

from app.models.config import ModelsConfig
from app.models.gateway import ModelUnavailable
from app.observability.otel import tracer
from app.settings import Settings


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
            scores = [0.0] * len(documents)
            for result in response.json().get("results") or []:
                index = result.get("index")
                if isinstance(index, int) and 0 <= index < len(documents):
                    scores[index] = float(result.get("relevance_score") or 0.0)
            return scores
