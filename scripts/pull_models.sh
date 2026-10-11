#!/usr/bin/env bash
# Pulls every Ollama model the tier aliases resolve to, as `.env` sets them
# (FAST_MODEL, STANDARD_MODEL, HEAVY_MODEL, EMBED_MODEL; ADR-0066), into the
# Ollama server the stack calls (MODEL_API_BASE, EMBED_API_BASE). Models served
# elsewhere (e.g. `openrouter/...`) are skipped, and already-present models are
# left alone, so this is safe to re-run.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/models_env.sh

pulled=0
while read -r model host; do
  tags=$(curl -sf -m 5 "$host/api/tags") || {
    echo "error: Ollama is not reachable at $host" >&2
    exit 1
  }
  pulled=1
  if jq -e --arg m "$model" '.models[] | select(.name == $m or .name == ($m + ":latest"))' <<<"$tags" >/dev/null; then
    echo "  present  $model ($host)"
    continue
  fi
  echo "  pulling  $model into $host (this can take several minutes)"
  curl -sS -X POST "$host/api/pull" -H 'Content-Type: application/json' \
    -d "{\"model\":\"$model\",\"stream\":false}" | jq -e '.status == "success"' >/dev/null || {
    echo "error: pulling $model failed" >&2
    exit 1
  }
done < <(ollama_models)

(( pulled )) && echo "every Ollama model in .env is available" || echo "no Ollama model in .env; nothing to pull"

# The reranker (ADR-0134): bge-reranker-v2-m3 into the volume the `rerank` service reads, with
# the Infinity image's own Hugging Face client. Skipped when the weights are already there.
RERANK_IMAGE=michaelf34/infinity:0.0.75
RERANK_MODEL=BAAI/bge-reranker-v2-m3
docker volume create rerank-models >/dev/null
if docker run --rm --entrypoint sh -v rerank-models:/models "$RERANK_IMAGE" \
     -c 'test -s /models/bge-reranker-v2-m3/model.safetensors'; then
  echo "  present  $RERANK_MODEL (volume rerank-models)"
else
  echo "  fetching $RERANK_MODEL into volume rerank-models (about 2.3 GB)"
  docker run --rm --entrypoint sh -v rerank-models:/models "$RERANK_IMAGE" \
    -c "huggingface-cli download $RERANK_MODEL --local-dir /models/bge-reranker-v2-m3 --exclude '*.onnx*'" >/dev/null
  echo "every reranker weight is available"
fi
