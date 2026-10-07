#!/usr/bin/env bash
# End-to-end smoke test against the running stack (`make up`).
# It grows each phase: phases add checks and never remove them (ADR-0009).
#   phase 01: health, PKCE login, gateway authz, streamed general turn,
#             Langfuse trace with context block token counts, audit record,
#             conversation ownership, the viewer's tier ceiling.
#   phase 02: a second turn after `docker compose restart agent` continues the
#             conversation; a memory row is written for the analyst and is
#             invisible to the viewer.
#   phase 03: fake PII in an answer never appears in a token event; a memory
#             candidate with a fake mobile number is not stored; a policy edit
#             takes effect without a restart and its block returns message_ar;
#             a role-scoped exception (policy:pii-exempt) reaches the analyst and not the
#             viewer; a second turn on a busy conversation is 409 turn_in_flight; the
#             temporary policies end switched off, on success or failure (EXIT trap), and
#             the policy row counts are equal across runs (no policy residue; conversations,
#             guard events, the policy change log, audit rows and sessions grow by design).
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a

GW=$GATEWAY_PUBLIC_URL KC=$KEYCLOAK_PUBLIC_URL LF=$LANGFUSE_PUBLIC_URL WEB=$WEB_PUBLIC_URL
TMP=$(mktemp -d)
# Smoke's temporary policies: whatever happens, the run ends with every one of them off.
SMOKE_POLICY_IDS=(smoke-live-block smoke-pii-exempt)
cleanup() {
  if [[ -n ${ADMIN:-} ]]; then
    for id in "${SMOKE_POLICY_IDS[@]}"; do
      curl -s -o /dev/null -X PUT -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' \
        -d '{"enabled":false}' "$GW/v1/admin/policies/$id/enabled" || true
    done
  fi
  rm -rf "$TMP"
}
trap cleanup EXIT
# Where the policy row counts of the last successful run are kept, to compare across runs.
SMOKE_STATE=build/smoke/policy-rows

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
  -d "{\"conversation_id\":\"$CONV\",\"message\":\"ما عاصمة المملكة العربية السعودية؟ أجب في ثلاث جمل قصيرة.\",\"locale\":\"ar\"}" \
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
# The answer leaves sentence by sentence through the DATA buffer (phase 03, I5), so three
# sentences are three token events at least.
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
# Earlier runs stored this message's items already, and a duplicate is not stored again
# (dedupe), so the analyst's own items are deleted first, through their own API.
curl -fsS -H "Authorization: Bearer $ANALYST" "$GW/v1/memory" > "$TMP/analyst-before.json" || fail "GET /v1/memory as analyst"
for id in $(jq -r '.data.items[].id' "$TMP/analyst-before.json"); do
  [[ $(status -X DELETE -H "Authorization: Bearer $ANALYST" "$GW/v1/memory/$id") == 204 ]] \
    || fail "the analyst could not delete their own item $id"
done
before=$(sql "select count(*) from agent.memory_items where tenant_id = 'default' and user_id = '$ANALYST_ID'")
(( before == 0 )) && ok "the analyst deleted their earlier items" || fail "$before item(s) left before the memory turn"
MEMORY_CONV=$(uuidgen | tr 'A-Z' 'a-z')
curl -sN -H "Authorization: Bearer $ANALYST" -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
  -d "{\"conversation_id\":\"$MEMORY_CONV\",\"message\":\"أعمل محللًا للبيانات في وزارة التعليم، وأفضّل أن تكون الإجابات مختصرة. ما عاصمة الأردن؟\",\"locale\":\"ar\"}" \
  "$GW/v1/chat/turns" | sed -n 's/^data: //p' > "$TMP/memory-turn.jsonl"
