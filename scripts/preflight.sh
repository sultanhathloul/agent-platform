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

# The reranker's weights (owner decision R1): `make up` never waits for them, and the rerank
# service idles without them; this says what to run. The volume is external, so it is created here.
# Only relevant when RERANK_ENABLED=true (review round 2, finding 3); development leaves it off.
if local_reranker && [[ ${RERANK_ENABLED:-false} == true ]]; then
  docker volume create "$RERANK_VOLUME" >/dev/null
  present=$(rerank_revision_present)
  if [[ -z $present ]]; then
    echo "warning: no reranker weights in volume $RERANK_VOLUME: run 'make models' (documents cannot be" \
      "reranked until then; or set RERANK_ENABLED=false in .env)" >&2
  elif [[ $present != "$RERANK_REVISION" ]]; then
    echo "warning: the reranker weights are revision ${present:0:12}, not the pinned ${RERANK_REVISION:0:12}:" \
      "run 'make models'" >&2
  fi
fi
exit 0
