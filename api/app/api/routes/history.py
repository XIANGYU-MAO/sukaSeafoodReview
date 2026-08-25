from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    CurrentAuth,
    require_completed_password_change,
    require_review_access,
)
from app.database import get_db
from app.schemas.history import HistoryEditRequest, HistoryFilters, HistoryResponse
from app.schemas.review import ReviewResponse
from app.services.history import (
    HistoryReviewNotFound,
    HistoryReviewReadOnly,
    StaleReviewVersion,
    edit_review,
    get_history,
)


router = APIRouter(prefix="/history", tags=["history"])


@router.get("", response_model=HistoryResponse)
async def history(
    filters: Annotated[HistoryFilters, Depends()],
    auth: CurrentAuth = Depends(require_completed_password_change),
    db: AsyncSession = Depends(get_db),
) -> HistoryResponse:
    if filters.reviewer is not None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    if (
        filters.date_from is not None
        and filters.date_to is not None
        and filters.date_from > filters.date_to
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="date_from must not be after date_to",
        )
    return await get_history(db, auth.user.id, filters)


@router.patch("/{review_id}", response_model=ReviewResponse)
async def update_history(
    review_id: UUID,
    payload: HistoryEditRequest,
    auth: CurrentAuth = Depends(require_review_access),
    db: AsyncSession = Depends(get_db),
) -> ReviewResponse:
    try:
        return await edit_review(db, auth.user.id, review_id, payload)
    except HistoryReviewNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from exc
    except HistoryReviewReadOnly as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "REVIEW_NOT_CURRENT"},
        ) from exc
    except StaleReviewVersion as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "STALE_REVIEW_VERSION",
                "latest": exc.latest.model_dump(mode="json"),
            },
        ) from exc
