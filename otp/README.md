# OpenTripPlanner

SmartRouteNYC uses [OpenTripPlanner](https://www.opentripplanner.org/) (OTP) to compute every itinerary. This directory contains the inputs, configuration and scripts needed to build the routing graph for New York City and the PATH-connected part of New Jersey.

## Version

| | |
|---|---|
| OTP | 2.10.0 |
| Image | `opentripplanner/opentripplanner:2.10.0@sha256:8d54e5c5…1100933` (pinned by digest) |
| API | GTFS GraphQL API at `/otp/gtfs/v1`, using `planConnection` |

OTP 2.10 requires Java 25, so it runs in Docker rather than on the host JVM.

## Data sources

Defined in [`sources.json`](sources.json). Downloaded files are written to `data/otp/graph/` and are never committed.

| Source | Feed ID | Publisher | License |
|---|---|---|---|
| Subway (supplemented with planned service changes) | `MTASBWY` | MTA | [MTA data terms](https://www.mta.info/developers) |
| Bus: Bronx, Brooklyn, Manhattan, Queens, Staten Island | `MTABX` `MTABK` `MTAMN` `MTAQN` `MTASI` | MTA | MTA data terms |
| MTA Bus Company | `MTABC` | MTA | MTA data terms |
| PATH | `PATH` | Port Authority of NY & NJ | Check before redistribution |
| Streets: NYC metropolitan extract | | OpenStreetMap via [BBBike](https://download.bbbike.org/osm/bbbike/NewYork/) | ODbL, © OpenStreetMap contributors |

The street extract covers `-74.36, 40.48, -73.67, 40.96` (west, south, east, north): the five boroughs plus Jersey City, Hoboken, Harrison and Newark. Its MD5 is checked against the checksum BBBike publishes.

## Usage

Prerequisites: Docker and Python 3.12.

```bash
# 1. Download and validate all inputs, write manifest.json
python otp/scripts/fetch_inputs.py

# 2. Build the graph (heap is configurable)
OTP_BUILD_HEAP=12g otp/scripts/build_graph.sh

# 3. Serve it
docker compose up -d otp

# 4. Check routing end to end
python otp/scripts/smoke_test.py
```

`fetch_inputs.py` retries transient failures with exponential backoff and jitter. It writes files atomically and records each source's SHA-256 checksum, size and service dates in `manifest.json`. It warns when a feed's service is about to end, and when a feed carries no stop-level accessibility data.

## Smoke tests

| Check | What it proves |
|---|---|
| `subway` | Plans Chambers St → 116 St–Columbia, then confirms that an express-to-local transfer can be **enforced and verified**: 2/3 to 96 St then 1 must verify, and 2/3 to 110 St (where the express does not stop) must be rejected |
| `path` | Plans Journal Square → Columbia University from coordinates and requires an itinerary that uses both PATH and the subway |

### Verifying a specific transfer

OTP's route filters apply to the whole trip, not to individual legs. To verify a proposal such as "take the express to 96 St, then the local", the query:

1. restricts routes to the union of the proposed lines,
2. adds a `visit` via-point at the transfer station, and
3. checks each returned itinerary leg by leg (line, boarding station, alighting station).

Timing always comes from a single OTP search, so the feed's minimum transfer times apply. Planning each segment separately and joining the results looks simpler, but it ignores those transfer times. At 96 St, where the subway feed requires 180 s, it overstates the saving by four minutes.

## Performance

Measured on a 4-core, 16 GB machine with all eight feeds and the street extract (October 2026 data):

| Metric | Value |
|---|---|
| Graph build | 9 min 9 s, 12 GB heap |
| Graph size | 466 MB; 14,336 stops; 531,466 walking transfers |
| Server memory after load | 3.3 GB |
| Plan latency, address to address | p50 631 ms, p95 953 ms (24 warm requests over 6 cross-borough trips) |

## Data notes

- **Short feed validity.** The subway feed covers about five months and PATH about four, and both are republished frequently. Inputs must be refreshed and the graph rebuilt on a schedule.
- **No accessibility in GTFS.** None of the MTA or PATH feeds set `wheelchair_boarding`. Accessibility comes from MTA's elevator and escalator feeds instead. Every active elevator in the equipment feed carries the GTFS stop ID it serves, so outages map directly onto stations in the graph.
- **Upcoming vs. current outages.** MTA's current-outage feed also lists scheduled future outages, flagged with `isupcomingoutage`. Those must be filtered out, or working elevators are reported as broken.
- **PATH stations.** The PATH feed does not group platforms under parent stations, so each station appears as several unrelated stops. Address-based planning is unaffected; stop-based queries need a station mapping.
- **Transfers.** The subway feed defines 613 transfer rules with minimum transfer times. Walking transfers between stations are routed over the OpenStreetMap network.
- **Realtime.** MTA subway, MTA bus and PATH publish GTFS-Realtime without an API key and update roughly every 15–30 s. OTP realtime updaters are not configured yet.
