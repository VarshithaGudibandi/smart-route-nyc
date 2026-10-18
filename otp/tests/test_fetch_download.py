"""Tests for the resumable downloader in fetch_inputs.py, against a local HTTP server."""

from __future__ import annotations

import http.server
import sys
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import fetch_inputs

BODY = bytes(range(256)) * 4096  # 1 MiB
ETAG = '"v1"'


class Server(http.server.ThreadingHTTPServer):
    # Per test: how each request behaves, consumed in order.
    plan: list[str]
    seen_ranges: list[str | None]


class Handler(http.server.BaseHTTPRequestHandler):
    server: Server

    def log_message(self, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        self.server.seen_ranges.append(self.headers.get("Range"))
        behaviour = self.server.plan.pop(0) if self.server.plan else "full"
        if behaviour == "error":
            self.send_response(503)
            self.end_headers()
            return
        start = 0
        requested = self.headers.get("Range")
        if requested and behaviour != "ignore_range" and self.headers.get("If-Range") == ETAG:
            start = int(requested.removeprefix("bytes=").rstrip("-"))
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(BODY) - 1}/{len(BODY)}")
        else:
            self.send_response(200)
        self.send_header("ETag", ETAG)
        self.send_header("Content-Length", str(len(BODY) - start))
        self.end_headers()
        if behaviour == "cut":
            # Send a third of what remains, then drop the connection.
            self.wfile.write(BODY[start : start + (len(BODY) - start) // 3])
            self.wfile.flush()
            self.connection.close()
            return
        self.wfile.write(BODY[start:])


class DownloadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = Server(("127.0.0.1", 0), Handler)
        self.server.plan = []
        self.server.seen_ranges = []
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/extract.pbf"
        self.dir = Path(tempfile.mkdtemp())
        self.dest = self.dir / "extract.pbf"
        patches = [
            mock.patch.object(fetch_inputs.time, "sleep"),
            mock.patch.object(fetch_inputs, "_resume_counts", {}),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def test_resumes_after_dropped_connections(self) -> None:
        self.server.plan = ["cut", "cut", "cut", "cut", "cut", "full"]
        fetch_inputs.download(self.url, self.dest)
        self.assertEqual(self.dest.read_bytes(), BODY)
        self.assertIsNone(self.server.seen_ranges[0])
        self.assertTrue(all(r and r.startswith("bytes=") for r in self.server.seen_ranges[1:]))
        self.assertFalse(self.dest.with_suffix(".pbf.part").exists())

    def test_progress_does_not_use_up_the_retry_budget(self) -> None:
        # More dropped connections than MAX_ATTEMPTS, each making progress.
        self.server.plan = ["cut"] * (fetch_inputs.MAX_ATTEMPTS + 2) + ["full"]
        fetch_inputs.download(self.url, self.dest)
        self.assertEqual(self.dest.read_bytes(), BODY)

    def test_server_ignoring_the_range_restarts_cleanly(self) -> None:
        self.server.plan = ["cut", "ignore_range"]
        fetch_inputs.download(self.url, self.dest)
        self.assertEqual(self.dest.read_bytes(), BODY)

    def test_gives_up_after_failures_without_progress(self) -> None:
        self.server.plan = ["error"] * fetch_inputs.MAX_ATTEMPTS
        with self.assertRaises(urllib.error.HTTPError):
            fetch_inputs.download(self.url, self.dest)
        self.assertFalse(self.dest.exists())


if __name__ == "__main__":
    unittest.main()
