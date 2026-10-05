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
