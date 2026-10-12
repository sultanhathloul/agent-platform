# Sourced by preflight.sh and pull_models.sh: the model settings resolved from
# .env, the same values docker compose passes to LiteLLM (ADR-0066).
[[ -f .env ]] && { set -a; source .env; set +a; }

# One line per alias served by Ollama: "<model> <base URL reachable from the host>".
# host.docker.internal is the Docker host as containers see it; the host itself is localhost.
ollama_models() {
  local var base model
  for var in FAST_MODEL STANDARD_MODEL HEAVY_MODEL EMBED_MODEL; do
    model=${!var:-}
    [[ $model =~ ^ollama(_chat)?/(.+)$ ]] || continue
    if [[ $var == EMBED_MODEL ]]; then base=${EMBED_API_BASE:-}; else base=${MODEL_API_BASE:-}; fi
    printf '%s %s\n' "${BASH_REMATCH[2]}" "${base/host.docker.internal/localhost}"
  done | sort -u
}

# The stack's own reranker (ADR-0134, ADR-0179): its weights, at a pinned revision, live in the
# external volume rerank-models. Used only when RERANK_API_BASE points at the `rerank` service.
RERANK_IMAGE=michaelf34/infinity:0.0.75
RERANK_REPO=BAAI/bge-reranker-v2-m3
RERANK_REVISION=953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e
RERANK_VOLUME=rerank-models

local_reranker() { [[ ${RERANK_API_BASE:-} == http://rerank:7997 ]]; }

# The revision of the weights in the volume, or nothing when there are none.
rerank_revision_present() {
  docker run --rm --entrypoint sh -v "$RERANK_VOLUME:/models:ro" "$RERANK_IMAGE" -c \
    'test -s /models/bge-reranker-v2-m3/model.safetensors && head -1 /models/bge-reranker-v2-m3/.cache/huggingface/download/model.safetensors.metadata' \
    2>/dev/null || true
}
