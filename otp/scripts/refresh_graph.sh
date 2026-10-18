#!/usr/bin/env bash
# Rebuild the routing graph from fresh feeds and deploy it with rollback.
#
#   1. Fetch and validate inputs into data/otp/graph-next
#   2. Build the graph there (the live graph keeps serving meanwhile)
#   3. Swap directories, restart OTP, run the routing smoke tests
#   4. If OTP does not come up or a smoke test fails, restore the previous graph
#
# Intended to run on a schedule (e.g. nightly cron) because published
# timetables expire every few months.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DATA="$ROOT/data/otp"
LIVE="$DATA/graph"
NEXT="$DATA/graph-next"
PREVIOUS="$DATA/graph-previous"
OTP_URL="${OTP_URL:-http://localhost:8080}"
READY_TIMEOUT_S="${READY_TIMEOUT_S:-300}"

log() { echo "[$(date -u +%H:%M:%S)] $*"; }

wait_for_otp() {
  local deadline=$((SECONDS + READY_TIMEOUT_S))
  until curl -sf -X POST -H 'Content-Type: application/json' \
      -d '{"query":"{ __typename }"}' "$OTP_URL/otp/gtfs/v1" >/dev/null; do
    if ((SECONDS > deadline)); then return 1; fi
    sleep 5
  done
}

rollback() {
  log "Rolling back to the previous graph"
  rm -rf "$LIVE"
  mv "$PREVIOUS" "$LIVE"
  docker compose -f "$ROOT/docker-compose.yml" restart otp
  wait_for_otp && log "Previous graph restored" || log "OTP did not recover; manual action needed"
  exit 1
}

rm -rf "$NEXT"
log "Fetching inputs"
python3 "$ROOT/otp/scripts/fetch_inputs.py" --graph-dir "$NEXT"

log "Building graph"
"$ROOT/otp/scripts/build_graph.sh" "$NEXT"

log "Swapping graphs"
rm -rf "$PREVIOUS"
mv "$LIVE" "$PREVIOUS"
mv "$NEXT" "$LIVE"
docker compose -f "$ROOT/docker-compose.yml" restart otp

log "Waiting for OTP"
wait_for_otp || rollback

log "Running smoke tests"
python3 "$ROOT/otp/scripts/smoke_test.py" --url "$OTP_URL" || rollback

log "Deployed graph version $(cat "$LIVE/graph-version")"
