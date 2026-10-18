"""create core tables

Revision ID: 0001
Revises:
Create Date: 2026-10-18 04:16:30.678452
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "accessibility_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("operator", sa.String(length=16), nullable=False),
        sa.Column("station_external_id", sa.String(length=64), nullable=True),
        sa.Column("equipment_external_id", sa.String(length=32), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_source_ref", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_accessibility_events")),
        sa.UniqueConstraint(
            "operator",
            "equipment_external_id",
            "observed_at",
            "status",
            name=op.f("uq_accessibility_events_operator_equipment_external_id_observed_at_status"),
        ),
    )
    op.create_index(
        op.f("ix_accessibility_events_station_external_id_fetched_at"),
        "accessibility_events",
        ["station_external_id", "fetched_at"],
        unique=False,
    )
    op.create_table(
        "equipment_status",
        sa.Column("operator", sa.String(length=16), nullable=False),
        sa.Column("equipment_external_id", sa.String(length=32), nullable=False),
        sa.Column("station_external_id", sa.String(length=64), nullable=True),
        sa.Column("equipment_type", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_version", sa.String(length=128), nullable=True),
        sa.PrimaryKeyConstraint(
            "operator", "equipment_external_id", name=op.f("pk_equipment_status")
        ),
    )
    op.create_index(
        op.f("ix_equipment_status_operator_station_external_id"),
        "equipment_status",
        ["operator", "station_external_id"],
        unique=False,
    )
    op.create_table(
        "feed_sync_runs",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("feed_kind", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("checksum", sa.String(length=128), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("run_id", name=op.f("pk_feed_sync_runs")),
    )
    op.create_index(
        op.f("ix_feed_sync_runs_provider_feed_kind_started_at"),
        "feed_sync_runs",
        ["provider", "feed_kind", "started_at"],
        unique=False,
    )
    op.create_table(
        "route_feedback",
        sa.Column("feedback_id", sa.Uuid(), nullable=False),
        sa.Column("route_result_ref_hash", sa.String(length=64), nullable=False),
        sa.Column("stars", sa.SmallInteger(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(comment) <= 1000", name=op.f("ck_route_feedback_comment_length")
        ),
        sa.CheckConstraint("stars BETWEEN 1 AND 5", name=op.f("ck_route_feedback_stars_range")),
        sa.PrimaryKeyConstraint("feedback_id", name=op.f("pk_route_feedback")),
        sa.UniqueConstraint(
            "route_result_ref_hash", name=op.f("uq_route_feedback_route_result_ref_hash")
        ),
    )
    op.create_index(
        op.f("ix_route_feedback_created_at"), "route_feedback", ["created_at"], unique=False
    )
    op.create_table(
        "source_freshness",
        sa.Column("source_key", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("source_version", sa.String(length=128), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_status", sa.String(length=16), nullable=False),
        sa.Column("last_successful_run_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["last_successful_run_id"],
            ["feed_sync_runs.run_id"],
            name=op.f("fk_source_freshness_last_successful_run_id_feed_sync_runs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("source_key", name=op.f("pk_source_freshness")),
    )


def downgrade() -> None:
    op.drop_table("source_freshness")
    op.drop_index(op.f("ix_route_feedback_created_at"), table_name="route_feedback")
    op.drop_table("route_feedback")
    op.drop_index(
        op.f("ix_feed_sync_runs_provider_feed_kind_started_at"), table_name="feed_sync_runs"
    )
    op.drop_table("feed_sync_runs")
    op.drop_index(
        op.f("ix_equipment_status_operator_station_external_id"), table_name="equipment_status"
    )
    op.drop_table("equipment_status")
    op.drop_index(
        op.f("ix_accessibility_events_station_external_id_fetched_at"),
        table_name="accessibility_events",
    )
    op.drop_table("accessibility_events")
