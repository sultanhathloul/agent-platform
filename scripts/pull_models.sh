#!/usr/bin/env bash
# Pulls every Ollama model named in infra/litellm/config.yaml, the only file in
# the repository that names a model (ADR-0008). Already-present models are left
# alone, so this is safe to re-run.
#
# Point it at a shared model server with OLLAMA_HOST, e.g.
#   OLLAMA_HOST=http://models.internal:11434 scripts/pull_models.sh
set -euo pipefail
cd "$(dirname "$0")/.."

host="${OLLAMA_HOST:-${OLLAMA_HOST_URL:-http://localhost:11434}}"

models=$(sed -nE 's#^[[:space:]]*model: ollama(_chat)?/(.+)$#\2#p' infra/litellm/config.yaml | sort -u)
if [[ -z "$models" ]]; then
  echo "no Ollama models in infra/litellm/config.yaml; nothing to pull"
  exit 0
fi

tags=$(curl -sf -m 5 "$host/api/tags") || {
  echo "error: Ollama is not reachable at $host" >&2
  exit 1
}

for model in $models; do
  if jq -e --arg m "$model" '.models[] | select(.name == $m or .name == ($m + ":latest"))' <<<"$tags" >/dev/null; then
    echo "  present  $model"
    continue
  fi
  echo "  pulling  $model (this can take several minutes)"
  curl -sS -X POST "$host/api/pull" -H 'Content-Type: application/json' \
    -d "{\"model\":\"$model\",\"stream\":false}" | jq -e '.status == "success"' >/dev/null || {
    echo "error: pulling $model failed" >&2
    exit 1
  }
done

echo "all models in infra/litellm/config.yaml are available at $host"
