from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Index,
    JSON,
    String,
    UniqueConstraint,
    func,
    true,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.auth import Base, TimestampMixin
from app.species_codes import (
    POSTGRESQL_SPECIES_CODE_CHECK_SQL,
    SQLITE_SPECIES_CODE_CHECK_SQL,
)


class Species(TimestampMixin, Base):
    __tablename__ = "species"
    __table_args__ = (
        CheckConstraint(
            SQLITE_SPECIES_CODE_CHECK_SQL, name="ck_species_code_safe"
        ).ddl_if(dialect="sqlite"),
        CheckConstraint(
            POSTGRESQL_SPECIES_CODE_CHECK_SQL, name="ck_species_code_safe"
        ).ddl_if(dialect="postgresql"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name_zh: Mapped[str] = mapped_column(String(255), nullable=False)
    name_en: Mapped[str] = mapped_column(String(255), nullable=False)
    scientific_name: Mapped[str] = mapped_column(String(255), nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )


class Candidate(TimestampMixin, Base):
    __tablename__ = "candidates"
    __table_args__ = (
        UniqueConstraint(
            "source_dataset",
            "source_record_id",
            name="uq_candidates_source_record",
        ),
        Index(
            "uq_candidates_current_reviewer_nonnull",
            "current_reviewer_id",
            unique=True,
            sqlite_where=text("current_reviewer_id IS NOT NULL"),
            postgresql_where=text("current_reviewer_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    species_id: Mapped[UUID] = mapped_column(
        ForeignKey("species.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    source_dataset: Mapped[str] = mapped_column(String(128), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(255), nullable=False)
    preview_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    original_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    creator: Mapped[str | None] = mapped_column(String(512))
    license: Mapped[str] = mapped_column(String(255), nullable=False)
    license_url: Mapped[str | None] = mapped_column(String(2048))
    attribution: Mapped[str] = mapped_column(String(1024), nullable=False)
    location: Mapped[str | None] = mapped_column(String(512))
    observed_on: Mapped[date | None] = mapped_column(Date)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    current_reviewer_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )
    current_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
