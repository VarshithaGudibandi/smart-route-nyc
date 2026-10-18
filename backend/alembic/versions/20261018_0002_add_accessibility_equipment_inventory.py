"""add accessibility equipment inventory

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-18 04:57:29.518562
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "accessibility_equipment",
        sa.Column("operator", sa.String(length=16), nullable=False),
        sa.Column("equipment_external_id", sa.String(length=32), nullable=False),
        sa.Column("equipment_type", sa.String(length=8), nullable=False),
        sa.Column("station_name", sa.String(length=128), nullable=False),
        sa.Column("station_complex_id", sa.String(length=16), nullable=True),
        sa.Column("serving", sa.Text(), nullable=True),
        sa.Column("lines_served", sa.String(length=64), nullable=True),
        sa.Column("ada_accessible", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("has_redundant", sa.Boolean(), nullable=False),
        sa.Column("alternative_route", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_version", sa.String(length=128), nullable=True),
        sa.PrimaryKeyConstraint(
            "operator", "equipment_external_id", name=op.f("pk_accessibility_equipment")
        ),
    )
    op.create_table(
        "accessibility_equipment_stops",
        sa.Column("operator", sa.String(length=16), nullable=False),
        sa.Column("equipment_external_id", sa.String(length=32), nullable=False),
        sa.Column("gtfs_stop_id", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(
            ["operator", "equipment_external_id"],
            ["accessibility_equipment.operator", "accessibility_equipment.equipment_external_id"],
            name=op.f("fk_accessibility_equipment_stops_operator_accessibility_equipment"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "operator",
            "equipment_external_id",
            "gtfs_stop_id",
            name=op.f("pk_accessibility_equipment_stops"),
        ),
    )
    op.create_index(
        op.f("ix_accessibility_equipment_stops_operator_gtfs_stop_id"),
        "accessibility_equipment_stops",
        ["operator", "gtfs_stop_id"],
        unique=False,
    )
    op.add_column("equipment_status", sa.Column("reason", sa.String(length=128), nullable=True))
    op.add_column(
        "equipment_status",
        sa.Column("outage_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "equipment_status",
        sa.Column("estimated_return_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "source_freshness", sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("source_freshness", "valid_until")
    op.drop_column("equipment_status", "estimated_return_at")
    op.drop_column("equipment_status", "outage_started_at")
    op.drop_column("equipment_status", "reason")
    op.drop_index(
        op.f("ix_accessibility_equipment_stops_operator_gtfs_stop_id"),
        table_name="accessibility_equipment_stops",
    )
    op.drop_table("accessibility_equipment_stops")
    op.drop_table("accessibility_equipment")
