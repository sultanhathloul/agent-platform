#!/usr/bin/env bash
# End-to-end smoke test against the running stack (`make up`).
# It grows each phase: phases add checks and never remove them (ADR-0009).
#   phase 01: health, PKCE login, gateway authz, streamed general turn,
#             Langfuse trace with context block token counts, audit record,
#             conversation ownership, the viewer's tier ceiling.
#   phase 02: a second turn after `docker compose restart agent` continues the
#             conversation; a memory row is written for the analyst and is
#             invisible to the viewer.
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
     && jq -e '(.observations | map(.name) | index("context.assemble") and index("model.call")) and .sessionId != null' \
        "$TMP/trace.json" >/dev/null; then
    # The root span, which carries the session, ends after the memory write (ADR-0058), so wait for it too.
    found=1; break
  fi
  sleep 2
done
[[ -n "$found" ]] && ok "trace ingested with context.assemble and model.call" || fail "trace $TRACE_ID not in Langfuse"
# Capabilities and the memory writer assemble their own packages too (ADR-0038, ADR-0058); the answer node's is
# selected by its node name.
BLOCKS=$(jq -c '[.observations[] | select(.name == "context.assemble") | .metadata
  | {context: (.context // .attributes["langfuse.observation.metadata.context"]),
     blocks: (.block_tokens // .attributes["langfuse.observation.metadata.block_tokens"])}
  | map_values(if type == "string" then fromjson else . end)
  | select(.context.node == "answer") | .blocks][0]' "$TMP/trace.json")
check "block token counts: $BLOCKS" jq -en --argjson b "$BLOCKS" '$b.system > 0 and $b.user_message > 0'
check "model.call is a generation with usage" jq -e \
  '[.observations[] | select(.name == "model.call")][0] | .type == "GENERATION" and (.usageDetails.output // .usage.output // 0) > 0' "$TMP/trace.json"
check "trace carries session (conversation) id" jq -e --arg c "$CONV" '.sessionId == $c' "$TMP/trace.json"

step "audit record"
row=$(docker compose exec -T postgres psql -U postgres -d app -tAc \
  "select status from gateway.audit_events where request_id = '$REQUEST_ID' and action = 'chat.turn'")
[[ "$row" == "ok" ]] && ok "one chat.turn audit row with status ok" || fail "audit row for $REQUEST_ID: '$row'"

step "conversation ownership (ADR-0041)"
code=$(curl -s -o "$TMP/forbidden.json" -w '%{http_code}' -H "Authorization: Bearer $VIEWER" \
  -H 'Content-Type: application/json' \
  -d "{\"conversation_id\":\"$CONV\",\"message\":\"ماذا سأل صاحب هذه المحادثة؟\",\"locale\":\"ar\"}" "$GW/v1/chat/turns")
[[ $code == 403 ]] && ok "viewer reusing the analyst's conversation_id → 403" \
  || fail "viewer reusing the analyst's conversation_id returned $code"
check "error code is conversation_forbidden" jq -e '.error.code == "conversation_forbidden"' "$TMP/forbidden.json"

step "viewer turn runs on standard (caps.role:viewer, ADR-0049)"
check "viewer token carries the viewer realm role" python3 -c '
import base64, json, sys
payload = sys.argv[1].split(".")[1]
claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
sys.exit(0 if "viewer" in claims.get("realm_access", {}).get("roles", []) else 1)' "$VIEWER"
VIEWER_CONV=$(uuidgen | tr 'A-Z' 'a-z')
curl -sN -H "Authorization: Bearer $VIEWER" -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
  -d "{\"conversation_id\":\"$VIEWER_CONV\",\"message\":\"ما عاصمة مصر؟ أجب بكلمة واحدة.\",\"locale\":\"ar\"}" \
  "$GW/v1/chat/turns" | sed -n 's/^data: //p' > "$TMP/viewer.jsonl"
check "viewer events end with done" jq -se 'last.type == "done"' "$TMP/viewer.jsonl"
check "every viewer tier event is standard" \
  jq -se '[.[] | select(.type == "tier")] | length > 0 and all(.data.tier == "standard")' "$TMP/viewer.jsonl"

sql() { docker compose exec -T postgres psql -U postgres -d app -tAc "$1"; }

step "state survives an agent restart (ADR-0055)"
docker compose restart agent >/dev/null 2>&1 || fail "docker compose restart agent"
docker compose up -d --wait --wait-timeout 300 agent >/dev/null 2>&1 && ok "agent is healthy again" \
  || fail "agent did not become healthy after the restart"
ANALYST=$(token_for analyst analyst) || fail "analyst login"
curl -sN -H "Authorization: Bearer $ANALYST" -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
  -d "{\"conversation_id\":\"$CONV\",\"message\":\"وما أكبر مدنها من حيث عدد السكان؟ أجب بجملة واحدة.\",\"locale\":\"ar\"}" \
  "$GW/v1/chat/turns" | sed -n 's/^data: //p' > "$TMP/restarted.jsonl"
check "the second turn after the restart ends with done" jq -se 'last.type == "done"' "$TMP/restarted.jsonl"
check "the conversation continues at turn 2" jq -se 'last.data.state_digest.turn == 2' "$TMP/restarted.jsonl"

step "memory: written for the analyst, invisible to the viewer (ADR-0058, ADR-0059)"
VIEWER=$(token_for viewer viewer) || fail "viewer login"
ANALYST_ID=$(jq -r '.data.user_id' "$TMP/me.json")
code=$(status -X PUT -H "Authorization: Bearer $ANALYST" -H 'Content-Type: application/json' -d '{"enabled":true}' \
  "$GW/v1/memory/settings")
[[ $code == 200 ]] && ok "the analyst's memory is on" || fail "PUT /v1/memory/settings returned $code"
MEMORY_CONV=$(uuidgen | tr 'A-Z' 'a-z')
curl -sN -H "Authorization: Bearer $ANALYST" -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
  -d "{\"conversation_id\":\"$MEMORY_CONV\",\"message\":\"أعمل محللًا للبيانات في وزارة التعليم، وأفضّل أن تكون الإجابات مختصرة. ما عاصمة الأردن؟\",\"locale\":\"ar\"}" \
  "$GW/v1/chat/turns" | sed -n 's/^data: //p' > "$TMP/memory-turn.jsonl"
check "the memory turn ends with done" jq -se 'last.type == "done"' "$TMP/memory-turn.jsonl"
rows=$(sql "select count(*) from agent.memory_items where tenant_id = 'default' and user_id = '$ANALYST_ID'")
(( rows >= 1 )) && ok "$rows memory row(s) for the analyst" || fail "no memory row for the analyst"
curl -fsS -H "Authorization: Bearer $ANALYST" "$GW/v1/memory" > "$TMP/analyst-memory.json" || fail "GET /v1/memory as analyst"
check "the analyst lists their memory" jq -e '.data.enabled and (.data.items | length) >= 1' "$TMP/analyst-memory.json"
ITEM=$(jq -r '.data.items[0].id' "$TMP/analyst-memory.json")
printf '        memory: %s\n' "$(jq -r '.data.items[0].text' "$TMP/analyst-memory.json")"
curl -fsS -H "Authorization: Bearer $VIEWER" "$GW/v1/memory" > "$TMP/viewer-memory.json" || fail "GET /v1/memory as viewer"
check "the analyst's item is not in the viewer's memory" \
  jq -e --arg id "$ITEM" '[.data.items[].id] | index($id) | not' "$TMP/viewer-memory.json"
code=$(curl -s -o "$TMP/viewer-delete.json" -w '%{http_code}' -X DELETE -H "Authorization: Bearer $VIEWER" \
  "$GW/v1/memory/$ITEM")
[[ $code == 404 ]] && ok "the viewer deleting the analyst's item → 404" || fail "the viewer's delete returned $code"
check "error code is memory_not_found" jq -e '.error.code == "memory_not_found"' "$TMP/viewer-delete.json"
[[ $(sql "select count(*) from agent.memory_items where id = '$ITEM'") == 1 ]] && ok "the analyst's item is still stored" \
  || fail "the analyst's item is gone"
denied=$(sql "select count(*) from gateway.audit_events where action = 'memory.delete' and status = 'denied' and detail->>'item_id' = '$ITEM'")
(( denied >= 1 )) && ok "the refused delete is audited as denied" || fail "no denied memory.delete audit row"

printf '\nsmoke: all phase 01 and phase 02 checks passed\n'