check "the memory turn ends with done" jq -se 'last.type == "done"' "$TMP/memory-turn.jsonl"
MEMORY_TURN=$(jq -sr 'last.turn_id' "$TMP/memory-turn.jsonl")
after=$(sql "select count(*) from agent.memory_items where tenant_id = 'default' and user_id = '$ANALYST_ID'")
this_turn=$(sql "select count(*) from agent.memory_items where tenant_id = 'default' and user_id = '$ANALYST_ID' and source_turn = '$MEMORY_TURN'")
(( this_turn >= 1 && after - before == this_turn )) && ok "the memory turn wrote $this_turn row(s), and only it" \
  || fail "memory rows: before $before, after $after, from this turn $this_turn"
curl -fsS -H "Authorization: Bearer $ANALYST" "$GW/v1/memory" > "$TMP/analyst-memory.json" || fail "GET /v1/memory as analyst"
turn_ids=$(sql "select id from agent.memory_items where source_turn = '$MEMORY_TURN' and user_id = '$ANALYST_ID' order by id" | sort | paste -sd, -)
check "the analyst lists exactly this turn's items" jq -e --arg ids "$turn_ids" \
  '.data.enabled and ([.data.items[].id] | sort | join(",")) == ($ids | split(",") | sort | join(","))' "$TMP/analyst-memory.json"
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

# Fresh tokens: the realm's access tokens live 300 s and the steps above take a while.
ADMIN=$(token_for admin admin) || fail "admin login"
ANALYST=$(token_for analyst analyst) || fail "analyst login"
VIEWER=$(token_for viewer viewer) || fail "viewer login"

step "phase 03: fake PII in an answer is redacted before any token leaves (ADR-0039, D3)"
# Fake numbers only (ADR-0065): they pass the format checks and nothing else.
PII_CONV=$(uuidgen | tr 'A-Z' 'a-z')
curl -sN -H "Authorization: Bearer $VIEWER" -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
  -d "{\"conversation_id\":\"$PII_CONV\",\"message\":\"انسخ النص التالي حرفيًا كما هو دون أي تعليق أو تغيير: للتواصل اتصل على 0501234567 أو راسل test.user@example.org\",\"locale\":\"ar\"}" \
  "$GW/v1/chat/turns" | sed -n 's/^data: //p' > "$TMP/pii.jsonl"
check "the PII turn ends with done" jq -se 'last.type == "done"' "$TMP/pii.jsonl"
check "pii-redact-output redacted the answer (the model repeated the PII)" jq -se \
  '[.[] | select(.type == "guard" and .data.policy_id == "pii-redact-output" and .data.action == "redact")] | length > 0' "$TMP/pii.jsonl"
check "no token event, nor all of them joined, holds the mobile number or the address" python3 -c '
import json, re, sys
events = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8")]
tokens = [e["data"]["text"] for e in events if e["type"] == "token"]
digits = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
mobile = re.compile(r"0\D?5\D?0\D?1\D?2\D?3\D?4\D?5\D?6\D?7")
for text in [*tokens, "".join(tokens)]:
    folded = text.translate(digits)
    if mobile.search(folded) or "test.user@example.org" in folded:
        sys.exit(1)
sys.exit(0 if tokens else 1)' "$TMP/pii.jsonl"
printf '        answer: %s\n' "$(jq -rse 'last.data.answer' "$TMP/pii.jsonl")"

step "phase 03: a memory candidate carrying a fake mobile number is not stored (B1)"
MOBILE_CONV=$(uuidgen | tr 'A-Z' 'a-z')
curl -sN -H "Authorization: Bearer $ANALYST" -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
  -d "{\"conversation_id\":\"$MOBILE_CONV\",\"message\":\"رقم جوالي 0559876543 وأعمل في إدارة التخطيط. لا تكرر الرقم في إجابتك. ما عاصمة سلطنة عُمان؟\",\"locale\":\"ar\"}" \
  "$GW/v1/chat/turns" | sed -n 's/^data: //p' > "$TMP/mobile.jsonl"
