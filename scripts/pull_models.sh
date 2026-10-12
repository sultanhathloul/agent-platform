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

# The reranker (ADR-0134, ADR-0179): bge-reranker-v2-m3 at a pinned revision into the external
# volume the `rerank` service reads, with the Infinity image's own Hugging Face client. Skipped when
# that revision is already there, or when RERANK_API_BASE points at a reranker served elsewhere.
if ! local_reranker; then
  echo "  skipped  reranker weights (RERANK_API_BASE=${RERANK_API_BASE:-unset} is not the stack's rerank service)"
  exit 0
fi
docker volume create "$RERANK_VOLUME" >/dev/null
if [[ $(rerank_revision_present) == "$RERANK_REVISION" ]]; then
  echo "  present  $RERANK_REPO@${RERANK_REVISION:0:12} (volume $RERANK_VOLUME)"
else
  echo "  fetching $RERANK_REPO@${RERANK_REVISION:0:12} into volume $RERANK_VOLUME (about 2.3 GB)"
  # The image enables hf_transfer, which can stall; the plain downloader resumes instead.
  docker run --rm --entrypoint sh -e HF_HUB_ENABLE_HF_TRANSFER=0 -v "$RERANK_VOLUME:/models" "$RERANK_IMAGE" \
    -c "huggingface-cli download $RERANK_REPO --revision $RERANK_REVISION --local-dir /models/bge-reranker-v2-m3 --exclude '*.onnx*'" >/dev/null
  [[ $(rerank_revision_present) == "$RERANK_REVISION" ]] || { echo "error: the reranker download did not complete" >&2; exit 1; }
  echo "every reranker weight is available"
fi
