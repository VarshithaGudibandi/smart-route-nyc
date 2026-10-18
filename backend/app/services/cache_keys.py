"""Deterministic cache keys for route searches.

Two requests share a key only if they would produce the same OTP query:
same rounded coordinates, same departure bucket, same accessibility and
optimisation settings, and the same routing graph. Keys are HMACs, so Redis
never contains readable locations.
"""

import hashlib
import hmac
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.schemas.routes import RouteSearchRequest

NEW_YORK = ZoneInfo("America/New_York")
KEY_SCHEMA_VERSION = 1
# 4 decimal places is about 11 m: close enough that walking legs barely change.
COORDINATE_DECIMALS = 4
DEPARTURE_BUCKET = timedelta(minutes=5)


def departure_bucket(moment: datetime) -> datetime:
    """Floor ``moment`` to its 5-minute bucket in New York local time."""
    local = moment.astimezone(NEW_YORK)
    bucket_minutes = DEPARTURE_BUCKET.seconds // 60
    return local.replace(
        minute=local.minute - local.minute % bucket_minutes, second=0, microsecond=0
    )


def route_search_key(
    request: RouteSearchRequest, bucket: datetime, graph_version: str, secret: bytes
) -> str:
    accessibility = request.accessibility
    optimization = request.optimization
    payload = {
        "v": KEY_SCHEMA_VERSION,
        "graph": graph_version,
        "origin": [
            round(request.origin.lat, COORDINATE_DECIMALS),
            round(request.origin.lon, COORDINATE_DECIMALS),
        ],
        "destination": [
            round(request.destination.lat, COORDINATE_DECIMALS),
            round(request.destination.lon, COORDINATE_DECIMALS),
        ],
        "bucket": bucket.isoformat(),
        # Every field that changes the OTP query or the post-filtering must be here,
        # or results computed for one rider's needs could be served to another's.
        "step_free": accessibility.step_free_required,
        "wheelchair": accessibility.wheelchair_required,
        "max_walk": accessibility.max_walk_meters,
        "primary": optimization.primary,
        "fewer_transfers": optimization.prefer_fewer_transfers,
        "less_walking": optimization.prefer_less_walking,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hmac.new(secret, canonical.encode(), hashlib.sha256).hexdigest()[:32]
    return f"srn:v{KEY_SCHEMA_VERSION}:route:{digest}"
