"""Create the collaborative review schema.

Revision ID: 20260826_01
Revises:
Create Date: 2026-08-26
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260826_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def timestamps() -> tuple[sa.Column, sa.Column]:
    return (
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
    )


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column(
            "active", sa.Boolean(), server_default=sa.true(), nullable=False
        ),
        sa.Column(
            "failed_login_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "species",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name_zh", sa.String(length=255), nullable=False),
        sa.Column("name_en", sa.String(length=255), nullable=False),
        sa.Column("scientific_name", sa.String(length=255), nullable=False),
        sa.Column(
            "active", sa.Boolean(), server_default=sa.true(), nullable=False
        ),
        sa.Column(
            "sort_order",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])

    op.create_table(
        "candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("species_id", sa.Uuid(), nullable=False),
        sa.Column("source_dataset", sa.String(length=128), nullable=False),
        sa.Column("source_record_id", sa.String(length=255), nullable=False),
        sa.Column("preview_url", sa.String(length=2048), nullable=False),
        sa.Column("original_url", sa.String(length=2048), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
        sa.Column("creator", sa.String(length=512), nullable=True),
        sa.Column("license", sa.String(length=255), nullable=False),
        sa.Column("license_url", sa.String(length=2048), nullable=True),
        sa.Column("attribution", sa.String(length=1024), nullable=False),
        sa.Column("location", sa.String(length=512), nullable=True),
        sa.Column("observed_on", sa.Date(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("current_reviewer_id", sa.Uuid(), nullable=True),
        sa.Column("current_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "active", sa.Boolean(), server_default=sa.true(), nullable=False
        ),
        sa.Column(
            "version", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["current_reviewer_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["species_id"], ["species.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_dataset",
            "source_record_id",
            name="uq_candidates_source_record",
        ),
    )
    op.create_index(
        "ix_candidates_current_reviewer_id", "candidates", ["current_reviewer_id"]
    )
    op.create_index("ix_candidates_species_id", "candidates", ["species_id"])

    decision = sa.Enum(
        "APPROVED", "REJECTED", "UNSURE", name="decision", native_enum=False
    )
    op.create_table(
        "reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(), nullable=False),
        sa.Column("decision", decision, nullable=False),
        sa.Column("rejection_reason", sa.String(length=128), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("whole_fish", sa.String(length=32), nullable=True),
        sa.Column("exact_species_verified", sa.String(length=32), nullable=True),
        sa.Column(
            "is_current", sa.Boolean(), server_default=sa.true(), nullable=False
        ),
        sa.Column(
            "version", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["candidate_id"], ["candidates.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["reviewer_id"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "decision IN ('APPROVED', 'REJECTED', 'UNSURE')",
            name="ck_reviews_decision",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reviews_candidate_id", "reviews", ["candidate_id"])
    op.create_index("ix_reviews_reviewer_id", "reviews", ["reviewer_id"])
    op.create_index(
        "uq_reviews_current_candidate",
        "reviews",
        ["candidate_id"],
        unique=True,
        sqlite_where=sa.text("is_current = 1"),
        postgresql_where=sa.text("is_current IS TRUE"),
    )

    op.create_table(
        "review_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("review_id", sa.Uuid(), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("decision", decision, nullable=False),
        sa.Column("rejection_reason", sa.String(length=128), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("whole_fish", sa.String(length=32), nullable=True),
        sa.Column("exact_species_verified", sa.String(length=32), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("review_version", sa.Integer(), nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["candidate_id"], ["candidates.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["review_id"], ["reviews.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewer_id"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "decision IN ('APPROVED', 'REJECTED', 'UNSURE')",
            name="ck_review_revisions_decision",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_review_revisions_candidate_id", "review_revisions", ["candidate_id"]
    )
    op.create_index(
        "ix_review_revisions_review_id", "review_revisions", ["review_id"]
    )
    op.create_index(
        "ix_review_revisions_reviewer_id", "review_revisions", ["reviewer_id"]
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("object_type", sa.String(length=128), nullable=False),
        sa.Column("object_id", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("before_json", sa.JSON(), nullable=True),
        sa.Column("after_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_actor_id", "audit_events", ["actor_id"])

    op.create_table(
        "idempotency_commands",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("command_key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_idempotency_commands_user_id", "idempotency_commands", ["user_id"]
    )
    op.create_index(
        "uq_idempotency_commands_user_key",
        "idempotency_commands",
        ["user_id", "command_key"],
        unique=True,
    )

    op.create_table(
        "export_batches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column("species_id", sa.Uuid(), nullable=True),
        sa.Column("receipt_token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["species_id"], ["species.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("receipt_token_hash"),
    )
    op.create_index(
        "ix_export_batches_created_by_id", "export_batches", ["created_by_id"]
    )
    op.create_index(
        "ix_export_batches_species_id", "export_batches", ["species_id"]
    )

    export_action = sa.Enum(
        "ADD", "REMOVE", "MOVE", name="exportaction", native_enum=False
    )
    op.create_table(
        "export_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("review_id", sa.Uuid(), nullable=False),
        sa.Column("review_version", sa.Integer(), nullable=False),
        sa.Column("action", export_action, nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("target_relative_path", sa.String(length=1024), nullable=False),
        sa.Column("previous_relative_path", sa.String(length=1024), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("succeeded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["export_batches.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"], ["candidates.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["review_id"], ["reviews.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "action IN ('ADD', 'REMOVE', 'MOVE')",
            name="ck_export_items_action",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_export_items_batch_id", "export_items", ["batch_id"])
    op.create_index(
        "ix_export_items_candidate_id", "export_items", ["candidate_id"]
    )
    op.create_index("ix_export_items_review_id", "export_items", ["review_id"])
    op.create_index(
        "uq_export_items_batch_candidate_action",
        "export_items",
        ["batch_id", "candidate_id", "action"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("export_items")
    op.drop_table("export_batches")
    op.drop_table("idempotency_commands")
    op.drop_table("audit_events")
    op.drop_table("review_revisions")
    op.drop_table("reviews")
    op.drop_table("candidates")
    op.drop_table("sessions")
    op.drop_table("species")
    op.drop_table("users")
