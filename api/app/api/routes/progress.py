from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentAuth, require_completed_password_change
from app.database import get_db
from app.schemas.progress import ProgressResponse
from app.services.progress import get_progress
from app.services.settings import get_system_settings


router = APIRouter(prefix="/progress", tags=["progress"])


@router.get("", response_model=ProgressResponse)
async def progress(
    auth: CurrentAuth = Depends(require_completed_password_change),
    db: AsyncSession = Depends(get_db),
) -> ProgressResponse:
    settings = await get_system_settings(db)
    if auth.user.role != "admin" and not settings.reviewer_team_progress_visible:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return await get_progress(db)
