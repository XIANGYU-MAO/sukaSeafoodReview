from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, JSON, LargeBinary, String, TypeDecorator
from sqlalchemy.orm import Mapped, mapped_column

from app.models.auth import Base, TimestampMixin


class UTCDateTime(TypeDecorator):
    """Return aware UTC datetimes even when SQLite drops timezone metadata."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class CandidateImportPreview(TimestampMixin, Base):
    __tablename__ = "candidate_import_previews"
    __table_args__ = (
        Index(
            "uq_candidate_import_previews_token_digest",
            "token_digest",
            unique=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    actor_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[bytes | None] = mapped_column(LargeBinary)
    report_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    database_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True, nullable=False)
    committed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
