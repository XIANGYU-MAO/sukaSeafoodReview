"""Allow export batches to be hidden without deleting sync history.

Revision ID: 20260828_09
Revises: 20260827_08
Create Date: 2026-08-28
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260828_09"
down_revision: str | None = "20260827_08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "export_batches",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        op.f("ix_export_batches_archived_at"),
        "export_batches",
        ["archived_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_export_batches_archived_at"),
        table_name="export_batches",
    )
    op.drop_column("export_batches", "archived_at")
