#!/usr/bin/env bash
# Proves the airgap layout of `make smoke-airgap` (phase 04, F2) before smoke runs on it:
# every running container's networks are internal (no route out), except `litellm`, which
# also joins `egress` for the model endpoints, and `edge`, the fixed TCP relay that carries
# the host's ports in (ADR-0147). Then a live probe: a container on the internal network
# cannot reach the internet, while litellm still answers a model call.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a
ok()   { printf '  ok    %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1" >&2; exit 1; }

printf '\n== airgap layout\n'
project=${COMPOSE_PROJECT_NAME:-agent-platform}
for id in $(docker compose ps -q); do
  service=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$id")
  for network in $(docker inspect -f '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}' "$id"); do
    internal=$(docker network inspect -f '{{.Internal}}' "$network")
    case "$service:$network" in
      litellm:${project}_egress|edge:${project}_ingress) continue ;;
    esac
    [[ $internal == true ]] || fail "$service is on $network, which has a route out"
  done
done
ok "every container but litellm and edge is only on internal networks"
[[ $(docker network inspect -f '{{.Internal}}' "${project}_airgap") == true ]] && ok "${project}_airgap is internal" \
  || fail "${project}_airgap is not internal"

printf '\n== egress probe\n'
if docker run --rm --network "${project}_airgap" python:3.12.14-slim-bookworm python -c "
import urllib.request, sys
try:
    urllib.request.urlopen('https://example.com', timeout=8)
except Exception:
    sys.exit(0)
sys.exit(1)"; then ok "a container on the internal network cannot reach the internet"; else fail "the internal network reaches the internet"; fi
if docker compose exec -T agent python -c "
import urllib.request, sys
try:
    urllib.request.urlopen('https://example.com', timeout=8)
except Exception:
    sys.exit(0)
sys.exit(1)"; then ok "the agent cannot reach the internet"; else fail "the agent reaches the internet"; fi
docker compose exec -T agent python -c "
import httpx, os, sys
r = httpx.post('http://litellm:4000/v1/embeddings', headers={'Authorization': 'Bearer ' + os.environ['LITELLM_API_KEY']},
               json={'model': 'embed', 'input': ['اختبار']}, timeout=60)
sys.exit(0 if r.status_code == 200 else 1)" && ok "litellm still reaches the embedding endpoint" || fail "litellm cannot reach the model endpoints"
