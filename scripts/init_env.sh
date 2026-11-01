#!/usr/bin/env bash
# Creates .env from .env.example. Every occurrence of __generate__ becomes a
# fresh 64-hex-character secret. An existing .env is never touched, except with --add-missing:
# then each variable of .env.example that docker-compose.yml requires (written ${NAME} or
# ${NAME:?...}, no default) and .env does not assign is appended, generated the same way, and
# nothing already in .env changes (a variable a later PR added, e.g. reports, ADR-0384). Optional
# variables, which compose gives a default, are left to the operator.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "${1:-}" == "--add-missing" && -f .env ]]; then
  added=0
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^([A-Z][A-Z0-9_]*)= ]] || continue
    name="${BASH_REMATCH[1]}"
    grep -qE "^${name}=" .env && continue
    grep -qE "\\$\\{${name}(:\\?[^}]*)?\\}" docker-compose.yml || continue
    while [[ "$line" == *__generate__* ]]; do
      line="${line/__generate__/$(openssl rand -hex 32)}"
    done
    printf '%s\n' "$line" >> .env
    echo "added ${name} to .env"
    added=$((added + 1))
  done < .env.example
  echo "${added} variable(s) added to .env"
  exit 0
fi

if [[ -f .env ]]; then
  echo ".env already exists; leaving it untouched"
  exit 0
fi

umask 077
while IFS= read -r line || [[ -n "$line" ]]; do
  while [[ "$line" == *__generate__* ]]; do
    line="${line/__generate__/$(openssl rand -hex 32)}"
  done
  printf '%s\n' "$line"
done < .env.example > .env

echo "created .env with generated secrets"
