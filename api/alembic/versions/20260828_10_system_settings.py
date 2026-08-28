"""Add singleton settings for login and team progress visibility.

Revision ID: 20260828_10
Revises: 20260828_09
Create Date: 2026-08-28
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260828_10"
down_revision: str | None = "20260828_09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    table = op.create_table(
        "system_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "login_name_mode",
            sa.String(length=16),
            server_default="choices",
            nullable=False,
        ),
        sa.Column(
            "reviewer_team_progress_visible",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name="ck_system_settings_singleton"),
        sa.CheckConstraint(
            "login_name_mode IN ('choices', 'manual')",
            name="ck_system_settings_login_name_mode",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.bulk_insert(
        table,
        [
            {
                "id": 1,
                "login_name_mode": "choices",
                "reviewer_team_progress_visible": True,
            }
        ],
    )


def downgrade() -> None:
    op.drop_table("system_settings")
