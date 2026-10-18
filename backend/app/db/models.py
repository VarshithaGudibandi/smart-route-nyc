"""Database schema.

The application stores no accounts and no travel history. Tables hold
anonymous feedback, provenance of ingested transit data, accessibility
equipment status and current service alerts.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    MetaData,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Deterministic constraint names keep Alembic migrations stable.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class RouteFeedback(Base):
    """Anonymous 1-5 star rating of a route result.

    ``route_result_ref_hash`` is a hash of the short-lived reference issued
    with a search result; the unique constraint makes resubmission idempotent.
    """

    __tablename__ = "route_feedback"
    __table_args__ = (
        CheckConstraint("stars BETWEEN 1 AND 5", name="stars_range"),
        CheckConstraint("char_length(comment) <= 1000", name="comment_length"),
        Index(None, "created_at"),
    )

    feedback_id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    route_result_ref_hash: Mapped[str] = mapped_column(String(64), unique=True)
    stars: Mapped[int] = mapped_column(SmallInteger)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FeedSyncRun(Base):
    """One execution of an ingestion job for a single feed."""

    __tablename__ = "feed_sync_runs"
    __table_args__ = (Index(None, "provider", "feed_kind", "started_at"),)

    run_id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(32))
    feed_kind: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))
    checksum: Mapped[str | None] = mapped_column(String(128))
    error_code: Mapped[str | None] = mapped_column(String(64))


class SourceFreshness(Base):
    """Latest known state of each upstream data source."""

    __tablename__ = "source_freshness"

    source_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    source_version: Mapped[str | None] = mapped_column(String(128))
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_status: Mapped[str] = mapped_column(String(16))
    # End of the published service period, for static timetables.
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_successful_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("feed_sync_runs.run_id", ondelete="SET NULL")
    )


class AccessibilityEquipment(Base):
    """Inventory of elevators and escalators, from the operator's equipment feed."""

    __tablename__ = "accessibility_equipment"

    operator: Mapped[str] = mapped_column(String(16), primary_key=True)
    equipment_external_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    equipment_type: Mapped[str] = mapped_column(String(8))
    station_name: Mapped[str] = mapped_column(String(128))
    station_complex_id: Mapped[str | None] = mapped_column(String(16))
    serving: Mapped[str | None] = mapped_column(Text)
    lines_served: Mapped[str | None] = mapped_column(String(64))
    ada_accessible: Mapped[bool] = mapped_column(Boolean)
    is_active: Mapped[bool] = mapped_column(Boolean)
    has_redundant: Mapped[bool] = mapped_column(Boolean)
    alternative_route: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_version: Mapped[str | None] = mapped_column(String(128))


class AccessibilityEquipmentStop(Base):
    """GTFS stops served by a piece of equipment.

    A station complex has several GTFS parent stops (14 St serves ``A31`` and
    ``L01``), so the mapping is one-to-many and indexed by stop for routing.
    """

    __tablename__ = "accessibility_equipment_stops"
    __table_args__ = (
        ForeignKeyConstraint(
            ["operator", "equipment_external_id"],
            ["accessibility_equipment.operator", "accessibility_equipment.equipment_external_id"],
            ondelete="CASCADE",
        ),
        Index(None, "operator", "gtfs_stop_id"),
    )

    operator: Mapped[str] = mapped_column(String(16), primary_key=True)
    equipment_external_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    gtfs_stop_id: Mapped[str] = mapped_column(String(16), primary_key=True)


class EquipmentStatus(Base):
    """Current state of one elevator or escalator, upserted by ingestion."""

    __tablename__ = "equipment_status"
    __table_args__ = (Index(None, "operator", "station_external_id"),)

    operator: Mapped[str] = mapped_column(String(16), primary_key=True)
    equipment_external_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    # GTFS stop ID(s) the equipment serves, e.g. "132/D19/L02" for a complex.
    station_external_id: Mapped[str | None] = mapped_column(String(64))
    equipment_type: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(String(128))
    outage_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    estimated_return_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_version: Mapped[str | None] = mapped_column(String(128))


class AccessibilityEvent(Base):
    """Append-only history of equipment status changes."""

    __tablename__ = "accessibility_events"
    __table_args__ = (
        # Re-ingesting the same feed must not create duplicate rows.
        UniqueConstraint("operator", "equipment_external_id", "observed_at", "status"),
        Index(None, "station_external_id", "fetched_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    operator: Mapped[str] = mapped_column(String(16))
    station_external_id: Mapped[str | None] = mapped_column(String(64))
    equipment_external_id: Mapped[str] = mapped_column(String(32))
    event_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    raw_source_ref: Mapped[str | None] = mapped_column(Text)


class ServiceAlert(Base):
    """Current service alerts (delays, suspensions, planned work).

    The operator publishes the full set on every fetch, so the table mirrors
    the latest feed: alerts that disappear are deleted. Times are the feed's.
    """

    __tablename__ = "service_alerts"

    operator: Mapped[str] = mapped_column(String(16), primary_key=True)
    alert_external_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    alert_type: Mapped[str | None] = mapped_column(String(64))
    header_text: Mapped[str] = mapped_column(Text)
    description_text: Mapped[str | None] = mapped_column(Text)
    # [[start, end], ...] in epoch seconds; end is null for open-ended periods.
    active_periods: Mapped[list[list[int | None]]] = mapped_column(JSONB)
    # [{"route_id": "C", "stop_id": "A24", "direction_id": 1}, ...], GTFS IDs
    # without the feed prefix, exactly as the operator publishes them.
    informed_entities: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_version: Mapped[str | None] = mapped_column(String(128))
