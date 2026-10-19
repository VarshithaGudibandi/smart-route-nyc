// Load tests for the SmartRouteNYC API, run with k6 in Docker (see README.md).
//
//   SCENARIO=mixed        searches, place lookups and investigation polls at rising rates
//   SCENARIO=same_search  50 people send the same search at the same moment
//   SCENARIO=otp_outage   searches while OpenTripPlanner is stopped
//
// Trips are the 30 real origin and destination pairs used by the transfer
// suggestion evaluation.
import http from "k6/http";
import { check, sleep } from "k6";
import { Counter } from "k6/metrics";

const BASE = __ENV.API_URL || "http://localhost:8000";
const SCENARIO = __ENV.SCENARIO || "mixed";
const TRIPS = open("../../backend/evals/suggestion_trips.jsonl")
  .split("\n")
  .filter((line) => line.trim())
  .map((line) => JSON.parse(line));
const PLACE_QUERIES = ["times sq", "herald", "columbia", "journal sq", "bay ridge", "fulton", "grand central", "court sq"];
const HEADERS = { headers: { "Content-Type": "application/json" } };

const cacheResults = new Counter("route_search_cache");
// Responses by status and error code, so a failed check can be explained.
const outcomes = new Counter("route_search_outcome");

const SCENARIOS = {
  mixed: {
    executor: "ramping-arrival-rate",
    startRate: 1,
    timeUnit: "1s",
    preAllocatedVUs: 20,
    maxVUs: 60,
    stages: [
      { target: 4, duration: "1m" },
      { target: 4, duration: "2m" },
      { target: 8, duration: "1m" },
      { target: 8, duration: "1m" },
    ],
  },
  same_search: { executor: "shared-iterations", vus: 50, iterations: 50, maxDuration: "1m" },
  otp_outage: { executor: "constant-vus", vus: 5, duration: "30s" },
};

const THRESHOLDS = {
  mixed: {
    "http_req_failed{name:search}": ["rate<0.01"],
    "http_req_duration{name:search}": ["p(95)<5000"],
    "http_req_duration{name:places}": ["p(95)<500"],
  },
  // Exactly one search reaches OpenTripPlanner; everyone else shares its answer.
  same_search: { "route_search_cache{cache:MISS}": ["count==1"], checks: ["rate==1"] },
  // Fail fast and say so: 503 within a second, never a 500 or an invented route.
  // A trip already in the cache may still be answered with its real routes.
  otp_outage: { checks: ["rate==1"], "http_req_duration{name:search}": ["p(95)<1000"] },
};

// Always-true thresholds, only so the summary reports these breakdowns.
const REPORTED = Object.fromEntries(
  [
    ...["200", "429", "500", "503", "504"].map((status) => `route_search_outcome{status:${status}}`),
    ...["HIT", "MISS", "SHARED"].map((cache) => `route_search_cache{cache:${cache}}`),
  ].map((name) => [name, ["count>=0"]]),
);

export const options = {
  scenarios: { [SCENARIO]: SCENARIOS[SCENARIO] },
  thresholds: { ...REPORTED, ...THRESHOLDS[SCENARIO] },
  summaryTrendStats: ["med", "p(90)", "p(95)", "p(99)", "max"],
};

function pick(list) {
  return list[Math.floor(Math.random() * list.length)];
}

function searchBody(trip, departAt, stepFree) {
  return JSON.stringify({
    origin: trip.origin,
    destination: trip.destination,
    depart_at: departAt,
    accessibility: { step_free_required: stepFree },
  });
}

// A departure in the next few hours, on a minute boundary, so repeated
// trips sometimes share a cache entry the way real riders would.
function departure(minutesAhead) {
  const at = new Date(Date.now() + minutesAhead * 60_000);
  at.setUTCSeconds(0, 0);
  return at.toISOString();
}

function search(body) {
  const response = http.post(`${BASE}/api/v1/routes/search`, body, { ...HEADERS, tags: { name: "search" } });
  cacheResults.add(1, { cache: response.headers["X-Cache"] || "none" });
  let code = "";
  if (response.status >= 400) {
    try {
      code = response.json("code") || "";
    } catch (_) {
      code = "unparsed";
    }
  }
  outcomes.add(1, { status: String(response.status), code });
  return response;
}

function mixed() {
  if (Math.random() < 0.3) {
    const response = http.get(`${BASE}/api/v1/places?q=${encodeURIComponent(pick(PLACE_QUERIES))}`, {
      tags: { name: "places" },
    });
    check(response, { "places 200": (r) => r.status === 200 });
    return;
  }
  const minutes = 30 * Math.floor(Math.random() * 8);
  const response = search(searchBody(pick(TRIPS), departure(minutes), Math.random() < 0.3));
  check(response, { "search 200": (r) => r.status === 200 });
  const investigation = response.status === 200 ? response.json("investigation") : null;
  if (investigation) {
    sleep(investigation.poll_after_ms / 1000);
    const poll = http.get(`${BASE}/api/v1/routes/investigations/${investigation.id}`, {
      tags: { name: "investigation" },
    });
    check(poll, { "investigation 200": (r) => r.status === 200 });
  }
}

export function setup() {
  // Later than any departure the mixed scenario uses, so this search starts uncached.
  return { departAt: departure(9 * 60 + 17) };
}

export default function (data) {
  if (SCENARIO === "mixed") return mixed();
  if (SCENARIO === "same_search") {
    const response = search(searchBody(TRIPS[0], data.departAt, false));
    check(response, { "search 200": (r) => r.status === 200 });
    return;
  }
  // otp_outage: a different trip and time each time, so nothing is cached.
  const response = search(searchBody(pick(TRIPS), departure(60 + Math.floor(Math.random() * 600)), false));
  check(response, {
    "503 routing_unavailable, or real routes from the cache": (r) =>
      (r.status === 503 && r.json("code") === "routing_unavailable") ||
      (r.status === 200 && r.headers["X-Cache"] === "HIT"),
  });
  sleep(0.5);
}

export function handleSummary(data) {
  const out = __ENV.SUMMARY_DIR || ".";
  return { [`${out}/${SCENARIO}.json`]: JSON.stringify(data, null, 2), stdout: textSummary(data) };
}

function textSummary(data) {
  const lines = [`scenario: ${SCENARIO}`];
  for (const [name, metric] of Object.entries(data.metrics)) {
    if (!/http_req_duration\{name|http_reqs$|http_req_failed|route_search_|checks/.test(name)) continue;
    lines.push(`${name}: ${JSON.stringify(metric.values)}`);
  }
  return lines.join("\n") + "\n";
}
