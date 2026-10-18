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

## Refreshing timetables

Published timetables cover only a few months, so the graph must be rebuilt regularly. [`scripts/refresh_graph.sh`](scripts/refresh_graph.sh) does this without taking routing offline:

1. Fetch and validate fresh inputs into `data/otp/graph-next`. The live graph keeps serving.
2. Build the new graph there.
3. Swap directories (the previous graph is kept as `graph-previous`) and restart OTP.
4. Wait for OTP to answer, then run the smoke tests. If either fails, the previous graph is restored automatically.

Run it on a schedule, for example nightly:

```cron
30 3 * * * cd /srv/smart-route-nyc && OTP_BUILD_HEAP=10g otp/scripts/refresh_graph.sh >> /var/log/smartroute-refresh.log 2>&1
```

The ingestion worker's `gtfs_static` job reports each feed's end of service at `/api/v1/data/freshness`, so a refresh that stops running shows up there before routing starts missing trips.

## Derived station accessibility

MTA's subway GTFS leaves `wheelchair_boarding` empty, so OTP's wheelchair routing cannot tell accessible stations from inaccessible ones. `fetch_inputs.py` fills it in from MTA's elevator inventory ([`scripts/gtfs_accessibility.py`](scripts/gtfs_accessibility.py)) before the graph is built:

- `1` (accessible) for a station with at least one active ADA-accessible elevator
- `2` (not accessible) for every other subway station
- platforms inherit their parent station's value

The router configuration then penalises inaccessible stops heavily for wheelchair requests, so routing steers toward accessible stations. Only the static inventory is used here; live outages are applied when each route is assessed. The source and counts are recorded under `derived` in `manifest.json`. PATH publishes no equivalent data, so PATH stops stay unknown. If the inventory cannot be fetched, the whole fetch fails and a refresh keeps the current graph.

## Routing configuration

[`config/router-config.json`](config/router-config.json) changes one OTP default. With wheelchair routing enabled, OTP normally **excludes** stops and trips whose accessibility is unknown. None of the NYC or PATH feeds publish that information, so every stop is unknown and wheelchair searches would return nothing. The configuration penalises unknown stops and trips instead of excluding them. Street routing still strongly avoids stairs. The application then classifies each route's accessibility from separate evidence, so the penalty never stands in for a verdict.

## Graph versioning

`build_graph.sh` writes `graph-version` (a fingerprint of `graph.obj`) next to the graph. The API includes it in cache keys, so cached routes from a previous graph are never served after a rebuild.

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
- **No accessibility in GTFS.** None of the MTA or PATH feeds set `wheelchair_boarding`. For the subway it is derived from MTA's elevator inventory (see above); live status comes from MTA's outage feed. Every active elevator in the equipment feed carries the GTFS stop ID it serves, so outages map directly onto stations in the graph.
- **Upcoming vs. current outages.** MTA's current-outage feed also lists scheduled future outages, flagged with `isupcomingoutage`. Those must be filtered out, or working elevators are reported as broken.
- **PATH stations.** The PATH feed does not group platforms under parent stations, so each station appears as several unrelated stops. Address-based planning is unaffected; stop-based queries need a station mapping.
- **Transfers.** The subway feed defines 613 transfer rules with minimum transfer times. Walking transfers between stations are routed over the OpenStreetMap network.
- **Realtime.** MTA subway, MTA bus and PATH publish GTFS-Realtime without an API key and update roughly every 15–30 s. OTP realtime updaters are not configured yet.
