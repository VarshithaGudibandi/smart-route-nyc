"""Freshness probes for GTFS-Realtime feeds.

The routing engine does not consume realtime data yet; these probes record
how current each feed is (from the feed's own header timestamp), so the API
can state whether realtime information is available and how old it is.
"""

import hashlib
from datetime import UTC, datetime

import httpx
from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2

from app.core.freshness import SourceKind
from app.ingestion.http import FetchError, fetch_bytes
from app.ingestion.runs import RunContext, RunOutcome


class GtfsRealtimeProbe:
    def __init__(self, http: httpx.AsyncClient, provider: str, source_key: str, url: str) -> None:
        self._http = http
        self.provider = provider
        self.source_key = source_key
        self.kind = SourceKind.REALTIME_SERVICE
        self._url = url

    async def run(self, ctx: RunContext) -> RunOutcome:
        raw = await fetch_bytes(self._http, self._url)
        feed = gtfs_realtime_pb2.FeedMessage()
        try:
            feed.ParseFromString(raw)
        except DecodeError as exc:
            raise FetchError(
                f"invalid GTFS-Realtime payload from {self._url}", "decode_error"
            ) from exc
        if not feed.header.timestamp:
            raise FetchError("GTFS-Realtime header has no timestamp", "missing_timestamp")
        return RunOutcome(
            checksum=hashlib.sha256(raw).hexdigest(),
            observed_at=datetime.fromtimestamp(feed.header.timestamp, UTC),
            stats={"entities": len(feed.entity)},
        )
