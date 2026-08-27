"""Advance candidates to the synchronization-generation epoch.

Revision ID: 20260827_07
Revises: 20260827_06
Create Date: 2026-08-27
"""

from collections.abc import Sequence

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260827_07"
down_revision: str | None = "20260827_06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAX_DB_INTEGER = 2_147_483_647


def _candidate_epoch_update_sql(dialect: str) -> sa.TextClause:
    maximum = "GREATEST" if dialect == "postgresql" else "MAX"
    return sa.text(
        "UPDATE candidates AS c "
        "SET version = 1 + "
        f"{maximum}("
        "c.version, "
        "COALESCE((SELECT MAX(r.version) FROM reviews r WHERE r.candidate_id = c.id), 0), "
        "COALESCE((SELECT MAX(rr.review_version) FROM review_revisions rr WHERE rr.candidate_id = c.id), 0), "
        "COALESCE((SELECT MAX(ei.review_version) FROM export_items ei WHERE ei.candidate_id = c.id), 0)"
        ")"
    )


def _expire_pending_batches() -> None:
    op.execute(
        sa.text(
            "UPDATE export_batches "
            "SET status = 'expired', expired_at = COALESCE(expired_at, CURRENT_TIMESTAMP) "
            "WHERE status = 'pending'"
        )
    )


def upgrade() -> None:
    dialect = context.get_context().dialect.name
    if context.is_offline_mode():
        op.execute(_candidate_epoch_update_sql(dialect))
        _expire_pending_batches()
        return

    bind = op.get_bind()
    if dialect == "postgresql":
        op.execute(
            sa.text("LOCK TABLE export_batches IN SHARE UPDATE EXCLUSIVE MODE")
        )
    maximum = bind.execute(
        sa.text(
            "SELECT MAX(v) FROM ("
            "SELECT version AS v FROM candidates "
            "UNION ALL SELECT version FROM reviews "
            "UNION ALL SELECT review_version FROM review_revisions "
            "UNION ALL SELECT review_version FROM export_items"
            ") AS historical"
        )
    ).scalar()
    if maximum is not None and int(maximum) >= MAX_DB_INTEGER:
        raise RuntimeError("candidate synchronization generation exhausted")
    op.execute(_candidate_epoch_update_sql(dialect))
    _expire_pending_batches()


def downgrade() -> None:
    # Candidate versions remain monotonic when returning to the previous schema.
    pass
