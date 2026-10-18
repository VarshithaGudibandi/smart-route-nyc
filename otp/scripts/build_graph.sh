#!/usr/bin/env bash
# Build the OpenTripPlanner graph from the inputs fetched by fetch_inputs.py.
#
# Usage: otp/scripts/build_graph.sh [graph-dir]
# Env:   OTP_BUILD_HEAP  JVM heap for the build (default 12g)
set -euo pipefail

GRAPH_DIR="$(cd "${1:-data/otp/graph}" && pwd)"
HEAP="${OTP_BUILD_HEAP:-12g}"
# Pinned by digest so every build uses an identical OTP binary.
OTP_IMAGE="opentripplanner/opentripplanner:2.10.0@sha256:8d54e5c589186707ee365417f2202dc878c451fa3001b8edff07019531100933"

if [[ ! -f "$GRAPH_DIR/build-config.json" ]]; then
  echo "No build-config.json in $GRAPH_DIR. Run otp/scripts/fetch_inputs.py first." >&2
  exit 1
fi

echo "Building graph in $GRAPH_DIR with heap $HEAP"
docker run --rm \
  -e JAVA_TOOL_OPTIONS="-Xmx${HEAP}" \
  -v "$GRAPH_DIR":/var/opentripplanner \
  "$OTP_IMAGE" --build --save

# The API includes this fingerprint in its cache keys, so deploying a rebuilt
# graph retires every route cached from the previous one.
if command -v sha256sum >/dev/null; then sha256=(sha256sum); else sha256=(shasum -a 256); fi  # macOS
"${sha256[@]}" "$GRAPH_DIR/graph.obj" | cut -c1-16 > "$GRAPH_DIR/graph-version"
echo "Graph written to $GRAPH_DIR/graph.obj (version $(cat "$GRAPH_DIR/graph-version"))"
