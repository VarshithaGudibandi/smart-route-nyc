#!/usr/bin/env bash
# Run SmartRouteNYC on this machine. Needs only Docker.
#
#   ./start.sh                    start (the first run downloads data and builds the routing graph)
#   ./start.sh --with-dashboards  also start Prometheus and Grafana
#   ./start.sh --refresh          rebuild the routing graph from the latest timetables
#   ./start.sh --stop             stop everything (data is kept)
#
# Written for bash 3.2, so it runs unchanged on macOS.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

GRAPH_DIR="$ROOT/data/otp/graph"
GIB=$((1024 * 1024 * 1024))
# Docker Desktop's "8 GB" setting reports about 7.6 GiB.
MIN_MEMORY_GIB=7
FIRST_RUN_DISK_GIB=5
READY_TIMEOUT_S=${READY_TIMEOUT_S:-300}
PORTS="3000 8000 8080 5432 6379"

bold=""; dim=""; red=""; green=""; reset=""
if [[ -t 1 ]]; then
  bold=$'\033[1m'; dim=$'\033[2m'; red=$'\033[31m'; green=$'\033[32m'; reset=$'\033[0m'
fi
step() { echo "${bold}==> $*${reset}"; }
note() { echo "    $*"; }
fail() {
  echo "${red}${bold}Error:${reset} $1" >&2
  shift
  for line in "$@"; do echo "       $line" >&2; done
  exit 1
}

usage() { sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'; }

dashboards=false
refresh=false
stop=false
for arg in "$@"; do
  case "$arg" in
    --with-dashboards) dashboards=true ;;
    --refresh) refresh=true ;;
    --stop) stop=true ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done

compose() {
  if $dashboards; then
    docker compose --profile dashboards "$@"
  else
    docker compose "$@"
  fi
}

check_docker() {
  command -v docker >/dev/null 2>&1 ||
    fail "Docker is not installed." "Install Docker Desktop (Mac, Windows) or Docker Engine (Linux): https://docs.docker.com/get-docker/"
  docker info >/dev/null 2>&1 ||
    fail "Docker is installed but not running." "Start Docker Desktop (or the Docker service) and run ./start.sh again."
  command -v curl >/dev/null 2>&1 || fail "curl is not installed." "Install it with your package manager."
  docker compose version >/dev/null 2>&1 ||
    fail "Docker Compose v2 is missing." "It ships with Docker Desktop; on Linux install the docker-compose-plugin package."
}

# Bytes of memory available to containers.
docker_memory() { docker info --format '{{.MemTotal}}'; }

check_memory() {
  local bytes gib
  bytes=$(docker_memory)
  gib=$((bytes / GIB))
  if ((bytes < MIN_MEMORY_GIB * GIB - GIB / 2)); then
    fail "Docker has ${gib} GB of memory; SmartRouteNYC needs 8 GB." \
      "Docker Desktop: Settings > Resources > Memory, set 8 GB or more, then Apply & restart." \
      "Linux: the machine itself needs 8 GB of RAM."
  fi
  note "Docker memory: ${gib} GB"
}

check_disk() {
  local free_kb
  free_kb=$(df -Pk "$ROOT" | awk 'NR == 2 { print $4 }')
  if ((free_kb < FIRST_RUN_DISK_GIB * 1024 * 1024)); then
    fail "Less than ${FIRST_RUN_DISK_GIB} GB of free disk space." \
      "The first run needs about ${FIRST_RUN_DISK_GIB} GB for container images, timetables, the street map and the routing graph."
  fi
}

check_ports() {
  # Our own containers hold these ports when the stack is already up.
  if [[ -n "$(docker compose ps -q --status running 2>/dev/null)" ]]; then return; fi
  local port ports=$PORTS
  if $dashboards; then ports="$ports 3001 9090"; fi
  for port in $ports; do
    if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
      fail "Port $port is already in use by another program." \
        "Stop that program (for example a local PostgreSQL, Redis or dev server) and run ./start.sh again."
    fi
  done
}

ensure_env() {
  [[ -f .env ]] && return
  local secret
  secret=$(od -An -tx1 -N32 /dev/urandom | tr -d ' \n')
  cat > .env <<EOF
# Created by start.sh. Settings for the local Docker stack; never commit this file.
KEY_HASHING_SECRET=$secret
EOF
  note "Created .env with a random key-hashing secret"
}

