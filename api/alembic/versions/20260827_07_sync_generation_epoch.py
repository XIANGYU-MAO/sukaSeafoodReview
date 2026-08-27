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


def _historical_values_sql() -> str:
    return (
        "SELECT version AS v FROM candidates "
        "UNION ALL SELECT version FROM reviews "
        "UNION ALL SELECT review_version FROM review_revisions "
        "UNION ALL SELECT review_version FROM export_items"
    )


def _offline_exhaustion_guard(dialect: str) -> tuple[sa.TextClause, ...]:
    historical = _historical_values_sql()
    if dialect == "postgresql":
        return (
            sa.text(
                "DO $$ BEGIN "
                "IF EXISTS (SELECT 1 FROM ("
                f"{historical}"
                f") AS historical WHERE v >= {MAX_DB_INTEGER}) THEN "
                "RAISE EXCEPTION 'candidate synchronization generation exhausted'; "
                "END IF; END $$"
            ),
        )
    return (
        sa.text(
            "CREATE TEMPORARY TABLE _sync_generation_guard ("
            "value INTEGER NOT NULL CHECK (value = 0))"
        ),
        sa.text(
            "INSERT INTO _sync_generation_guard (value) "
            "SELECT CASE WHEN EXISTS (SELECT 1 FROM ("
            f"{historical}"
            f") AS historical WHERE v >= {MAX_DB_INTEGER}) THEN 1 ELSE 0 END"
        ),
        sa.text("DROP TABLE _sync_generation_guard"),
    )


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
    if dialect == "postgresql":
        op.execute(
            sa.text("LOCK TABLE export_batches IN SHARE ROW EXCLUSIVE MODE")
        )
    if context.is_offline_mode():
        for guard in _offline_exhaustion_guard(dialect):
            op.execute(guard)
        op.execute(_candidate_epoch_update_sql(dialect))
        _expire_pending_batches()
        return

    bind = op.get_bind()
    maximum = bind.execute(
        sa.text(
            "SELECT MAX(v) FROM ("
            f"{_historical_values_sql()}"
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
