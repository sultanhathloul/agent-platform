#!/usr/bin/env bash
# Host prerequisites the stack cannot check for itself. Warns, and fails only
# when Docker is down.
set -uo pipefail
cd "$(dirname "$0")/.."
source scripts/models_env.sh

docker info >/dev/null 2>&1 || { echo "error: docker is not running" >&2; exit 1; }

mem=$(docker info --format '{{.MemTotal}}')
if (( mem < 12 * 1024 * 1024 * 1024 )); then
  echo "warning: Docker has $(( mem / 1024 / 1024 / 1024 )) GiB of memory; 12 GiB or more is recommended" >&2
fi

# The models .env names on Ollama (ADR-0066); models served elsewhere are not checked.
while read -r model host; do
  if ! tags=$(curl -sf -m 3 "$host/api/tags"); then
    echo "warning: Ollama is not reachable at $host; $model fails until it is" >&2
    continue
  fi
  jq -e --arg m "$model" '.models[] | select(.name == $m or .name == ($m + ":latest"))' <<<"$tags" >/dev/null \
    || echo "warning: Ollama model $model is not pulled at $host (make models)" >&2
done < <(ollama_models)
exit 0
