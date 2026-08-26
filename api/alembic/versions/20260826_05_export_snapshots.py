"""Add immutable export snapshots and synchronized-state constraints.

Revision ID: 20260826_05
Revises: 20260826_04
Create Date: 2026-08-26
"""

from collections.abc import Sequence
import re
from uuid import UUID

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260826_05"
down_revision: str | None = "20260826_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SAFE_SPECIES_CODE_PATTERN = re.compile(r"[A-Z][A-Z0-9_-]{0,31}\Z", re.ASCII)
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def _is_safe_species_code(value: str) -> bool:
    return (
        SAFE_SPECIES_CODE_PATTERN.fullmatch(value) is not None
        and value not in WINDOWS_RESERVED_NAMES
    )


def _species_code_check_sql(dialect: str) -> str:
    reserved = ", ".join(f"'{name}'" for name in sorted(WINDOWS_RESERVED_NAMES))
    if dialect == "sqlite":
        allowed = (
            "substr(code, 1, 1) GLOB '[A-Z]' "
            "AND code NOT GLOB '*[^A-Z0-9_-]*'"
        )
    else:
        allowed = "code ~ '^[A-Z][A-Z0-9_-]{0,31}$'"
    return (
        "length(code) BETWEEN 1 AND 32 "
        f"AND {allowed} "
        f"AND code NOT IN ({reserved})"
    )


def _constraint_sql() -> str:
    return _species_code_check_sql(context.get_context().dialect.name)


def _validate_existing_species_codes() -> None:
    if context.is_offline_mode():
        op.execute(
            sa.text(
                "DO $$ BEGIN "
                f"IF EXISTS (SELECT 1 FROM species WHERE NOT ({_constraint_sql()})) "
                "THEN RAISE EXCEPTION 'unsafe species codes block revision 20260826_05'; "
                "END IF; END $$"
            )
        )
        return
    codes = list(op.get_bind().execute(sa.text("SELECT code FROM species")).scalars())
    unsafe = sorted(code for code in codes if not _is_safe_species_code(code))
    if unsafe:
        raise RuntimeError(
            "unsafe species codes block revision 20260826_05: " + ", ".join(unsafe)
        )


def _backfill_scope_keys() -> None:
    if context.is_offline_mode():
        op.execute(
            sa.text(
                "UPDATE export_batches SET scope_key = "
                "CASE WHEN species_id IS NULL THEN 'ALL' "
                "ELSE CAST(species_id AS VARCHAR(36)) END"
            )
        )
        return
    bind = op.get_bind()
    rows = list(
        bind.execute(
            sa.text("SELECT id, species_id FROM export_batches")
        ).mappings()
    )
    for row in rows:
        scope_key = "ALL" if row["species_id"] is None else str(UUID(str(row["species_id"])))
        bind.execute(
            sa.text("UPDATE export_batches SET scope_key = :scope_key WHERE id = :id"),
            {"scope_key": scope_key, "id": row["id"]},
        )


