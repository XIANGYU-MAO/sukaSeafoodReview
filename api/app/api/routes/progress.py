from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentAuth, require_completed_password_change
from app.database import get_db
from app.schemas.progress import ProgressResponse
from app.services.progress import get_progress


router = APIRouter(prefix="/progress", tags=["progress"])


@router.get("", response_model=ProgressResponse)
async def progress(
    _: CurrentAuth = Depends(require_completed_password_change),
    db: AsyncSession = Depends(get_db),
) -> ProgressResponse:
    return await get_progress(db)
