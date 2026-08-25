"""Ensure each reviewer holds at most one current candidate.

Revision ID: 20260826_03
Revises: 20260826_02
Create Date: 2026-08-26
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260826_03"
down_revision: str | None = "20260826_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_candidates_current_reviewer_nonnull",
        "candidates",
        ["current_reviewer_id"],
        unique=True,
        sqlite_where=sa.text("current_reviewer_id IS NOT NULL"),
        postgresql_where=sa.text("current_reviewer_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_candidates_current_reviewer_nonnull", table_name="candidates"
    )
