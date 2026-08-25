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
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.auth import Base


class ExportAction(StrEnum):
    ADD = "ADD"
    REMOVE = "REMOVE"
    MOVE = "MOVE"


class ExportBatch(Base):
    __tablename__ = "export_batches"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    created_by_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    species_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("species.id", ondelete="RESTRICT"), index=True
    )
    receipt_token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(32), default="pending", server_default="pending", nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
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
    action: Mapped[ExportAction] = mapped_column(
        Enum(ExportAction, native_enum=False), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(32), default="pending", server_default="pending", nullable=False
    )
    target_relative_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    previous_relative_path: Mapped[str | None] = mapped_column(String(1024))
    sha256: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    succeeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
