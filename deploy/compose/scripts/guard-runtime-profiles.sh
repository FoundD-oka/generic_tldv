#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-$(git rev-parse --show-toplevel)}"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT/deploy/compose/docker-compose.yml}"

if [ ! -f "$ENV_FILE" ]; then
  echo "runtime profile guard: missing env file: $ENV_FILE"
  exit 1
fi

COMPOSE_CMD=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

env_value() {
  local key="$1"
  grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2-
}

runtime_host_url() {
  local port
  port="$(env_value RUNTIME_API_PORT)"
  port="${port:-8090}"
  echo "http://127.0.0.1:${port}"
}

wait_for_profiles() {
  local url="$1"
  local output="$2"
  local attempt

  for attempt in $(seq 1 30); do
    if curl -fsS "$url/profiles" -o "$output" 2>/dev/null; then
      return 0
    fi
    sleep 2
  done

  return 1
}

profile_issues() {
  local profiles_file="$1"
  shift

  python3 - "$profiles_file" "$@" <<'PY'
import json
import sys

path = sys.argv[1]
required_profiles = sys.argv[2:] or ["meeting", "browser-session"]
with open(path, "r", encoding="utf-8") as fh:
    profiles = json.load(fh)

for name in required_profiles:
    profile = profiles.get(name)
    if not isinstance(profile, dict):
        print(f"runtime profile {name} is missing")
        continue
    image = str(profile.get("image") or "").strip()
    if not image:
        print(f"runtime profile {name}.image is empty")
PY
}

profile_notes() {
  local profiles_file="$1"
  shift

  python3 - "$profiles_file" "$@" <<'PY'
import json
import sys

path = sys.argv[1]
required_profiles = set(sys.argv[2:] or ["meeting", "browser-session"])
with open(path, "r", encoding="utf-8") as fh:
    profiles = json.load(fh)

for name in sorted(profiles):
    if name in required_profiles:
        continue
    profile = profiles.get(name)
    if not isinstance(profile, dict):
        continue
    image = str(profile.get("image") or "").strip()
    if not image:
        print(f"optional runtime profile {name}.image is empty; ignored")
PY
}

service_env_is_set() {
  local service="$1"
  local var="$2"

  if ! "${COMPOSE_CMD[@]}" ps -q "$service" | grep -q .; then
    echo "service ${service} is not running"
    return 1
  fi

  "${COMPOSE_CMD[@]}" exec -T "$service" sh -lc "test -n \"\${${var}:-}\"" >/dev/null 2>&1
}

collect_issues() {
  local profiles_file="$1"
  local runtime_url
  runtime_url="$(runtime_host_url)"
  local required_profiles
  required_profiles=(${PROFILE_GUARD_REQUIRED_PROFILES:-meeting browser-session})

  if ! wait_for_profiles "$runtime_url" "$profiles_file"; then
    echo "runtime-api /profiles is not reachable at ${runtime_url}/profiles"
    return 0
  fi

  profile_issues "$profiles_file" "${required_profiles[@]}"

  if ! service_env_is_set runtime-api BROWSER_IMAGE; then
    echo "runtime-api BROWSER_IMAGE is empty or unavailable"
  fi
  if ! service_env_is_set meeting-api BOT_IMAGE_NAME; then
    echo "meeting-api BOT_IMAGE_NAME is empty or unavailable"
  fi
  if ! service_env_is_set dashboard VEXA_API_KEY; then
    echo "dashboard VEXA_API_KEY is empty or unavailable"
  fi
}

collect_notes() {
  local profiles_file="$1"
  local required_profiles
  required_profiles=(${PROFILE_GUARD_REQUIRED_PROFILES:-meeting browser-session})

  if [ ! -s "$profiles_file" ]; then
    return 0
  fi

  profile_notes "$profiles_file" "${required_profiles[@]}"
}

read_issues() {
  local profiles_file="$1"
  local issues_raw
  issues_raw="$(collect_issues "$profiles_file" || true)"
  if [ -n "$issues_raw" ]; then
    printf '%s\n' "$issues_raw"
  fi
}

print_notes() {
  local profiles_file="$1"
  local notes
  notes="$(collect_notes "$profiles_file" || true)"
  if [ -n "$notes" ]; then
    printf '%s\n' "$notes" | sed 's/^/  note: /'
  fi
}

repair_services() {
  echo "runtime profile guard: recreating services with $ENV_FILE"
  "${COMPOSE_CMD[@]}" up -d --force-recreate admin-api runtime-api meeting-api api-gateway dashboard
}

main() {
  local repair="${PROFILE_GUARD_REPAIR:-1}"
  PROFILE_GUARD_PROFILES_FILE="$(mktemp)"
  trap 'rm -f "${PROFILE_GUARD_PROFILES_FILE:-}"' EXIT

  local issues
  issues="$(read_issues "$PROFILE_GUARD_PROFILES_FILE")"

  if [ -z "$issues" ]; then
    echo "runtime profile guard: ok"
    print_notes "$PROFILE_GUARD_PROFILES_FILE"
    return 0
  fi

  echo "runtime profile guard: found deployment config issue(s):"
  printf '%s\n' "$issues" | sed 's/^/  - /'

  if [ "$repair" != "1" ]; then
    echo "runtime profile guard: repair disabled"
    return 1
  fi

  repair_services

  issues="$(read_issues "$PROFILE_GUARD_PROFILES_FILE")"
  if [ -n "$issues" ]; then
    echo "runtime profile guard: still failing after repair:"
    printf '%s\n' "$issues" | sed 's/^/  - /'
    return 1
  fi

  echo "runtime profile guard: repaired and verified"
  print_notes "$PROFILE_GUARD_PROFILES_FILE"
}

main "$@"
