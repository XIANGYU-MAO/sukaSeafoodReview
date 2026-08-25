from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentAuth, require_review_access
from app.database import get_db
from app.models import Species
from app.schemas.review import (
    CandidateResponse,
    DecisionRequest,
    ReviewFilters,
    ReviewResponse,
    SpeciesSummary,
)
from app.services.pool import get_or_open_current
from app.services.reviews import (
    IdempotencyConflict,
    ReviewAssignmentConflict,
    submit_decision,
)


router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.post(
    "/current",
    response_model=CandidateResponse,
    responses={status.HTTP_204_NO_CONTENT: {"description": "Pool is empty"}},
)
async def current_candidate(
    filters: Annotated[ReviewFilters, Depends()],
    auth: CurrentAuth = Depends(require_review_access),
    db: AsyncSession = Depends(get_db),
) -> CandidateResponse | Response:
    candidate = await get_or_open_current(db, auth.user.id, filters)
    if candidate is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    species = await db.get(Species, candidate.species_id)
    if species is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    return CandidateResponse(
        id=candidate.id,
        species=SpeciesSummary.model_validate(
            {
                "code": species.code,
                "name_zh": species.name_zh,
                "name_en": species.name_en,
                "scientific_name": species.scientific_name,
            }
        ),
        source_dataset=candidate.source_dataset,
        source_record_id=candidate.source_record_id,
        preview_url=candidate.preview_url,
        original_url=candidate.original_url,
        source_url=candidate.source_url,
        creator=candidate.creator,
        license=candidate.license,
        license_url=candidate.license_url,
        attribution=candidate.attribution,
        location=candidate.location,
        observed_on=candidate.observed_on,
        metadata=candidate.metadata_json,
    )


@router.post(
    "/{candidate_id}/decision",
    response_model=ReviewResponse,
    status_code=status.HTTP_201_CREATED,
)
async def decide(
    candidate_id: UUID,
    payload: DecisionRequest,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=255),
    ],
    auth: CurrentAuth = Depends(require_review_access),
    db: AsyncSession = Depends(get_db),
) -> ReviewResponse:
    if not idempotency_key.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Idempotency-Key must not be blank",
        )
    try:
        review = await submit_decision(
            db,
            auth.user.id,
            candidate_id,
            idempotency_key,
            payload,
        )
    except IdempotencyConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency-Key was already used for another request",
        ) from exc
    except ReviewAssignmentConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Candidate is not assigned to this user",
        ) from exc
    return ReviewResponse.from_review(review)
