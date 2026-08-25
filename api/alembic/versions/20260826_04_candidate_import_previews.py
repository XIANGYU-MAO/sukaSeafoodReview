"""Stage bounded candidate CSV previews for atomic import.

Revision ID: 20260826_04
Revises: 20260826_03
Create Date: 2026-08-26
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260826_04"
down_revision: str | None = "20260826_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "candidate_import_previews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=True),
        sa.Column("report_json", sa.JSON(), nullable=False),
        sa.Column("database_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
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
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_candidate_import_previews_actor_id",
        "candidate_import_previews",
        ["actor_id"],
    )
    op.create_index(
        "ix_candidate_import_previews_expires_at",
        "candidate_import_previews",
        ["expires_at"],
    )
    op.create_index(
        "uq_candidate_import_previews_token_digest",
        "candidate_import_previews",
        ["token_digest"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("candidate_import_previews")
