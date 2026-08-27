"""Allow multiple pending chunks for one export scope.

Revision ID: 20260827_06
Revises: 20260826_05
Create Date: 2026-08-27
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260827_06"
down_revision: str | None = "20260826_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("uq_export_batches_pending_scope", table_name="export_batches")


def downgrade() -> None:
    op.execute(
        sa.text(
            "WITH ranked AS ("
            "SELECT id, ROW_NUMBER() OVER ("
            "PARTITION BY scope_key ORDER BY created_at, id"
            ") AS pending_rank "
            "FROM export_batches WHERE status = 'pending'"
            ") "
            "UPDATE export_batches "
            "SET status = 'expired', expired_at = COALESCE(expired_at, CURRENT_TIMESTAMP) "
            "WHERE id IN (SELECT id FROM ranked WHERE pending_rank > 1)"
        )
    )
    op.create_index(
        "uq_export_batches_pending_scope",
        "export_batches",
        ["scope_key"],
        unique=True,
        sqlite_where=sa.text("status = 'pending'"),
        postgresql_where=sa.text("status = 'pending'"),
    )
