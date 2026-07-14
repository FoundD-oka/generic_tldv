#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
name="generic-tldv-dictionary-test-$$"
cleanup() {
  docker rm -f "$name" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker run -d --name "$name" \
  -e POSTGRES_USER=test_user \
  -e POSTGRES_PASSWORD=test_pass \
  -e POSTGRES_DB=test_db \
  -p 127.0.0.1::5432 postgres:16-alpine >/dev/null

for _ in $(seq 1 60); do
  if docker exec "$name" pg_isready -U test_user -d test_db >/dev/null 2>&1; then break; fi
  sleep 0.5
done
port="$(docker port "$name" 5432/tcp | awk -F: '{print $NF}')"
export DATABASE_URL="postgresql://test_user:test_pass@127.0.0.1:${port}/test_db"
export DB_HOST=127.0.0.1 DB_PORT="$port" DB_NAME=test_db DB_USER=test_user DB_PASSWORD=test_pass DB_SSL_MODE=disable

python_bin="${PYTHON_BIN:-$root/.pipeline/tmp/gemini-venv/bin/python}"
ready=0
for _ in $(seq 1 60); do
  if "$python_bin" "$root/scripts/migrations/20260712_add_transcription_dictionary.py" up >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 0.5
done
if [[ "$ready" != "1" ]]; then
  echo "PostgreSQL did not accept host connections" >&2
  exit 1
fi
"$python_bin" "$root/scripts/migrations/20260712_add_transcription_dictionary.py" up
(cd "$root/services/meeting-api" && "$python_bin" -m pytest tests/integration/test_transcription_dictionary_postgres.py -q)
"$python_bin" "$root/scripts/migrations/20260712_add_transcription_dictionary.py" down
