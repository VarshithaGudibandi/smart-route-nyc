"""add suggestion outcomes

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-18 15:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "suggestion_outcomes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("graph_version", sa.String(length=64), nullable=False),
        sa.Column("generator", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("first_routes", sa.String(length=128), nullable=False),
        sa.Column("transfer_station", sa.String(length=64), nullable=False),
        sa.Column("second_routes", sa.String(length=128), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("saves_seconds", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_suggestion_outcomes")),
    )
    op.create_index(
        op.f("ix_suggestion_outcomes_created_at"), "suggestion_outcomes", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_suggestion_outcomes_created_at"), table_name="suggestion_outcomes")
    op.drop_table("suggestion_outcomes")
