from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.auth import Base


class ImageOriginApproval(Base):
    __tablename__ = "image_origin_approvals"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    hostname: Mapped[str] = mapped_column(String(253), unique=True, nullable=False)
    approved_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
