#!/usr/bin/env bash
# Host prerequisites the stack cannot check for itself. Warns, and fails only
# when Docker is down.
set -uo pipefail
cd "$(dirname "$0")/.."

docker info >/dev/null 2>&1 || { echo "error: docker is not running" >&2; exit 1; }

mem=$(docker info --format '{{.MemTotal}}')
if (( mem < 12 * 1024 * 1024 * 1024 )); then
  echo "warning: Docker has $(( mem / 1024 / 1024 / 1024 )) GiB of memory; 12 GiB or more is recommended" >&2
fi

ollama_url="${OLLAMA_HOST_URL:-http://localhost:11434}"
if ! tags=$(curl -sf -m 3 "$ollama_url/api/tags"); then
  echo "warning: Ollama is not reachable at $ollama_url; chat fails until it is" >&2
  exit 0
fi
# Model names come from the LiteLLM config, the only file that names models.
for model in $(sed -nE 's#^[[:space:]]*model: ollama(_chat)?/(.+)$#\2#p' infra/litellm/config.yaml); do
  jq -e --arg m "$model" '.models[] | select(.name == $m or .name == ($m + ":latest"))' <<<"$tags" >/dev/null \
    || echo "warning: Ollama model $model is not pulled (ollama pull $model)" >&2
done
exit 0
