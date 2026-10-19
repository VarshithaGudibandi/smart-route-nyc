"""add later transfers to suggestion outcomes

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-19 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "suggestion_outcomes", sa.Column("later_transfers", sa.String(length=256), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("suggestion_outcomes", "later_transfers")
