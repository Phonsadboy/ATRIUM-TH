#!/usr/bin/env bash
set -euo pipefail

LABEL="com.atrium.system"
SERVICE="gui/$(id -u)/$LABEL"
URL="${ATRIUM_WATCHDOG_URL:-http://127.0.0.1:8787/api/runtime}"
HEALTH_URL="${ATRIUM_WATCHDOG_HEALTH_URL:-http://127.0.0.1:8787/health}"
TIMEOUT="${ATRIUM_WATCHDOG_TIMEOUT:-20}"
MISSES_BEFORE_RESTART="${ATRIUM_WATCHDOG_MISSES_BEFORE_RESTART:-5}"
STATE_FILE="${ATRIUM_WATCHDOG_STATE_FILE:-${TMPDIR:-/tmp}/atrium-watchdog.state}"

mkdir -p "$(dirname "$STATE_FILE")"

read_misses() {
  if [[ -f "$STATE_FILE" ]]; then
    local value
    value="$(tr -cd '0-9' < "$STATE_FILE" || true)"
    if [[ -n "$value" ]]; then
      echo "$value"
      return
    fi
  fi
  echo "0"
}

clear_misses() {
  rm -f "$STATE_FILE"
}

record_failure() {
  local reason="$1"
  local misses
  misses="$(read_misses)"
  misses=$((misses + 1))
  printf '%s\n' "$misses" > "$STATE_FILE"
  if (( misses < MISSES_BEFORE_RESTART )); then
    echo "$(date '+%Y-%m-%dT%H:%M:%S%z') watchdog miss $misses/$MISSES_BEFORE_RESTART for $LABEL: $reason" >&2
    exit 0
  fi
  restart_service "$reason after $misses consecutive misses"
}

restart_service() {
  local reason="$1"
  echo "$(date '+%Y-%m-%dT%H:%M:%S%z') restarting $LABEL: $reason" >&2
  clear_misses
  if ! launchctl print "$SERVICE" >/dev/null 2>&1; then
    echo "$(date '+%Y-%m-%dT%H:%M:%S%z') cannot restart $LABEL: service is not loaded" >&2
    exit 0
  fi
  launchctl kickstart -k "$SERVICE"
}

probe_payload() {
  local payload="$1"
  python3 - "$payload" <<'PY'
import json
import sys

try:
    data = json.loads(sys.argv[1])
except Exception:
    print("invalid-json")
    raise SystemExit(2)

engine = data.get("engine") or {}
jobs = data.get("jobs") or {}
if data.get("ok") is not True:
    print("not-ok")
    raise SystemExit(2)
if engine.get("stale"):
    print("engine-stale")
    raise SystemExit(2)
if jobs.get("staleRunning"):
    print("stale-running-jobs")
    raise SystemExit(2)
if data.get("running") is False:
    print("company-paused")
    raise SystemExit(0)
print("ok")
PY
}

payload="$(curl -fsS --max-time "$TIMEOUT" "$URL" 2>/dev/null || true)"
if [[ -z "$payload" ]]; then
  health_payload="$(curl -fsS --max-time "$TIMEOUT" "$HEALTH_URL" 2>/dev/null || true)"
  if [[ -n "$health_payload" ]]; then
    set +e
    health_output="$(probe_payload "$health_payload")"
    health_status=$?
    set -e
    if [[ "$health_status" -eq 0 ]]; then
      clear_misses
      echo "health-fallback-ok"
      exit 0
    fi
  fi
  record_failure "runtime endpoint unreachable"
fi

set +e
probe_output="$(probe_payload "$payload")"
status=$?
set -e
echo "$probe_output"

case "$status" in
  0)
    clear_misses
    exit 0
    ;;
  2)
    reason="$(python3 - "$payload" <<'PY'
import json
import sys
try:
    data = json.loads(sys.argv[1])
except Exception:
    print("invalid runtime payload")
    raise SystemExit(0)
engine = data.get("engine") or {}
jobs = data.get("jobs") or {}
if engine.get("stale"):
    print(f"engine stale tickAgeMs={engine.get('tickAgeMs')}")
elif jobs.get("staleRunning"):
    print(f"stale running jobs={len(jobs.get('staleRunning') or [])}")
else:
    print("runtime unhealthy")
PY
)"
    record_failure "$reason"
    ;;
  *)
    record_failure "watchdog probe failed"
    ;;
esac