check "the turn ends with done, no error shown to the user" jq -se 'last.type == "done" and ([.[] | select(.type == "error")] | length == 0)' "$TMP/mobile.jsonl"
stored=$(sql "select count(*) from agent.memory_items where user_id = '$ANALYST_ID' and (text like '%0559876543%' or text like '%٠٥٥٩٨٧٦٥٤٣%' or text like '%9876543%')")
(( stored == 0 )) && ok "no memory item holds the mobile number" || fail "$stored memory item(s) hold the mobile number"
blocked=$(jq -se '[.[] | select(.type == "guard" and .data.policy_id == "pii-block-in-memory" and .data.action == "block")] | length' "$TMP/mobile.jsonl")
printf '        pii-block-in-memory blocks in this turn: %s (0 when the extractor kept no candidate with the number, or the answer repeated it and the turn was guard-flagged)\n' "$blocked"

step "phase 03: admin policy endpoints are admin-only"
code=$(status -H "Authorization: Bearer $VIEWER" "$GW/v1/admin/policies")
[[ $code == 403 ]] && ok "viewer GET /v1/admin/policies → 403" || fail "viewer GET /v1/admin/policies returned $code"
curl -fsS -H "Authorization: Bearer $ADMIN" "$GW/v1/admin/policies" > "$TMP/policies.json" || fail "admin GET /v1/admin/policies"
check "the seed policies are listed" jq -e \
  '[.data.policies[].policy.id] as $ids | ["pii-redact-output","pii-block-in-memory","prompt-injection-input","topics-politics-llm-judge","web-domain-denylist","tool-visibility-by-role"] | all(. as $x | $ids | index($x))' "$TMP/policies.json"

# Smoke's temporary policies have fixed ids and go through the import endpoint: the first run
# creates them, later runs only switch them on, and every run ends with them off (the EXIT
# trap), so a repeated run leaves the policy tables as it found them.
SMOKE_POLICIES=$(printf "'%s'," "${SMOKE_POLICY_IDS[@]}"); SMOKE_POLICIES=${SMOKE_POLICIES%,}
policy_rows() {  # versions and heads of every policy
  sql "select (select count(*) from agent.guard_policies) || ' ' || (select count(*) from agent.guard_policy_heads)"
}
rows_before=$(policy_rows)
existed_before=$(sql "select count(*) from agent.guard_policy_heads where id in ($SMOKE_POLICIES)")
: > "$TMP/request-ids"
request_id() { tr -d '\r' < "$1" | awk -F': ' 'tolower($1) == "x-request-id" {print $2}' >> "$TMP/request-ids"; }
import_policy() {  # $1: one policy as JSON; prints the HTTP status
  curl -s -D "$TMP/import.headers" -o "$TMP/imported.json" -w '%{http_code}' -X POST -H "Authorization: Bearer $ADMIN" \
    -H 'Content-Type: application/json' -d "$(jq -cn --argjson p "$1" '{yaml: ({policies: [$p]} | tojson)}')" \
    "$GW/v1/admin/policies/import"
  request_id "$TMP/import.headers"
}
switch_off() {
  curl -s -D "$TMP/switch.headers" -o /dev/null -w '%{http_code}' -X PUT -H "Authorization: Bearer $ADMIN" \
    -H 'Content-Type: application/json' -d '{"enabled":false}' "$GW/v1/admin/policies/$1/enabled"
  request_id "$TMP/switch.headers"
}

step "phase 03: a policy edit takes effect without a restart, and its block returns message_ar"
POLICY_ID=smoke-live-block
MESSAGE_AR="حُجب هذا الطلب بسياسة اختبار الفحص الشامل."
POLICY=$(jq -cn --arg id "$POLICY_ID" --arg ar "$MESSAGE_AR" '{id: $id, name: "Smoke: live keyword block", scope: "global",
  checkpoints: ["INPUT"], checker: "keyword_list", config: {keywords: ["كلمةالفحصالشامل"]}, action: "block",
  message_ar: $ar, message_en: "Blocked by the smoke test policy.", enabled: true}')
code=$(import_policy "$POLICY")
[[ $code == 200 ]] && ok "admin switched $POLICY_ID on ($(jq -c '.data | {created, switched, versioned}' "$TMP/imported.json"))" \
  || fail "importing the policy returned $code"