# Heap sizes that fit the memory Docker has. Measured with the container
# capped at 7 GB: a 5 GB heap builds the graph in under 6 minutes on 4 CPUs,
# peaking at 4.7 GB. Serving uses about 3.3 GB.
choose_heaps() {
  local gib
  gib=$(($(docker_memory) / GIB))
  if ((gib >= 14)); then
    : "${OTP_BUILD_HEAP:=10g}" "${OTP_SERVE_HEAP:=6g}"
  else
    : "${OTP_BUILD_HEAP:=5g}" "${OTP_SERVE_HEAP:=4g}"
  fi
  export OTP_BUILD_HEAP OTP_SERVE_HEAP
}

build_images() {
  step "Building the app images (a few minutes the first time)"
  compose build
}

build_graph() {
  step "Downloading timetables and the street map (about 220 MB)"
  note "MTA subway and buses, PATH, and OpenStreetMap for NYC and Hudson County"
  otp/scripts/docker_python.sh otp/scripts/fetch_inputs.py --graph-dir "$GRAPH_DIR"

  step "Building the routing graph (about 10 minutes, once)"
  note "${dim}OpenTripPlanner is combining the timetables with the street network.${reset}"
  otp/scripts/build_graph.sh "$GRAPH_DIR"
}

refresh_graph() {
  step "Rebuilding the routing graph from the latest timetables"
  note "The current graph keeps serving until the new one passes its checks."
  PYTHON="$ROOT/otp/scripts/docker_python.sh" SMOKE_OTP_URL="http://otp:8080" \
    otp/scripts/refresh_graph.sh
}

wait_for() {
  local name=$1 url=$2 deadline=$((SECONDS + READY_TIMEOUT_S))
  printf '    Waiting for %s ' "$name"
  until curl -sf -o /dev/null "$url"; do
    if ((SECONDS > deadline)); then
      echo
      return 1
    fi
    printf '.'
    sleep 3
  done
  echo " ${green}ready${reset}"
}

wait_for_stack() {
  step "Starting"
  # OTP loads a 460 MB graph, which takes about a minute.
  if ! wait_for "the routing engine and API" "http://localhost:8000/api/v1/health/ready"; then
    curl -s "http://localhost:8000/api/v1/health/ready" || true
    echo
    fail "The API did not become ready within ${READY_TIMEOUT_S} s." \
      "See what failed: docker compose logs --tail 50 otp backend"
  fi
  wait_for "the web app" "http://localhost:3000" ||
    fail "The web app did not start." "See what failed: docker compose logs --tail 50 web"
  # The worker fetches elevator status and service alerts as soon as it starts.
  local deadline=$((SECONDS + 60))
  until curl -sf "http://localhost:8000/api/v1/data/freshness" | grep -q '"mta_elevators"'; do
    if ((SECONDS > deadline)); then
      note "Live elevator data has not arrived yet; check: docker compose logs worker"
      return
    fi
    sleep 3
  done
  note "Live elevator status and service alerts loaded"
}

check_docker

if $stop; then
  step "Stopping SmartRouteNYC"
  docker compose --profile dashboards down
  exit 0
fi

step "Checking this machine"
check_memory
choose_heaps

if $refresh; then
  [[ -f "$GRAPH_DIR/graph.obj" ]] || fail "There is no graph to refresh yet." "Run ./start.sh first."
  build_images
  compose up -d
  refresh_graph
  wait_for_stack
  exit 0
fi

check_ports
ensure_env

if [[ ! -f "$GRAPH_DIR/graph.obj" ]]; then
  check_disk
  note "First run: this takes about 15 minutes. Later starts take about a minute."
  build_images
  build_graph
else
  build_images
fi

compose up -d
wait_for_stack

echo
echo "${green}${bold}SmartRouteNYC is running: http://localhost:3000${reset}"
echo "    API docs:   http://localhost:8000/api/docs"
if $dashboards; then
  echo "    Dashboards: http://localhost:3001 (Grafana; metrics from Prometheus at http://localhost:9090)"
fi
echo "    Stop with:  ./start.sh --stop"
