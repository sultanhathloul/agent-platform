#!/usr/bin/env bash
# Creates .env from .env.example. Every occurrence of __generate__ becomes a
# fresh 64-hex-character secret. An existing .env is never touched.
set -euo pipefail
cd "$(dirname "$0")/.."

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
