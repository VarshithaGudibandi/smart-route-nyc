"""MTA subway service alerts.

Read from the JSON form of ``camsys/subway-alerts``. The JSON is used rather
than the protobuf because it carries MTA's Mercury extension (the alert type,
such as "Delays" or "Planned - Part Suspended") without needing MTA's
extension ``.proto``.

The feed is a full dataset, so each run replaces the stored set. Its header
timestamp changes every fetch even when nothing else does, so the change
check hashes the alerts only. A feed older than what is stored is ignored,
so a lagging upstream cache cannot roll the table back.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cache.redis import ResilientRedis
from app.core.freshness import SourceKind
from app.db.models import ServiceAlert
from app.ingestion.http import FetchError, fetch_bytes
from app.ingestion.runs import RunContext, RunOutcome

logger = structlog.get_logger(__name__)

OPERATOR = "MTA"
ALERTS_VERSION_KEY = "srn:v1:ver:alerts"
MERCURY_ALERT = "transit_realtime.mercury_alert"


@dataclass(frozen=True)
class Alert:
    alert_id: str
    alert_type: str | None
    header: str
    description: str | None
    active_periods: tuple[tuple[int, int | None], ...]
    informed_entities: tuple[dict[str, Any], ...]
    updated_at: datetime | None


def parse_alerts(feed: dict[str, Any]) -> tuple[datetime, list[Alert]]:
    try:
        timestamp = int(feed["header"]["timestamp"])
        entities = feed["entity"]
    except (KeyError, TypeError, ValueError) as exc:
        raise FetchError("alert feed has no header timestamp or entity list", "malformed") from exc
    alerts = []
    for entity in entities:
        alert = entity.get("alert")
        if not alert:
            continue
        header = _english(alert.get("header_text"))
        if header is None:
            continue
        mercury = alert.get(MERCURY_ALERT) or {}
        alerts.append(
            Alert(
                alert_id=entity["id"],
                alert_type=mercury.get("alert_type"),
                header=header,
                description=_english(alert.get("description_text")),
                active_periods=tuple(
                    (int(p.get("start", 0)), int(p["end"]) if p.get("end") else None)
                    for p in alert.get("active_period", [])
                ),
                informed_entities=tuple(
                    {
                        key: item[key]
                        for key in ("route_id", "stop_id", "direction_id")
                        if key in item
                    }
                    for item in alert.get("informed_entity", [])
                ),
                updated_at=(
                    datetime.fromtimestamp(int(mercury["updated_at"]), UTC)
                    if mercury.get("updated_at")
                    else None
                ),
            )
        )
    return datetime.fromtimestamp(timestamp, UTC), alerts


def _english(text: dict[str, Any] | None) -> str | None:
    """The plain-text English translation (the feed also has an HTML one)."""
    for translation in (text or {}).get("translation", []):
        if translation.get("language") == "en" and translation.get("text"):
            return str(translation["text"])
    return None


def _checksum(alerts: list[Alert]) -> str:
    canonical = json.dumps(
        [a.__dict__ | {"updated_at": str(a.updated_at)} for a in alerts],
        sort_keys=True,
        default=list,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class MtaAlertsSource:
    provider = "mta"
    source_key = "mta_subway_alerts"
    kind = SourceKind.SERVICE_ALERTS

    def __init__(
        self,
        http: httpx.AsyncClient,
        sessions: async_sessionmaker[AsyncSession],
        redis: ResilientRedis,
        base_url: str,
    ) -> None:
        self._http = http
        self._sessions = sessions
        self._redis = redis
        self._url = f"{base_url}/camsys%2Fsubway-alerts.json"

    async def run(self, ctx: RunContext) -> RunOutcome:
        raw = await fetch_bytes(self._http, self._url)
        try:
            feed = json.loads(raw)
        except ValueError as exc:
            raise FetchError("alert feed is not JSON", "decode_error") from exc
        observed_at, alerts = parse_alerts(feed)
        checksum = _checksum(alerts)
        if checksum == ctx.previous_checksum:
            return RunOutcome(checksum=checksum, observed_at=observed_at, unchanged=True)

        now = datetime.now(UTC)
        async with self._sessions.begin() as session:
            stored = await session.scalar(
                select(func.max(ServiceAlert.observed_at)).where(ServiceAlert.operator == OPERATOR)
            )
            if stored is not None and observed_at < stored:
                logger.warning("alert_feed_older_than_stored", feed=observed_at, stored=stored)
                # Freshness stays at what is stored, not at this fetch.
                return RunOutcome(
                    checksum=ctx.previous_checksum, observed_at=stored, unchanged=True
                )
            await self._replace(session, alerts, observed_at, now, checksum[:16])

        await self._redis.increment(ALERTS_VERSION_KEY)
        return RunOutcome(
            checksum=checksum,
            observed_at=observed_at,
            stats={
                "alerts": len(alerts),
                "active": sum(_active(a, observed_at) for a in alerts),
            },
        )

    async def _replace(
        self,
        session: AsyncSession,
        alerts: list[Alert],
        observed_at: datetime,
        now: datetime,
        version: str,
    ) -> None:
        await session.execute(delete(ServiceAlert).where(ServiceAlert.operator == OPERATOR))
        if not alerts:
            return
        await session.execute(
            insert(ServiceAlert)
            .values(
                [
                    {
                        "operator": OPERATOR,
                        "alert_external_id": a.alert_id,
                        "alert_type": a.alert_type,
                        "header_text": a.header,
                        "description_text": a.description,
                        "active_periods": [list(p) for p in a.active_periods],
                        "informed_entities": list(a.informed_entities),
                        "source_updated_at": a.updated_at,
                        "observed_at": observed_at,
                        "fetched_at": now,
                        "source_version": version,
                    }
                    for a in alerts
                ]
            )
            # The feed has repeated an ID before; keep the first, do not fail the run.
            .on_conflict_do_nothing()
        )


def _active(alert: Alert, at: datetime) -> bool:
    moment = at.timestamp()
    return any(
        start <= moment and (end is None or moment < end) for start, end in alert.active_periods
    )
