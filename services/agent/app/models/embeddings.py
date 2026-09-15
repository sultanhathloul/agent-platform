"""Embeddings through LiteLLM (README §2, §8). The alias comes from config/models.yaml
(`embedding.alias`); code never names a model. An embedding is not a chat call, so
it has no ContextPackage; its text is the message or memory item being matched."""

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from app.models.config import ModelsConfig
from app.models.gateway import ModelUnavailable
from app.observability.otel import tracer
from app.settings import Settings


class EmbeddingGateway:
    def __init__(self, settings: Settings, config: ModelsConfig):
        self._client = AsyncOpenAI(
            base_url=settings.litellm_base_url,
            api_key=settings.litellm_api_key or "unset",
            timeout=settings.model_timeout_s,
            max_retries=0,
        )
        self._alias = config.embedding["alias"]

    async def embed(self, text: str) -> list[float]:
        with tracer.start_as_current_span("model.embed") as span:
            span.set_attribute("model.alias", self._alias)
            try:
                response = await self._client.embeddings.create(
                    model=self._alias, input=[text], encoding_format="float"
                )
            except (APIConnectionError, APITimeoutError) as exc:
                raise ModelUnavailable(f"LiteLLM unreachable: {exc}") from exc
            except APIStatusError as exc:
                raise ModelUnavailable(f"LiteLLM returned {exc.status_code}") from exc
            if not response.data:
                raise ModelUnavailable("LiteLLM returned no embedding")
            vector = [float(value) for value in response.data[0].embedding]
            span.set_attribute("model.embedding.dimensions", len(vector))
            return vector
