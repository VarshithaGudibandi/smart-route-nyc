"""Prometheus metrics for the API and the ingestion worker.

Served on a separate port (``METRICS_PORT``) that Compose does not publish,
so only Prometheus on the internal network can read them. Labels never
carry locations, query strings or investigation IDs: request paths are
recorded as their route template (``/api/v1/routes/investigations/{id}``),
which keeps cardinality bounded and reveals nothing about a trip.
"""

from wsgiref.simple_server import WSGIServer

from prometheus_client import Counter, Gauge, Histogram, start_http_server

# Request latency buckets, in seconds; route searches can take several seconds.
LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)

HTTP_REQUESTS = Counter(
    "srn_http_requests_total", "HTTP requests handled", ["method", "route", "status"]
)
HTTP_DURATION = Histogram(
    "srn_http_request_duration_seconds",
    "Time to answer an HTTP request",
    ["method", "route"],
    buckets=LATENCY_BUCKETS,
)

ROUTE_SEARCHES = Counter(
    "srn_route_searches_total",
    "Route searches by how the cache answered: hit, miss, or shared with a concurrent search",
    ["cache"],
)

OTP_REQUESTS = Counter(
    "srn_otp_requests_total",
    "Requests to OpenTripPlanner: ok, transient_error, query_error or circuit_open",
    ["outcome"],
)
OTP_DURATION = Histogram(
    "srn_otp_request_duration_seconds",
    "Time OpenTripPlanner took to answer one GraphQL request",
    buckets=LATENCY_BUCKETS,
)

INVESTIGATIONS = Counter(
    "srn_investigations_total",
    "Background transfer investigations: started, deduplicated, shed, completed, "
    "failed, timed_out or cancelled",
    ["event"],
)
INVESTIGATION_DURATION = Histogram(
    "srn_investigation_duration_seconds",
    "Time a transfer investigation took, from start of work to result",
    buckets=LATENCY_BUCKETS,
)
INVESTIGATIONS_RUNNING = Gauge(
    "srn_investigations_pending", "Investigations queued or running in this process"
)
SUGGESTIONS_VERIFIED = Counter(
    "srn_suggestions_verified_total", "Faster transfers verified by OpenTripPlanner"
)

INGEST_RUNS = Counter(
    "srn_ingest_runs_total", "Ingestion job runs by outcome", ["source", "outcome"]
)
INGEST_LAST_SUCCESS = Gauge(
    "srn_ingest_last_success_timestamp_seconds",
    "Unix time of each source's last successful fetch",
    ["source"],
)
INGEST_DURATION = Histogram(
    "srn_ingest_duration_seconds",
    "Time one ingestion job took",
    ["source"],
    buckets=(0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)


def serve_metrics(port: int | None) -> WSGIServer | None:
    """Expose the default registry on ``port`` in a daemon thread, if set."""
    if port is None:
        return None
    server, _ = start_http_server(port)
    return server