blocked_turn() {
  curl -sN -H "Authorization: Bearer $VIEWER" -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
    -d "{\"conversation_id\":\"$(uuidgen | tr 'A-Z' 'a-z')\",\"message\":\"سؤال فيه كلمةالفحصالشامل\",\"locale\":\"ar\"}" \
    "$GW/v1/chat/turns" | sed -n 's/^data: //p' > "$1"
}
blocked_turn "$TMP/live-blocked.jsonl"
check "the next turn is blocked, no agent restart (input_blocked)" jq -se 'last.type == "error" and last.data.code == "input_blocked"' "$TMP/live-blocked.jsonl"
check "the block returns the policy's message_ar" jq -se --arg ar "$MESSAGE_AR" 'last.data.message_ar == $ar' "$TMP/live-blocked.jsonl"
check "the error names the policy version" jq -se --arg id "$POLICY_ID" 'last.data.policy_id | startswith($id + "@")' "$TMP/live-blocked.jsonl"
code=$(switch_off "$POLICY_ID")
[[ $code == 200 ]] && ok "admin switched $POLICY_ID off" || fail "switching the policy off returned $code"
blocked_turn "$TMP/live-allowed.jsonl"
check "the turn after the switch passes" jq -se 'last.type == "done"' "$TMP/live-allowed.jsonl"
# Only this run's requests count: the audit rows are matched by the request ids the gateway returned.
RUN_IDS=$(sed "s/.*/'&'/" "$TMP/request-ids" | paste -sd, -)
audited=$(sql "select count(*) from gateway.audit_events where status = 'ok' and request_id in ($RUN_IDS)
  and ((action = 'policy.import' and (detail->'created' ? '$POLICY_ID' or detail->'switched' ? '$POLICY_ID'))
       or (action = 'policy.enable' and detail->>'policy_id' = '$POLICY_ID'))")
(( audited == 2 )) && ok "this run's two policy edits are audited, by request id" || fail "this run's policy edits audited: $audited of 2"

step "phase 03: a role-scoped exception reaches its holder only (policy:pii-exempt, ADR-0107)"
check "the analyst's token carries policy:pii-exempt" python3 -c '
import base64, json, sys
payload = sys.argv[1].split(".")[1]
claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
sys.exit(0 if "policy:pii-exempt" in claims.get("realm_access", {}).get("roles", []) else 1)' "$ANALYST"
EXEMPT=$(jq -cn '{id: "smoke-pii-exempt", name: "Smoke: PII exception for policy:pii-exempt", scope: "role:pii-exempt",
  checkpoints: ["DATA"], checker: "presidio",
  config: {entities: ["SA_MOBILE", "SA_NATIONAL_ID", "SA_IBAN", "EMAIL_ADDRESS"], score_threshold: 0.5, targets: ["answer", "summary"]},
  action: "allow", severity: "high", group: "pii-output", enabled: true}')
code=$(import_policy "$EXEMPT")
[[ $code == 200 ]] && ok "admin switched smoke-pii-exempt on" || fail "importing the exception returned $code"
curl -fsS -H "Authorization: Bearer $ADMIN" "$GW/v1/admin/policies/smoke-pii-exempt" > "$TMP/exempt.json" || fail "GET smoke-pii-exempt"
check "it is flagged an exception over pii-redact-output" jq -e \
  '.data.current.exception == true and (.data.current.overrides | any(startswith("pii-redact-output@")))' "$TMP/exempt.json"
exempt_turn() {  # $1: token, $2: output file
  curl -sN -H "Authorization: Bearer $1" -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
    -d "{\"conversation_id\":\"$(uuidgen | tr 'A-Z' 'a-z')\",\"message\":\"انسخ النص التالي حرفيًا كما هو دون أي تعليق أو تغيير: رقم التواصل 0501234567\",\"locale\":\"ar\"}" \
    "$GW/v1/chat/turns" | sed -n 's/^data: //p' > "$2"
}
exempt_turn "$ANALYST" "$TMP/exempt-analyst.jsonl"
exempt_turn "$VIEWER" "$TMP/exempt-viewer.jsonl"
code=$(switch_off smoke-pii-exempt)
[[ $code == 200 ]] && ok "admin switched smoke-pii-exempt off" || fail "switching the exception off returned $code"
check "the analyst sees the fake mobile in the answer" jq -se \
  'last.type == "done" and ([.[] | select(.type == "token") | .data.text] | join("") | contains("0501234567"))' "$TMP/exempt-analyst.jsonl"
check "the viewer, same tenant, gets it redacted in every token event" jq -se \
  'last.type == "done" and ([.[] | select(.type == "token") | .data.text] | join("") | (contains("0501234567") | not) and contains("[REDACTED:SA_MOBILE]"))' \
  "$TMP/exempt-viewer.jsonl"

step "phase 03: a second turn on a busy conversation is 409 turn_in_flight (D6)"
BUSY_CONV=$(uuidgen | tr 'A-Z' 'a-z')
curl -sN -H "Authorization: Bearer $ANALYST" -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
  -d "{\"conversation_id\":\"$BUSY_CONV\",\"message\":\"اكتب فقرة من خمس جمل عن تاريخ مدينة الرياض.\",\"locale\":\"ar\"}" \
  "$GW/v1/chat/turns" > "$TMP/busy-first.sse" &   # raw and unbuffered (-N): its first event shows the turn runs
FIRST=$!
for _ in $(seq 1 200); do grep -q '^data: ' "$TMP/busy-first.sse" 2>/dev/null && break; sleep 0.05; done
grep -q '^data: ' "$TMP/busy-first.sse" || fail "the first turn did not start streaming"
code=$(curl -s -o "$TMP/busy-second.json" -w '%{http_code}' -H "Authorization: Bearer $ANALYST" -H 'Content-Type: application/json' \
  -d "{\"conversation_id\":\"$BUSY_CONV\",\"message\":\"وما عدد سكانها؟\",\"locale\":\"ar\"}" "$GW/v1/chat/turns")
[[ $code == 409 ]] && ok "the concurrent turn → 409" || fail "the concurrent turn returned $code"
check "error code is turn_in_flight" jq -e '.error.code == "turn_in_flight"' "$TMP/busy-second.json"
wait "$FIRST"
sed -n 's/^data: //p' "$TMP/busy-first.sse" > "$TMP/busy-first.jsonl"
check "the first turn still ends with done" jq -se 'last.type == "done"' "$TMP/busy-first.jsonl"

step "phase 03: no policy residue (temporary policies off, policy row counts equal across runs)"
off=$(sql "select count(*) from agent.guard_policy_heads where id in ($SMOKE_POLICIES) and not enabled")
(( off == ${#SMOKE_POLICY_IDS[@]} )) && ok "smoke's policies are switched off" \
  || fail "$off of smoke's ${#SMOKE_POLICY_IDS[@]} policies are off"
rows_after=$(policy_rows)
read -r versions_before heads_before <<< "$rows_before"
read -r versions_after heads_after <<< "$rows_after"
created=$(( ${#SMOKE_POLICY_IDS[@]} - existed_before ))  # a first run creates smoke's policies, once
(( versions_after == versions_before + created && heads_after == heads_before + created )) \
  && ok "this run added $created policy rows (versions $versions_before → $versions_after, heads $heads_before → $heads_after)" \
  || fail "policy rows went from $rows_before to $rows_after with $created policies created"
if [[ -f $SMOKE_STATE ]]; then
  previous=$(cat "$SMOKE_STATE")
  [[ $rows_after == "$previous" ]] && ok "policy rows equal to the previous run's ($rows_after)" \
    || fail "policy rows $rows_after differ from the previous run's $previous"
else
  ok "no previous run recorded; this run's policy rows ($rows_after) are the reference"
fi
mkdir -p "$(dirname "$SMOKE_STATE")" && printf '%s\n' "$rows_after" > "$SMOKE_STATE"

printf '\nsmoke: all phase 01, 02 and 03 checks passed\n'
