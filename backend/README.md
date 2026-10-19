# SmartRouteNYC API

FastAPI service that plans routes with OpenTripPlanner, checks them against a rider's accessibility needs, investigates faster transfers in the background, explains routes from verified facts, and stores anonymous feedback. A separate worker process keeps elevator, alert and feed-freshness data current.

## Development

The backend uses [uv](https://docs.astral.sh/uv/).

```bash
cd backend
uv sync                              # install dependencies from uv.lock
uv run pytest tests/unit             # unit tests, no Docker needed
uv run pytest                        # unit + integration (starts PostgreSQL and Redis containers)
uv run mypy app tests evals alembic scripts
uv run alembic upgrade head          # apply migrations to DATABASE_URL
uv run python -m app.ingestion --once                     # run every ingestion job once
uv run python -m app.ingestion --once --job mta_elevators # or one of them
```

Interactive API docs are served at http://localhost:8000/api/docs when `APP_ENV` is not `production`.

## Layout

```text
app/
  api/v1/          HTTP endpoints
  services/        route search, accessibility, suggestions, explanations, places, feedback
  ai/              model-facing code: prompts, strict schemas, grounding checks
  integrations/    OpenTripPlanner GraphQL client
  ingestion/       worker: MTA elevators and alerts, realtime feed freshness, retention
  core/            settings, errors, middleware, rate limiting, circuit breakers, metrics
  cache/           Redis client that degrades safely when Redis is unavailable
  db/, schemas/    SQLAlchemy models and Pydantic request/response models
alembic/           migrations
evals/             offline evaluations with labelled cases
tests/             unit and integration tests (fixtures recorded from live sources)
```

## AI model

Any provider works. `app/ai/anthropic_provider.py` calls Claude through Anthropic's SDK; `app/ai/openai_compatible.py` calls any service with the OpenAI-compatible chat API (OpenAI, Gemini, Groq, Mistral, OpenRouter, Ollama). With `LLM_PROVIDER=auto` (the default), a key starting `sk-ant-` uses Claude, any other key the OpenAI-compatible API, and no key the rule-based baselines.

| Setting | Default | |
|---|---|---|
| `LLM_API_KEY` | none | The provider's API key |
| `LLM_MODEL` | `claude-opus-5-5` for Claude keys | Required for other providers |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | The provider's OpenAI-compatible endpoint |
| `LLM_EFFORT` | `low` | Claude only; short, structured tasks |
| `LLM_TIMEOUT_S` | `20` | Per call; one retry for rate limits, server errors and network failures |
| `LLM_DAILY_CALL_LIMIT` | `2000` | Shared across instances through Redis |

A reply that is refused, cut off or fails validation is discarded and the rule-based result is used. Each call is logged with its task, model, latency and token counts, never the prompt.

## Route search

`POST /api/v1/routes/search` returns up to five itineraries with every leg, its timing and its geometry.

```bash
curl -s localhost:8000/api/v1/routes/search -H 'Content-Type: application/json' -d '{
  "origin":      {"lat": 40.73301, "lon": -74.06289, "label": "Journal Square"},
  "destination": {"lat": 40.80755, "lon": -73.96449, "label": "Columbia University"},
  "accessibility": {"step_free_required": true}
}'
```

| Behavior | Detail |
|---|---|
| Caching | Redis, keyed by rounded coordinates (about 11 m), a 5-minute departure bucket, every preference and the routing graph version. `X-Cache` reports `HIT`, `MISS` or `SHARED` |
| Coalescing | Identical concurrent searches share one routing computation, within and across instances |
| Rate limiting | 20 searches per minute per client, shared across instances; `429` with `Retry-After` |
| Failures | `503 routing_unavailable` or `504 routing_timeout`; errors are not cached |
| Privacy | Cache and rate-limit keys are HMACs; no coordinates or client addresses are stored readably |

## Accessibility assessment

Every route is assessed on every response, including cached ones, so verdicts use the latest elevator status. Each station and walking leg gets a finding:

| Place | `meets` | `violates` | `unknown` |
|---|---|---|---|
| Subway station | A usable elevator, none out of service, data fresh | No usable elevator, or all out of service | Some elevators out, or elevator data stale |
| Walking leg | No stairs mapped in OpenStreetMap | Stairs mapped, or an entrance tagged not wheelchair accessible | |
| PATH station, bus stop | | | Always: no equipment data is published |

A route `violates` if any finding does, `meets` only if all do, and is `unknown` otherwise. Wheelchair users need an ADA-accessible elevator; step-free travel can use any working one. When a requirement is set, only complying routes are returned in `routes`; everything else goes to `review_only_routes` with each barrier and unconfirmed step listed. Walking findings rely on OpenStreetMap and say "no stairs found" rather than guaranteeing a step-free walk.

## Typed preferences

`POST /api/v1/preferences/interpret` turns a rider's words ("I use a wheelchair and hate changing trains") into the same settings the on-screen controls produce, for the rider to confirm. Preset controls never call it.

- The model only proposes; code decides. Replies must match a strict schema or are discarded (`502`).
- Text can add requirements but never remove one the rider selected; such attempts are reported as conflicts.
- The rider's text is sent as delimited data. Calls have a timeout, one retry, a circuit breaker and a daily budget shared through Redis.
- Without an API key, or when the model call fails (a bad key, no credit, an outage, an unusable reply), keyword rules answer instead, reported as `provider: "baseline"`; the failure is logged as `preferences_model_failed`.

## Transfer suggestions

OpenTripPlanner ranks itineraries by a cost that penalizes transfers, so it often prefers a one-seat ride over a faster trip with changes. After each search a background investigation looks for those:

1. **Facts.** For the fastest route's subway segment, a map of the corridor ahead: every interchange (a station more than one line serves on the way), and for each pair of stations the lines between them, with stop counts and headways from the trips running in that hour. A line that runs local at night shows local stop counts then.
2. **Plans.** A plan rides from the boarding station to the alighting station, changing at up to three interchanges: express then local, local then express, or local, express and back to the local (the 1 to the 2/3 at Chambers St, back to the 1 at 96 St). With an API key the model (`app/ai/suggestions.py`) plans from the map; a rules planner walks every path with up to two changes and ranks them by estimated minutes.
3. **Checks.** IDs must come from the facts, every ride must run between its stations, and no line may be suspended.
4. **Verification.** One OTP query restricted to the plan's lines and forced through each transfer station in order, under the rider's settings. It must ride exactly those lines, arrive at least a minute earlier and meet any accessibility requirement.

With **Fewer transfers** selected, no investigation runs. A change between lines on the same platform takes 1 minute in the graph, where MTA's timetable allows 3 ([`otp/README.md`](../otp/README.md#same-platform-transfers)). Any route that relies on that, from a search or a suggestion, carries a `SAME_PLATFORM_CHANGE` note, shown as "1-min change assumed".

The search response includes `{"investigation": {"id": "...", "poll_after_ms": 1500}}`; poll `GET /api/v1/routes/investigations/{id}`, cancel with `DELETE`. Identical searches share one investigation (Redis `SET NX`); at most 2 run and 16 wait per instance, each with a 20 s timeout. Each proposal's outcome is kept in `suggestion_outcomes` for 180 days, with lines and stations only.

## Route explanations

`POST /api/v1/routes/explain` takes a `search_id` and `route_id` from a recent search, so the server only explains routes it returned. The explanation is built from facts computed by code: every leg and transfer, every accessibility finding with its source and age, alerts in effect while the rider is on each line, and what the data cannot say. Without a model, the explanation is the key facts the page does not already list (the overall trip, transfers, the accessibility verdict, alerts, stale data). A model writes a short summary of 2 to 5 sentences instead, and its reply is replaced by those facts unless every sentence cites real facts, uses only numbers, names and accessibility words found in them, and every required fact (barriers, unconfirmed steps, alerts, stale data) is cited.

## Feedback

`POST /api/v1/feedback`: 1 to 5 stars and an optional comment, for a result issued in the last 15 minutes. Stored: the stars, the comment with emails and phone numbers masked, and an HMAC of the result reference; no locations or client address. Rating again replaces the earlier rating. 5 per minute per client; comments are cleared after 12 months.

## Place search

`GET /api/v1/places?q=` returns stations first (built from the routing graph, with station complexes merged and common spellings matched), then addresses from [Photon](https://github.com/komoot/photon), called from the backend only, cached and behind a circuit breaker. If Photon is unavailable, stations still return with a notice. `PHOTON_BASE_URL` points it at a self-hosted instance.

## Data ingestion

The `worker` service (`python -m app.ingestion`):

| Job | Every | What it does |
|---|---|---|
| `mta_elevators` | 2 min | Elevator and escalator inventory and current outages, mapped to GTFS stops |
| `mta_subway_alerts` | 2 min | Service alerts: delays, suspensions, planned work |
| `*_realtime` | 1 min | How current each GTFS-Realtime feed is |
| `gtfs_static` | 1 h | When each published timetable stops covering service |
| `retention` | 1 day | Deletes records past their retention period |

Runs are recorded in `feed_sync_runs`; unchanged payloads are skipped by checksum, writes are idempotent, an older observation never overwrites a newer one, and an empty or malformed feed is rejected instead of wiping data. `GET /api/v1/data/freshness` reports each source as fresh, stale or expired.

## Evaluations

| Evaluation | Cases | Result | Run |
|---|---|---|---|
| Typed preferences, keyword baseline | 30 labelled requests, including injection attempts | 93% fully correct, 100% recall on wheelchair and step-free needs, 0 false positives | `uv run python -m evals.preferences` |
| Explanation grounding | 23 scripted model replies | All faithful replies accepted; all unfaithful and malformed ones rejected; 2 known gaps documented | `uv run python -m evals.explanations` |
| Transfer suggestions | 30 trips × 4 times, without step-free, live graph | A verified faster route for 6.7% of searches, saving up to 9 min (median 4.0). For 11 searches the fastest baseline route already uses a 1-minute same-platform change, so the express-and-back plans appear as OTP's own routes rather than as suggestions | `uv run python -m evals.suggestions` (needs the running stack) |

The first two run in CI and fail it on regressions in critical recall, false positives, injection safety or grounding.

To measure the configured model on the same cases (needs `LLM_API_KEY`; each run makes real API calls):

```bash
uv run python -m evals.preferences --provider model
uv run python -m evals.explanations --live 10        # score 10 of the model's explanations
uv run python -m evals.suggestions --generator model # the model proposes alongside the rules
```

## Observability

The API and worker serve Prometheus metrics on port 9100 inside the Compose network only. Labels use route templates, never locations or IDs. `./start.sh --with-dashboards` adds Prometheus and a provisioned Grafana dashboard at http://localhost:3001: traffic, latency and errors by route, cache results, OpenTripPlanner latency and circuit-breaker refusals, investigations, and time since each feed was last fetched. Logs are structured JSON with a request ID, returned in `X-Request-ID`.

## Load tests

[k6](https://k6.io) scenarios in [`../ops/load/smartroute.js`](../ops/load/smartroute.js), run in Docker against the local stack with `ops/load/run.sh`:

| Scenario | Checks |
|---|---|
| `mixed` | Searches over 30 real trips, place lookups and investigation polls, 1 to 8 requests per second |
| `same_search` | 50 identical concurrent searches: exactly one reaches OpenTripPlanner |
| `otp_outage` | OpenTripPlanner stopped: `503 routing_unavailable` within a second |

## Security

- The API sends `Content-Security-Policy: default-src 'none'`, `nosniff`, `no-referrer`, and HSTS in production.
- With `APP_ENV=production` it refuses to start with the development secret, a short secret, non-https or wildcard CORS origins, or the local database password, and disables the interactive docs.
- CI audits shipped Python and npm packages, scans both images with Trivy, and checks for committed secrets.
