from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    CurrentAuth,
    require_admin_access,
    require_admin_csrf,
)
from app.database import get_db
from app.schemas.admin import (
    AdminReviewFilters,
    AdminReviewListResponse,
    AdminReviewPatchRequest,
    AdminUserListResponse,
    CandidateAdminResponse,
    CandidateFilters,
    CandidateListResponse,
    CandidatePatchRequest,
    CandidateVersionReason,
    CurrentFilters,
    CurrentListResponse,
    ReopenRequest,
    ResetPasswordRequest,
    ResetPasswordResponse,
    SpeciesCreateRequest,
    SpeciesFilters,
    SpeciesListResponse,
    SpeciesPatchRequest,
    SpeciesResponse,
    TransferRequest,
)
from app.schemas.review import ReviewResponse
from app.services.admin import (
    AdminConflict,
    AdminObjectNotFound,
    create_species,
    edit_admin_review,
    list_admin_reviews,
    list_admin_users,
    list_candidates,
    list_current,
    list_species,
    patch_candidate,
    patch_species,
    release_current,
    reopen_review,
    reset_password_transaction,
    transfer_current,
)


router = APIRouter(prefix="/admin", tags=["admin"])


def _raise_admin_error(exc: Exception) -> None:
    if isinstance(exc, AdminObjectNotFound):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from exc
    if isinstance(exc, AdminConflict):
        detail = {"code": exc.code}
        if exc.latest is not None:
            detail["latest"] = exc.latest
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=detail
        ) from exc
    raise exc


@router.get("/users", response_model=AdminUserListResponse)
async def get_users(
    _: CurrentAuth = Depends(require_admin_access),
    db: AsyncSession = Depends(get_db),
) -> AdminUserListResponse:
    return await list_admin_users(db)


@router.get("/species", response_model=SpeciesListResponse)
async def get_species(
    filters: Annotated[SpeciesFilters, Depends()],
    _: CurrentAuth = Depends(require_admin_access),
    db: AsyncSession = Depends(get_db),
) -> SpeciesListResponse:
    return await list_species(db, filters)


@router.post(
    "/species",
    response_model=SpeciesResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_species(
    payload: SpeciesCreateRequest,
    auth: CurrentAuth = Depends(require_admin_csrf),
    db: AsyncSession = Depends(get_db),
) -> SpeciesResponse:
    try:
        return await create_species(db, auth.user.id, payload)
    except (AdminObjectNotFound, AdminConflict) as exc:
        _raise_admin_error(exc)


@router.patch("/species/{species_id}", response_model=SpeciesResponse)
async def update_species(
    species_id: UUID,
    payload: SpeciesPatchRequest,
    auth: CurrentAuth = Depends(require_admin_csrf),
    db: AsyncSession = Depends(get_db),
) -> SpeciesResponse:
    try:
        return await patch_species(db, auth.user.id, species_id, payload)
    except (AdminObjectNotFound, AdminConflict) as exc:
        _raise_admin_error(exc)


@router.get("/candidates", response_model=CandidateListResponse)
async def get_candidates(
    filters: Annotated[CandidateFilters, Depends()],
    _: CurrentAuth = Depends(require_admin_access),
    db: AsyncSession = Depends(get_db),
) -> CandidateListResponse:
    return await list_candidates(db, filters)


@router.patch("/candidates/{candidate_id}", response_model=CandidateAdminResponse)
async def update_candidate(
    candidate_id: UUID,
    payload: CandidatePatchRequest,
    auth: CurrentAuth = Depends(require_admin_csrf),
    db: AsyncSession = Depends(get_db),
) -> CandidateAdminResponse:
    try:
        return await patch_candidate(db, auth.user.id, candidate_id, payload)
    except (AdminObjectNotFound, AdminConflict) as exc:
        _raise_admin_error(exc)


@router.get("/reviews", response_model=AdminReviewListResponse)
async def get_reviews(
    filters: Annotated[AdminReviewFilters, Depends()],
    _: CurrentAuth = Depends(require_admin_access),
    db: AsyncSession = Depends(get_db),
) -> AdminReviewListResponse:
    if (
        filters.date_from is not None
        and filters.date_to is not None
        and filters.date_from > filters.date_to
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="date_from must not be after date_to",
        )
    return await list_admin_reviews(db, filters)


@router.patch("/reviews/{review_id}", response_model=ReviewResponse)
async def update_review(
    review_id: UUID,
    payload: AdminReviewPatchRequest,
    auth: CurrentAuth = Depends(require_admin_csrf),
    db: AsyncSession = Depends(get_db),
) -> ReviewResponse:
    try:
        return await edit_admin_review(db, auth.user.id, review_id, payload)
    except (AdminObjectNotFound, AdminConflict) as exc:
        _raise_admin_error(exc)


@router.get("/current", response_model=CurrentListResponse)
async def get_current(
    filters: Annotated[CurrentFilters, Depends()],
    _: CurrentAuth = Depends(require_admin_access),
    db: AsyncSession = Depends(get_db),
) -> CurrentListResponse:
    return await list_current(db, filters)


@router.post("/current/{candidate_id}/release", response_model=CandidateAdminResponse)
async def release(
    candidate_id: UUID,
    payload: CandidateVersionReason,
    auth: CurrentAuth = Depends(require_admin_csrf),
    db: AsyncSession = Depends(get_db),
) -> CandidateAdminResponse:
    try:
        return await release_current(db, auth.user.id, candidate_id, payload)
    except (AdminObjectNotFound, AdminConflict) as exc:
        _raise_admin_error(exc)


@router.post("/current/{candidate_id}/transfer", response_model=CandidateAdminResponse)
async def transfer(
    candidate_id: UUID,
    payload: TransferRequest,
    auth: CurrentAuth = Depends(require_admin_csrf),
    db: AsyncSession = Depends(get_db),
) -> CandidateAdminResponse:
    try:
        return await transfer_current(db, auth.user.id, candidate_id, payload)
    except (AdminObjectNotFound, AdminConflict) as exc:
        _raise_admin_error(exc)


@router.post("/reviews/{review_id}/reopen", response_model=CandidateAdminResponse)
async def reopen(
    review_id: UUID,
    payload: ReopenRequest,
    auth: CurrentAuth = Depends(require_admin_csrf),
    db: AsyncSession = Depends(get_db),
) -> CandidateAdminResponse:
    try:
        return await reopen_review(db, auth.user.id, review_id, payload)
    except (AdminObjectNotFound, AdminConflict) as exc:
        _raise_admin_error(exc)


@router.post("/users/{user_id}/reset-password", response_model=ResetPasswordResponse)
async def reset_password(
    user_id: UUID,
    payload: ResetPasswordRequest,
    auth: CurrentAuth = Depends(require_admin_csrf),
    db: AsyncSession = Depends(get_db),
) -> ResetPasswordResponse:
    try:
        temporary_password = await reset_password_transaction(
            db,
            actor_id=auth.user.id,
            target_user_id=user_id,
            reason=payload.reason,
            allow_admin=False,
        )
    except (AdminObjectNotFound, AdminConflict) as exc:
        _raise_admin_error(exc)
    return ResetPasswordResponse(temporary_password=temporary_password)
