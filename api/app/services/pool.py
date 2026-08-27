from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Candidate, Review, Species, User
from app.schemas.review import ReviewFilters
from app.services.auth import utc_now
from app.services.sync_generation import acquire_sync_generation_lock


def eligible_candidate_query(filters: ReviewFilters) -> Select[tuple[Candidate]]:
    reviewed = exists(
        select(Review.id).where(
            Review.candidate_id == Candidate.id,
            Review.is_current.is_(True),
        )
    )
    statement = (
        select(Candidate)
        .join(Species, Species.id == Candidate.species_id)
        .where(
            Candidate.active.is_(True),
            Species.active.is_(True),
            Candidate.current_reviewer_id.is_(None),
            ~reviewed,
        )
        .order_by(Candidate.id)
    )
    if filters.species_code is not None:
        statement = statement.where(Species.code == filters.species_code)
    if filters.source_dataset is not None:
        statement = statement.where(Candidate.source_dataset == filters.source_dataset)
    return statement.with_for_update(of=Candidate, skip_locked=True).limit(1)


async def lock_species_for_assignment(
    session: AsyncSession, species_id: UUID
) -> Species | None:
    return await session.scalar(
        select(Species)
        .where(Species.id == species_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )


async def get_or_open_current(
    session: AsyncSession,
    user_id: UUID,
    filters: ReviewFilters,
) -> Candidate | None:
    try:
        await acquire_sync_generation_lock(session)
        user = await session.scalar(
            select(User).where(User.id == user_id).with_for_update()
        )
        if user is None or not user.active:
            await session.rollback()
            return None

        current = await session.scalar(
            select(Candidate).where(Candidate.current_reviewer_id == user_id)
        )
        if current is not None:
            await session.commit()
            return current

        candidate = await session.scalar(eligible_candidate_query(filters))
        if candidate is None:
            await session.commit()
            return None

        species = await lock_species_for_assignment(session, candidate.species_id)
        reviewed = await session.scalar(
            select(
                exists().where(
                    Review.candidate_id == candidate.id,
                    Review.is_current.is_(True),
                )
            )
        )
        if (
            species is None
            or not species.active
            or not candidate.active
            or candidate.current_reviewer_id is not None
            or reviewed
        ):
            await session.rollback()
            return None

        candidate.current_reviewer_id = user_id
        candidate.current_started_at = utc_now()
        await session.commit()
        return candidate
    except BaseException:
        await session.rollback()
        raise