def upgrade() -> None:
    _validate_existing_species_codes()
    with op.batch_alter_table("species") as batch:
        batch.create_check_constraint("ck_species_code_safe", _constraint_sql())
    with op.batch_alter_table("export_batches") as batch:
        batch.add_column(sa.Column("scope_key", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True))
    _backfill_scope_keys()
    with op.batch_alter_table("export_batches") as batch:
        batch.alter_column("scope_key", existing_type=sa.String(length=36), nullable=False)
        batch.create_check_constraint(
            "ck_export_batches_status",
            "status IN ('pending', 'completed', 'expired')",
        )
    op.create_index(
        "uq_export_batches_pending_scope",
        "export_batches",
        ["scope_key"],
        unique=True,
        sqlite_where=sa.text("status = 'pending'"),
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_export_batches_status_expires",
        "export_batches",
        ["status", "expires_at"],
    )

    snapshot_columns = (
        sa.Column("candidate_version", sa.Integer(), nullable=True),
        sa.Column("species_code", sa.String(length=32), nullable=True),
        sa.Column("preview_url", sa.String(length=2048), nullable=True),
        sa.Column("original_url", sa.String(length=2048), nullable=True),
        sa.Column("source_url", sa.String(length=2048), nullable=True),
        sa.Column("creator", sa.String(length=512), nullable=True),
        sa.Column("license", sa.String(length=255), nullable=True),
        sa.Column("license_url", sa.String(length=2048), nullable=True),
        sa.Column("attribution", sa.String(length=1024), nullable=True),
        sa.Column("original_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("metadata_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("local_relative_path", sa.String(length=1024), nullable=True),
    )
    with op.batch_alter_table("export_items") as batch:
        for column in snapshot_columns:
            batch.add_column(column)
    op.execute(
        sa.text(
            "UPDATE export_items SET "
            "candidate_version = (SELECT version FROM candidates WHERE candidates.id = export_items.candidate_id), "
            "species_code = (SELECT species.code FROM candidates JOIN species ON species.id = candidates.species_id WHERE candidates.id = export_items.candidate_id), "
            "preview_url = (SELECT preview_url FROM candidates WHERE candidates.id = export_items.candidate_id), "
            "original_url = (SELECT original_url FROM candidates WHERE candidates.id = export_items.candidate_id), "
            "source_url = (SELECT source_url FROM candidates WHERE candidates.id = export_items.candidate_id), "
            "creator = (SELECT creator FROM candidates WHERE candidates.id = export_items.candidate_id), "
            "license = (SELECT license FROM candidates WHERE candidates.id = export_items.candidate_id), "
            "license_url = (SELECT license_url FROM candidates WHERE candidates.id = export_items.candidate_id), "
            "attribution = (SELECT attribution FROM candidates WHERE candidates.id = export_items.candidate_id), "
            "original_fingerprint = '0000000000000000000000000000000000000000000000000000000000000000', "
            "metadata_fingerprint = '0000000000000000000000000000000000000000000000000000000000000000', "
            "local_relative_path = CASE WHEN status = 'succeeded' THEN target_relative_path ELSE NULL END, "
            "status = CASE WHEN status = 'succeeded' THEN 'succeeded' ELSE 'pending' END"
        )
    )
    with op.batch_alter_table("export_items") as batch:
        for name, type_ in (
            ("candidate_version", sa.Integer()),
            ("species_code", sa.String(length=32)),
            ("preview_url", sa.String(length=2048)),
            ("original_url", sa.String(length=2048)),
            ("source_url", sa.String(length=2048)),
            ("license", sa.String(length=255)),
            ("attribution", sa.String(length=1024)),
            ("original_fingerprint", sa.String(length=64)),
            ("metadata_fingerprint", sa.String(length=64)),
        ):
            batch.alter_column(name, existing_type=type_, nullable=False)
        batch.create_check_constraint(
            "ck_export_items_status", "status IN ('pending', 'succeeded')"
        )
    op.create_index(
        "ix_export_items_candidate_succeeded",
        "export_items",
        ["candidate_id", "succeeded_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_export_items_candidate_succeeded", table_name="export_items")
    with op.batch_alter_table("export_items") as batch:
        batch.drop_constraint("ck_export_items_status", type_="check")
        for name in (
            "local_relative_path",
            "metadata_fingerprint",
            "original_fingerprint",
            "attribution",
            "license_url",
            "license",
            "creator",
            "source_url",
            "original_url",
            "preview_url",
            "species_code",
            "candidate_version",
        ):
            batch.drop_column(name)
    op.drop_index("ix_export_batches_status_expires", table_name="export_batches")
    op.drop_index("uq_export_batches_pending_scope", table_name="export_batches")
    with op.batch_alter_table("export_batches") as batch:
        batch.drop_constraint("ck_export_batches_status", type_="check")
        batch.drop_column("expired_at")
        batch.drop_column("completed_at")
        batch.drop_column("scope_key")
    with op.batch_alter_table("species") as batch:
        batch.drop_constraint("ck_species_code_safe", type_="check")
