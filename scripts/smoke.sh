#!/usr/bin/env bash
# End-to-end smoke test against the running stack (`make up`).
# It grows each phase: phases add checks and never remove them (ADR-0009).
#   phase 01: health, PKCE login, gateway authz, streamed general turn,
#             Langfuse trace with context block token counts, audit record.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a

GW=$GATEWAY_PUBLIC_URL KC=$KEYCLOAK_PUBLIC_URL LF=$LANGFUSE_PUBLIC_URL WEB=$WEB_PUBLIC_URL
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

step() { printf '\n== %s\n' "$1"; }
ok()   { printf '  ok    %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1" >&2; exit 1; }
check() { local label=$1; shift; "$@" >/dev/null 2>&1 && ok "$label" || fail "$label"; }
status() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
token_for() { uv run -q scripts/get_token.py "$1" "$2"; }

step "health"
unhealthy=$(docker compose ps --format '{{.Service}} {{.Health}}' | awk '$2 != "" && $2 != "healthy" {print $1}')
[[ -z "$unhealthy" ]] && ok "every service with a healthcheck is healthy" || fail "unhealthy: $unhealthy"
check "gateway /actuator/health UP" bash -c "curl -fsS '$GW/actuator/health' | jq -e '.status == \"UP\"'"
check "web /api/health" curl -fsS "$WEB/api/health"
check "keycloak issuer is $KC/realms/$KC_REALM" bash -c \
  "curl -fsS '$KC/realms/$KC_REALM/.well-known/openid-configuration' | jq -e '.issuer == \"$KC/realms/$KC_REALM\"'"
check "langfuse /api/public/health" curl -fsS "$LF/api/public/health"

step "login through the web client (authorization code + PKCE)"
ANALYST=$(token_for analyst analyst) || fail "analyst login"
ok "analyst logged in"
VIEWER=$(token_for viewer viewer) || fail "viewer login"
ok "viewer logged in"

step "gateway authentication and authorization"
[[ $(status "$GW/v1/me") == 401 ]] && ok "no token → 401" || fail "no token must be 401"
[[ $(status -H "Authorization: Bearer not.a.jwt" "$GW/v1/me") == 401 ]] && ok "bad token → 401" || fail "bad token must be 401"
curl -fsS -H "Authorization: Bearer $ANALYST" "$GW/v1/me" > "$TMP/me.json" || fail "/v1/me as analyst"
check "analyst holds cap:general (via user role), cap:sql, cap:kb, kb:moe:read" jq -e \
  '.data.permissions as $p | ["cap:general","cap:sql","cap:kb","kb:moe:read"] | all(. as $x | $p | index($x))' "$TMP/me.json"
check "no tool:* permissions (ADR-0006)" jq -e '[.data.permissions[] | select(startswith("tool:"))] | length == 0' "$TMP/me.json"
check "tenant_id is default" jq -e '.data.tenant_id == "default"' "$TMP/me.json"
check "display name is Arabic" jq -e '.data.display_name | test("\\p{Arabic}")' "$TMP/me.json"
code=$(status -H "Authorization: Bearer $VIEWER" -H 'Content-Type: application/json' \
  -d "{\"conversation_id\":\"$(uuidgen | tr 'A-Z' 'a-z')\",\"message\":\"test\",\"selected_kbs\":[\"moe\"]}" "$GW/v1/chat/turns")
[[ $code == 403 ]] && ok "viewer selecting kb moe → 403" || fail "viewer selecting kb moe returned $code"

step "general turn, streamed through gateway → agent → LiteLLM"
CONV=$(uuidgen | tr 'A-Z' 'a-z')
curl -sN -D "$TMP/headers" -H "Authorization: Bearer $ANALYST" -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -d "{\"conversation_id\":\"$CONV\",\"message\":\"ما عاصمة المملكة العربية السعودية؟ أجب بجملة واحدة.\",\"locale\":\"ar\"}" \
  "$GW/v1/chat/turns" \
  | python3 -u -c 'import sys, time
for line in sys.stdin:
    if line.startswith("data: "):
        print(f"{time.monotonic():.3f} {line[6:].rstrip()}", flush=True)' > "$TMP/events"
REQUEST_ID=$(awk -F': ' 'tolower($1) == "x-request-id" {print $2}' "$TMP/headers" | tr -d '\r')
cut -d' ' -f2- "$TMP/events" > "$TMP/events.jsonl"
check "events end with done" jq -se 'last.type == "done"' "$TMP/events.jsonl"
check "no error event" jq -se '[.[] | select(.type == "error")] | length == 0' "$TMP/events.jsonl"
check "tier event: general runs on standard" jq -se '[.[] | select(.type == "tier")][0].data.tier == "standard"' "$TMP/events.jsonl"
tokens=$(jq -se '(map(.type) | index("done")) as $done | [.[:$done][] | select(.type == "token")] | length' "$TMP/events.jsonl")
(( tokens >= 3 )) && ok "$tokens token events before done" || fail "expected at least 3 token events before done, got $tokens"
ANSWER=$(jq -rse 'last.data.answer' "$TMP/events.jsonl")
check "answer is non-empty Arabic" bash -c "[[ -n \"\$1\" ]] && grep -q '[ء-ي]' <<<\"\$1\"" _ "$ANSWER"
printf '        answer: %s\n' "$ANSWER"
TRACE_ID=$(jq -rse 'last.data.trace_id' "$TMP/events.jsonl")
[[ "$TRACE_ID" =~ ^[0-9a-f]{32}$ ]] && ok "trace id $TRACE_ID" || fail "done event has no trace id"

step "Langfuse trace with context block token counts"
found=""
for _ in $(seq 1 45); do
  if curl -fsS -u "$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY" "$LF/api/public/traces/$TRACE_ID" > "$TMP/trace.json" 2>/dev/null \
     && jq -e '.observations | map(.name) | index("context.assemble") and index("model.call")' "$TMP/trace.json" >/dev/null; then
    found=1; break
  fi
  sleep 2
done
[[ -n "$found" ]] && ok "trace ingested with context.assemble and model.call" || fail "trace $TRACE_ID not in Langfuse"
# Capabilities assemble their own packages too (ADR-0038); the answer node's is the one with a system block.
BLOCKS=$(jq -c '[.observations[] | select(.name == "context.assemble") | .metadata
  | (.block_tokens // .attributes["langfuse.observation.metadata.block_tokens"]) | if type == "string" then fromjson else . end
  | select(has("system"))][0]' "$TMP/trace.json")
check "block token counts: $BLOCKS" jq -en --argjson b "$BLOCKS" '$b.system > 0 and $b.user_message > 0'
check "model.call is a generation with usage" jq -e \
  '[.observations[] | select(.name == "model.call")][0] | .type == "GENERATION" and (.usageDetails.output // .usage.output // 0) > 0' "$TMP/trace.json"
check "trace carries session (conversation) id" jq -e --arg c "$CONV" '.sessionId == $c' "$TMP/trace.json"

step "audit record"
row=$(docker compose exec -T postgres psql -U postgres -d app -tAc \
  "select status from gateway.audit_events where request_id = '$REQUEST_ID' and action = 'chat.turn'")
[[ "$row" == "ok" ]] && ok "one chat.turn audit row with status ok" || fail "audit row for $REQUEST_ID: '$row'"

printf '\nsmoke: all phase 01 checks passed\n'
