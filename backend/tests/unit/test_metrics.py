import urllib.request

from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from app.core.metrics import serve_metrics
from app.core.middleware import RequestContextMiddleware


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/things/{thing_id}")
    async def thing(thing_id: str) -> dict[str, str]:
        return {"id": thing_id}

    app.add_middleware(RequestContextMiddleware)
    return app


def _requests(route: str, status: str) -> float:
    value = REGISTRY.get_sample_value(
        "srn_http_requests_total", {"method": "GET", "route": route, "status": status}
    )
    return value or 0.0


def test_requests_are_labelled_by_route_template_not_path() -> None:
    before = _requests("/things/{thing_id}", "200")
    client = TestClient(_app())

    client.get("/things/abc123")
    client.get("/things/40.7128,-74.0060")

    assert _requests("/things/{thing_id}", "200") == before + 2
    labels = [
        sample.labels.get("route", "") for metric in REGISTRY.collect() for sample in metric.samples
    ]
    assert not any("abc123" in label or "40.7128" in label for label in labels)


def test_unknown_paths_share_one_label() -> None:
    before = _requests("unmatched", "404")

    TestClient(_app()).get("/no/such/place")

    assert _requests("unmatched", "404") == before + 1


def test_metrics_server_is_off_without_a_port() -> None:
    assert serve_metrics(None) is None


def test_metrics_server_serves_the_registry() -> None:
    server = serve_metrics(0)
    assert server is not None
    try:
        port = server.server_address[1]
        body = urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=2).read()
        assert b"srn_http_requests_total" in body
    finally:
        server.shutdown()
