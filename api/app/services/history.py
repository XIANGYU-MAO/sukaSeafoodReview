from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Candidate, Review, ReviewRevision, Species
from app.schemas.history import (
    HistoryEditRequest,
    HistoryFacets,
    HistoryFilters,
    HistoryItem,
    HistoryResponse,
)
from app.schemas.review import ReviewResponse, SpeciesSummary
from app.services.admin import audited_change
from app.services.reviews import canonical_facts, review_snapshot
from app.services.sync_generation import acquire_sync_generation_lock


class HistoryReviewNotFound(Exception):
    pass


class HistoryReviewReadOnly(Exception):
    pass


@dataclass(frozen=True)
class StaleReviewVersion(Exception):
    latest: ReviewResponse


def review_for_update_query(
    reviewer_id: UUID, review_id: UUID
) -> Select[tuple[Review]]:
    return (
        select(Review)
        .where(Review.id == review_id, Review.reviewer_id == reviewer_id)
        .with_for_update(of=Review)
        .execution_options(populate_existing=True)
    )


def _history_conditions(reviewer_id: UUID, filters: HistoryFilters):
    conditions = [Review.reviewer_id == reviewer_id]
    if filters.species_code is not None:
        conditions.append(Species.code == filters.species_code)
    if filters.source_dataset is not None:
        conditions.append(Candidate.source_dataset == filters.source_dataset)
    if filters.decision is not None:
        conditions.append(Review.decision == filters.decision)
    if filters.date_from is not None:
        conditions.append(
            Review.created_at
            >= datetime.combine(filters.date_from, time.min, tzinfo=timezone.utc)
        )
    if filters.date_to is not None:
        conditions.append(
            Review.created_at
            < datetime.combine(filters.date_to, time.min, tzinfo=timezone.utc)
            + timedelta(days=1)
        )
    return conditions


async def get_history(
    session: AsyncSession,
    reviewer_id: UUID,
    filters: HistoryFilters,
) -> HistoryResponse:
    conditions = _history_conditions(reviewer_id, filters)
    joined = (
        select(Review, Candidate, Species)
        .join(Candidate, Candidate.id == Review.candidate_id)
        .join(Species, Species.id == Candidate.species_id)
        .where(*conditions)
    )
    total = int(
        await session.scalar(
            select(func.count(Review.id))
            .join(Candidate, Candidate.id == Review.candidate_id)
            .join(Species, Species.id == Candidate.species_id)
            .where(*conditions)
        )
        or 0
    )
    rows = (
        await session.execute(
            joined.order_by(Review.created_at.desc(), Review.id.desc())
            .offset(filters.offset)
            .limit(filters.limit)
        )
    ).all()
    species_rows = (
        await session.execute(
            select(
                Species.code,
                Species.name_zh,
                Species.name_en,
                Species.scientific_name,
                Species.sort_order,
            )
            .join(Candidate, Candidate.species_id == Species.id)
            .join(Review, Review.candidate_id == Candidate.id)
            .where(Review.reviewer_id == reviewer_id)
            .distinct()
            .order_by(Species.sort_order, Species.code)
        )
    ).all()
    source_rows = (
        await session.scalars(
            select(Candidate.source_dataset)
            .join(Review, Review.candidate_id == Candidate.id)
            .where(Review.reviewer_id == reviewer_id)
            .distinct()
            .order_by(Candidate.source_dataset)
        )
    ).all()
    items = [
        HistoryItem(
            id=review.id,
            candidate_id=review.candidate_id,
            reviewer_id=review.reviewer_id,
            decision=review.decision,
            rejection_reason=review.rejection_reason,
            notes=review.notes,
            whole_fish=review.whole_fish,
            exact_species_verified=review.exact_species_verified,
            is_current=review.is_current,
            read_only=not review.is_current,
            version=review.version,
            created_at=review.created_at,
            updated_at=review.updated_at,
            species=SpeciesSummary(
                code=species.code,
                name_zh=species.name_zh,
                name_en=species.name_en,
                scientific_name=species.scientific_name,
            ),
            source_dataset=candidate.source_dataset,
            source_record_id=candidate.source_record_id,
            preview_url=candidate.preview_url,
            original_url=candidate.original_url,
            source_url=candidate.source_url,
        )
        for review, candidate, species in rows
    ]
    return HistoryResponse(
        total=total,
        items=items,
        filters=HistoryFacets(
            species=[
                SpeciesSummary(
                    code=code,
                    name_zh=name_zh,
                    name_en=name_en,
                    scientific_name=scientific_name,
                )
                for code, name_zh, name_en, scientific_name, _ in species_rows
            ],
            sources=list(source_rows),
        ),
    )


async def edit_review(
    session: AsyncSession,
    reviewer_id: UUID,
    review_id: UUID,
    payload: HistoryEditRequest,
) -> ReviewResponse:
    try:
        await acquire_sync_generation_lock(session)
        candidate_id = await session.scalar(
            select(Review.candidate_id).where(
                Review.id == review_id, Review.reviewer_id == reviewer_id
            )
        )
        if candidate_id is None:
            await session.rollback()
            raise HistoryReviewNotFound
        candidate = await session.scalar(
            select(Candidate)
            .where(Candidate.id == candidate_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if candidate is None:
            await session.rollback()
            raise HistoryReviewNotFound
        review = await session.scalar(
            review_for_update_query(reviewer_id, review_id)
        )
        if review is None:
            await session.rollback()
            raise HistoryReviewNotFound
        if not review.is_current:
            await session.rollback()
            raise HistoryReviewReadOnly
        if review.version != payload.version:
            latest = ReviewResponse.from_review(review)
            await session.rollback()
            raise StaleReviewVersion(latest)

        before = review_snapshot(review)
        whole_fish, exact_species = canonical_facts(payload)
        review.decision = payload.decision
        review.rejection_reason = (
            payload.rejection_reason.value
            if payload.rejection_reason is not None
            else None
        )
        review.notes = payload.notes
        review.whole_fish = whole_fish
        review.exact_species_verified = exact_species
        review.version += 1
        candidate.version += 1
        after = review_snapshot(review)
        session.add(
            ReviewRevision(
                candidate_id=review.candidate_id,
                review_id=review.id,
                reviewer_id=review.reviewer_id,
                actor_id=reviewer_id,
                decision=review.decision,
                rejection_reason=review.rejection_reason,
                notes=review.notes,
                whole_fish=review.whole_fish,
                exact_species_verified=review.exact_species_verified,
                is_current=review.is_current,
                review_version=review.version,
                snapshot_json={"before": before, "after": after},
            )
        )
        await audited_change(
            session,
            action="REVIEW_SELF_UPDATE",
            actor_id=reviewer_id,
            object_type="Review",
            object_id=review.id,
            reason=None,
            before=before,
            after=after,
        )
        await session.commit()
        return ReviewResponse.from_review(review)
    except BaseException:
        if session.in_transaction():
            await session.rollback()
        raise
