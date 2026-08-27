from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.image_origins import normalize_image_origin_allowlist
from app.models import ImageOriginApproval


async def effective_image_origin_allowlist(
    session: AsyncSession, configured: tuple[str, ...]
) -> tuple[str, ...]:
    approved = tuple(
        (
            await session.scalars(
                select(ImageOriginApproval.hostname).order_by(
                    ImageOriginApproval.hostname
                )
            )
        ).all()
    )
    return normalize_image_origin_allowlist((*configured, *approved))
