#!/usr/bin/env bash
# Run one of the OTP scripts with the backend image's Python, so the host
# needs only Docker. The repository is mounted at its own path, so absolute
# paths mean the same thing inside and outside the container, and files are
# written as the calling user.
#
# Usage: otp/scripts/docker_python.sh otp/scripts/fetch_inputs.py [args...]
# Env:   SMARTROUTE_DOCKER_ARGS  extra `docker run` flags, e.g. proxy settings
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
IMAGE="${SMARTROUTE_TOOLS_IMAGE:-smartroute-backend}"
# On the Compose network, the smoke tests can reach OTP as http://otp:8080.
NETWORK="smartroute_default"

network_args=()
if [[ "${SMARTROUTE_DOCKER_ARGS:-}" != *--network* ]] &&
  docker network inspect "$NETWORK" >/dev/null 2>&1; then
  network_args=(--network "$NETWORK")
fi

# shellcheck disable=SC2086  # SMARTROUTE_DOCKER_ARGS is a list of flags
exec docker run --rm \
  --user "$(id -u):$(id -g)" \
  -e HOME=/tmp \
  -v "$ROOT:$ROOT" -w "$ROOT" \
  ${network_args[@]+"${network_args[@]}"} \
  ${SMARTROUTE_DOCKER_ARGS:-} \
  "$IMAGE" python "$@"
