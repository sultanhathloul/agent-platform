"""Embeddings through LiteLLM (README §2, §8). The alias comes from config/models.yaml
(`embedding.alias`); code never names a model. An embedding is not a chat call, so
it has no ContextPackage; its text is the message or memory item being matched."""

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from app.models.config import ModelsConfig
from app.models.gateway import ModelUnavailable
from app.observability.otel import tracer
from app.settings import Settings


class EmbeddingGateway:
    def __init__(self, settings: Settings, config: ModelsConfig, *, alias: str | None = None):
        self._client = AsyncOpenAI(
            base_url=settings.litellm_base_url,
            api_key=settings.litellm_api_key or "unset",
            timeout=settings.model_timeout_s,
            max_retries=0,
        )
        self._alias = alias or config.embedding["alias"]

    async def embed(self, text: str) -> list[float]:
        return (await self.embed_many([text]))[0]

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        """One request for several texts (document chunks at ingestion, phase 04), in order."""
        with tracer.start_as_current_span("model.embed") as span:
            span.set_attribute("model.alias", self._alias)
            span.set_attribute("model.embedding.inputs", len(texts))
            try:
                response = await self._client.embeddings.create(
                    model=self._alias, input=list(texts), encoding_format="float"
                )
            except (APIConnectionError, APITimeoutError) as exc:
                raise ModelUnavailable(f"LiteLLM unreachable: {exc}") from exc
            except APIStatusError as exc:
                raise ModelUnavailable(f"LiteLLM returned {exc.status_code}") from exc
            if len(response.data) != len(texts):
                raise ModelUnavailable(
                    f"LiteLLM returned {len(response.data)} embeddings for {len(texts)} texts"
                )
            ordered = sorted(response.data, key=lambda item: item.index)
            vectors = [[float(value) for value in item.embedding] for item in ordered]
            span.set_attribute("model.embedding.dimensions", len(vectors[0]) if vectors else 0)
            return vectors
