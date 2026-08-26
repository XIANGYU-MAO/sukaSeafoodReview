from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.auth import Base


class ExportAction(StrEnum):
    ADD = "ADD"
    REMOVE = "REMOVE"
    MOVE = "MOVE"


class ExportBatch(Base):
    __tablename__ = "export_batches"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'completed', 'expired')",
            name="ck_export_batches_status",
        ),
        Index("ix_export_batches_status_expires", "status", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    created_by_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    species_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("species.id", ondelete="RESTRICT"), index=True
    )
    scope_key: Mapped[str] = mapped_column(String(36), nullable=False)
    receipt_token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(32), default="pending", server_default="pending", nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ExportItem(Base):
    __tablename__ = "export_items"
    __table_args__ = (
        CheckConstraint(
            "action IN ('ADD', 'REMOVE', 'MOVE')",
            name="ck_export_items_action",
        ),
        Index(
            "uq_export_items_batch_candidate_action",
            "batch_id",
            "candidate_id",
            "action",
            unique=True,
        ),
        CheckConstraint(
            "status IN ('pending', 'succeeded')",
            name="ck_export_items_status",
        ),
        Index(
            "ix_export_items_candidate_succeeded",
            "candidate_id",
            "succeeded_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("export_batches.id", ondelete="CASCADE"), index=True, nullable=False
    )
    candidate_id: Mapped[UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    review_id: Mapped[UUID] = mapped_column(
        ForeignKey("reviews.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    review_version: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[ExportAction] = mapped_column(
        Enum(ExportAction, native_enum=False), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(32), default="pending", server_default="pending", nullable=False
    )
    target_relative_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    previous_relative_path: Mapped[str | None] = mapped_column(String(1024))
    species_code: Mapped[str] = mapped_column(String(32), nullable=False)
    preview_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    original_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    creator: Mapped[str | None] = mapped_column(String(512))
    license: Mapped[str] = mapped_column(String(255), nullable=False)
    license_url: Mapped[str | None] = mapped_column(String(2048))
    attribution: Mapped[str] = mapped_column(String(1024), nullable=False)
    original_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64))
    local_relative_path: Mapped[str | None] = mapped_column(String(1024))
    error: Mapped[str | None] = mapped_column(Text)
    succeeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
