"""Test-only embedding and rerank service behind the mock LiteLLM (phase 04, I15,
ADR-0143). `make test` uses it; the real stack never does.

- `POST /v1/embeddings` (OpenAI format): a hashed bag of the words normalized by
  app/text/arabic.py (search stems), L2-normalized, 256 dimensions. Texts that share more
  words are more similar, identically on every run.
- `POST /rerank` (Infinity format, ADR-0134): the share of the query's words that the
  document contains, so the document sharing the most words with the question ranks first.

Run: uvicorn tests.fixtures.embed_service:app --port 8000"""

import hashlib
import math

from fastapi import FastAPI
from pydantic import BaseModel

from app.text.arabic import search_terms

DIMENSIONS = 256

app = FastAPI(title="test embed and rerank")


def embed(text: str) -> list[float]:
    vector = [0.0] * DIMENSIONS
    for word in search_terms(text):
        digest = hashlib.sha256(word.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % DIMENSIONS
        vector[index] += 1.0 if digest[4] % 2 == 0 else -1.0
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0:
        vector[0] = 1.0  # an empty text still has a direction
        return vector
    return [v / norm for v in vector]


def overlap(query: str, document: str) -> float:
    wanted = set(search_terms(query))
    if not wanted:
        return 0.0
    return len(wanted & set(search_terms(document))) / len(wanted)


class EmbeddingRequest(BaseModel):
    model: str = "hashed"
    input: str | list[str]
    encoding_format: str | None = None


class RerankRequest(BaseModel):
    model: str = "overlap"
    query: str
    documents: list[str]
    top_n: int | None = None
    return_documents: bool | None = None


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/v1/embeddings")
def embeddings(request: EmbeddingRequest) -> dict:
    texts = [request.input] if isinstance(request.input, str) else request.input
    tokens = sum(len(search_terms(t)) for t in texts)
    return {
        "object": "list",
        "model": request.model,
        "data": [{"object": "embedding", "index": i, "embedding": embed(t)} for i, t in enumerate(texts)],
        "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
    }


@app.post("/rerank")
def rerank(request: RerankRequest) -> dict:
    scores = [overlap(request.query, d) for d in request.documents]
    order = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
    if request.top_n:
        order = order[: request.top_n]
    tokens = len(search_terms(request.query)) + sum(len(search_terms(d)) for d in request.documents)
    return {
        "object": "rerank",
        "model": request.model,
        "results": [{"index": i, "relevance_score": scores[i]} for i in order],
        "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
    }
