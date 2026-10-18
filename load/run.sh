#!/usr/bin/env bash
# Run the k6 load tests against the local stack started by ./start.sh.
#
# Usage: load/run.sh [mixed|same_search|otp_outage ...]   (default: all three)
#
# k6 runs in Docker on the Compose network. The rate limits are raised while
# the tests run, because all traffic comes from one address, and restored
# afterwards. otp_outage stops OpenTripPlanner and starts it again at the end.
# Results are written to load/results/<scenario>.json.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
K6_IMAGE="grafana/k6:1.8.2@sha256:bd6ae8607c0a05d0dc0dc3f5753c464b0929baf083ef0f20b7eed845a3d9e6b4"
LOAD_COMPOSE=(docker compose -f docker-compose.yml -f load/compose.load.yml)
scenarios=("$@")
if ((${#scenarios[@]} == 0)); then scenarios=(mixed same_search otp_outage); fi

wait_ready() {
  local deadline=$((SECONDS + 300))
  until curl -sf -o /dev/null http://localhost:8000/api/v1/health/ready; do
    if ((SECONDS > deadline)); then
      echo "The API is not ready; is the stack running (./start.sh)?" >&2
      return 1
    fi
    sleep 3
  done
}

# shellcheck disable=SC2329  # called by the EXIT trap
restore() {
  echo "Restoring normal rate limits"
  docker compose start otp >/dev/null
  docker compose up -d backend >/dev/null
  wait_ready
}
trap restore EXIT

missed=0
mkdir -p load/results
"${LOAD_COMPOSE[@]}" up -d backend >/dev/null
wait_ready

for scenario in "${scenarios[@]}"; do
  echo "==> $scenario"
  if [[ "$scenario" == otp_outage ]]; then docker compose stop otp >/dev/null; fi
  status=0
  docker run --rm \
    --network smartroute_default \
    --user "$(id -u):$(id -g)" \
    -v "$ROOT:/repo:ro" -v "$ROOT/load/results:/results" -w /repo/load \
    -e SCENARIO="$scenario" -e API_URL=http://backend:8000 -e SUMMARY_DIR=/results \
    -e K6_NO_USAGE_REPORT=true \
    "$K6_IMAGE" run --quiet smartroute.js || status=$?
  if [[ "$scenario" == otp_outage ]]; then
    docker compose start otp >/dev/null
    wait_ready
  fi
  # Exit code 99: a threshold was missed. Report it and run the rest.
  if ((status == 99)); then
    echo "    $scenario missed a threshold"
    missed=1
  elif ((status != 0)); then
    exit "$status"
  fi
done
exit "$missed"
