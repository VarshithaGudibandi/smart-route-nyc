"""add service alerts

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-18 15:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "service_alerts",
        sa.Column("operator", sa.String(length=16), nullable=False),
        sa.Column("alert_external_id", sa.String(length=64), nullable=False),
        sa.Column("alert_type", sa.String(length=64), nullable=True),
        sa.Column("header_text", sa.Text(), nullable=False),
        sa.Column("description_text", sa.Text(), nullable=True),
        sa.Column("active_periods", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("informed_entities", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_version", sa.String(length=128), nullable=True),
        sa.PrimaryKeyConstraint("operator", "alert_external_id", name=op.f("pk_service_alerts")),
    )


def downgrade() -> None:
    op.drop_table("service_alerts")
