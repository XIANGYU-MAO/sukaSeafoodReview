from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AuditEvent,
    Candidate,
    Decision,
    Review,
    ReviewRevision,
    Session,
    Species,
    User,
)
from app.schemas.admin import (
    AdminCandidateSummary,
    AdminReviewFilters,
    AdminReviewItem,
    AdminReviewListResponse,
    AdminReviewPatchRequest,
    AdminReviewSummary,
    AdminSourceListResponse,
    AdminSpeciesSummary,
    AdminUserDirectoryItem,
    AdminUserListResponse,
    AdminUserSummary,
    CandidateAdminResponse,
    CandidateFilters,
    CandidateListResponse,
    CandidatePatchRequest,
    CandidateVersionReason,
    CurrentFilters,
    CurrentItem,
    CurrentListResponse,
    ReopenRequest,
    SpeciesCreateRequest,
    SpeciesFilters,
    SpeciesListResponse,
    SpeciesPatchRequest,
    SpeciesResponse,
    TransferRequest,
)
from app.schemas.review import ReviewResponse
from app.services.auth import (
    FIXED_USERS,
    generate_temporary_password,
    hash_password,
    utc_now,
)
from app.services.pool import lock_species_for_assignment
from app.services.reviews import canonical_facts, review_snapshot


@dataclass(frozen=True)
class AdminConflict(Exception):
    code: str
    latest: dict[str, Any] | None = None


class AdminObjectNotFound(Exception):
    pass


def _decision_value(value: Decision | str) -> str:
    return value.value if isinstance(value, Decision) else str(value)


def species_snapshot(species: Species) -> dict[str, Any]:
    return {
        "id": str(species.id),
        "code": species.code,
        "name_zh": species.name_zh,
        "name_en": species.name_en,
        "scientific_name": species.scientific_name,
        "active": species.active,
        "sort_order": species.sort_order,
    }


def candidate_snapshot(candidate: Candidate) -> dict[str, Any]:
    return {
        "id": str(candidate.id),
        "species_id": str(candidate.species_id),
        "source_dataset": candidate.source_dataset,
        "source_record_id": candidate.source_record_id,
        "preview_url": candidate.preview_url,
        "original_url": candidate.original_url,
        "source_url": candidate.source_url,
        "creator": candidate.creator,
        "license": candidate.license,
        "license_url": candidate.license_url,
        "attribution": candidate.attribution,
        "location": candidate.location,
        "observed_on": (
            candidate.observed_on.isoformat()
            if candidate.observed_on is not None
            else None
        ),
        "metadata": candidate.metadata_json,
        "current_reviewer_id": (
            str(candidate.current_reviewer_id)
            if candidate.current_reviewer_id is not None
            else None
        ),
        "current_started_at": (
            candidate.current_started_at.isoformat()
            if candidate.current_started_at is not None
            else None
        ),
        "active": candidate.active,
        "version": candidate.version,
    }


async def audited_change(
    session: AsyncSession,
    *,
    action: str,
    actor_id: UUID | None,
    object_type: str,
    object_id: UUID | str,
    reason: str | None,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> AuditEvent:
    def redact(value):
        if isinstance(value, dict):
            return {
                key: (
                    "[REDACTED]"
                    if any(
                        marker in key.lower()
                        for marker in ("password", "hash", "token", "csrf", "secret")
                    )
                    else redact(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [redact(item) for item in value]
        return value

    event = AuditEvent(
        actor_id=actor_id,
        action=action,
        object_type=object_type,
        object_id=str(object_id),
        reason=reason,
        before_json=redact(before),
        after_json=redact(after),
    )
    session.add(event)
    return event


def _species_summary(species: Species) -> AdminSpeciesSummary:
    return AdminSpeciesSummary(
        id=species.id,
        code=species.code,
        name_zh=species.name_zh,
        name_en=species.name_en,
        scientific_name=species.scientific_name,
        active=species.active,
    )


def _user_summary(user: User) -> AdminUserSummary:
    return AdminUserSummary(id=user.id, display_name=user.name, active=user.active)


def _candidate_summary(candidate: Candidate) -> AdminCandidateSummary:
    return AdminCandidateSummary(
        id=candidate.id,
        source_dataset=candidate.source_dataset,
        source_record_id=candidate.source_record_id,
        preview_url=candidate.preview_url,
        original_url=candidate.original_url,
        source_url=candidate.source_url,
        active=candidate.active,
        version=candidate.version,
    )


async def list_admin_users(session: AsyncSession) -> AdminUserListResponse:
    fixed_names = [name for name, _ in FIXED_USERS]
    users = list(
        (await session.scalars(select(User).where(User.name.in_(fixed_names)))).all()
    )
    by_name = {user.name: user for user in users}
    items = [
        AdminUserDirectoryItem(
            id=by_name[name].id,
            display_name=by_name[name].name,
            active=by_name[name].active,
            role=by_name[name].role,
        )
        for name in fixed_names
        if name in by_name
    ]
    return AdminUserListResponse(total=len(items), items=items)


async def list_admin_sources(session: AsyncSession) -> AdminSourceListResponse:
    sources = list(
        (
            await session.scalars(
                select(Candidate.source_dataset)
                .distinct()
                .limit(1001)
            )
        ).all()
    )
    if len(sources) > 1000:
        raise RuntimeError("admin source catalog exceeds supported cardinality")
    return AdminSourceListResponse(
        sources=sorted(sources, key=lambda value: (value.casefold(), value))
    )


async def _candidate_response(
    session: AsyncSession, candidate: Candidate
) -> CandidateAdminResponse:
    species = await session.get(Species, candidate.species_id)
    if species is None:
        raise RuntimeError("candidate species is missing")
    current_reviewer = (
        await session.get(User, candidate.current_reviewer_id)
        if candidate.current_reviewer_id is not None
        else None
    )
    current_review = await session.scalar(
        select(Review).where(
            Review.candidate_id == candidate.id, Review.is_current.is_(True)
        )
    )
    review_summary = None
    if current_review is not None:
        reviewer = await session.get(User, current_review.reviewer_id)
        if reviewer is None:
            raise RuntimeError("reviewer is missing")
        review_summary = AdminReviewSummary(
            id=current_review.id,
            decision=current_review.decision,
            rejection_reason=current_review.rejection_reason,
            notes=current_review.notes,
            is_current=current_review.is_current,
            version=current_review.version,
            reviewer=_user_summary(reviewer),
        )
    return CandidateAdminResponse(
        id=candidate.id,
        species=_species_summary(species),
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
        active=candidate.active,
        version=candidate.version,
        current_started_at=candidate.current_started_at,
        current_reviewer=(
            _user_summary(current_reviewer) if current_reviewer is not None else None
        ),
        current_review=review_summary,
    )


async def list_species(
    session: AsyncSession, filters: SpeciesFilters
) -> SpeciesListResponse:
    conditions = []
    if filters.active is not None:
        conditions.append(Species.active.is_(filters.active))
    if filters.search is not None:
        pattern = f"%{filters.search}%"
        conditions.append(
            or_(
                Species.code.ilike(pattern),
                Species.name_zh.ilike(pattern),
                Species.name_en.ilike(pattern),
                Species.scientific_name.ilike(pattern),
            )
        )
    total = int(
        await session.scalar(
            select(func.count()).select_from(Species).where(*conditions)
        )
        or 0
    )
    counts = (
        select(Candidate.species_id, func.count(Candidate.id).label("candidate_count"))
        .group_by(Candidate.species_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(Species, func.coalesce(counts.c.candidate_count, 0))
            .outerjoin(counts, counts.c.species_id == Species.id)
            .where(*conditions)
            .order_by(Species.sort_order, Species.code, Species.id)
            .offset(filters.offset)
            .limit(filters.limit)
        )
    ).all()
    return SpeciesListResponse(
        total=total,
        items=[
            SpeciesResponse(
                **species_snapshot(species), candidate_count=int(candidate_count)
            )
            for species, candidate_count in rows
        ],
    )


async def create_species(
    session: AsyncSession, actor_id: UUID, payload: SpeciesCreateRequest
) -> SpeciesResponse:
    try:
        duplicate = await session.scalar(
            select(Species.id).where(Species.code == payload.code)
        )
        if duplicate is not None:
            raise AdminConflict("SPECIES_CODE_CONFLICT")
        species = Species(
            code=payload.code,
            name_zh=payload.name_zh,
            name_en=payload.name_en,
            scientific_name=payload.scientific_name,
            active=payload.active,
            sort_order=payload.sort_order,
        )
        session.add(species)
        await session.flush()
        after = species_snapshot(species)
        await audited_change(
            session,
            action="SPECIES_CREATE",
            actor_id=actor_id,
            object_type="Species",
            object_id=species.id,
            reason=payload.reason,
            before=None,
            after=after,
        )
        await session.commit()
        return SpeciesResponse(**after, candidate_count=0)
    except IntegrityError as exc:
        await session.rollback()
        raise AdminConflict("SPECIES_CODE_CONFLICT") from exc
    except BaseException:
        if session.in_transaction():
            await session.rollback()
        raise


async def patch_species(
    session: AsyncSession,
    actor_id: UUID,
    species_id: UUID,
    payload: SpeciesPatchRequest,
) -> SpeciesResponse:
    try:
        species = await session.scalar(
            select(Species)
            .where(Species.id == species_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if species is None:
            raise AdminObjectNotFound
        if payload.active is False and species.active:
            has_open = await session.scalar(
                select(
                    exists().where(
                        Candidate.species_id == species.id,
                        Candidate.current_reviewer_id.is_not(None),
                    )
                )
            )
            if has_open:
                raise AdminConflict("SPECIES_HAS_OPEN_CANDIDATE")
        before = species_snapshot(species)
        for field in ("name_zh", "name_en", "scientific_name", "active", "sort_order"):
            if field in payload.model_fields_set:
                setattr(species, field, getattr(payload, field))
        after = species_snapshot(species)
        await audited_change(
            session,
            action="SPECIES_UPDATE",
            actor_id=actor_id,
            object_type="Species",
            object_id=species.id,
            reason=payload.reason,
            before=before,
            after=after,
        )
        await session.commit()
        count = int(
            await session.scalar(
                select(func.count(Candidate.id)).where(
                    Candidate.species_id == species.id
                )
            )
            or 0
        )
        return SpeciesResponse(**after, candidate_count=count)
    except BaseException:
        if session.in_transaction():
            await session.rollback()
        raise


def _candidate_conditions(filters: CandidateFilters):
    current_review = exists().where(
        Review.candidate_id == Candidate.id, Review.is_current.is_(True)
    )
    conditions = []
    if filters.species_code is not None:
        conditions.append(Species.code == filters.species_code)
    if filters.source_dataset is not None:
        conditions.append(Candidate.source_dataset == filters.source_dataset)
    if filters.active is not None:
        conditions.append(Candidate.active.is_(filters.active))
    if filters.reviewed is not None:
        conditions.append(current_review if filters.reviewed else ~current_review)
    if filters.decision is not None:
        conditions.append(
            exists().where(
                Review.candidate_id == Candidate.id,
                Review.is_current.is_(True),
                Review.decision == filters.decision,
            )
        )
    if filters.current_reviewer_id is not None:
        conditions.append(Candidate.current_reviewer_id == filters.current_reviewer_id)
    if filters.search is not None:
        pattern = f"%{filters.search}%"
        conditions.append(
            or_(
                Candidate.source_dataset.ilike(pattern),
                Candidate.source_record_id.ilike(pattern),
                Candidate.creator.ilike(pattern),
                Candidate.attribution.ilike(pattern),
            )
        )
    return conditions


async def list_candidates(
    session: AsyncSession, filters: CandidateFilters
) -> CandidateListResponse:
    conditions = _candidate_conditions(filters)
    total = int(
        await session.scalar(
            select(func.count(Candidate.id))
            .join(Species, Species.id == Candidate.species_id)
            .where(*conditions)
        )
        or 0
    )
    candidates = list(
        (
            await session.scalars(
                select(Candidate)
                .join(Species, Species.id == Candidate.species_id)
                .where(*conditions)
                .order_by(Candidate.created_at.desc(), Candidate.id.desc())
                .offset(filters.offset)
                .limit(filters.limit)
            )
        ).all()
    )
    return CandidateListResponse(
        total=total,
        items=[
            await _candidate_response(session, candidate) for candidate in candidates
        ],
    )


async def _lock_target(session: AsyncSession, target_id: UUID | None) -> User | None:
    if target_id is None:
        return None
    return await session.scalar(
        select(User)
        .where(User.id == target_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def _ensure_target_eligible(
    session: AsyncSession,
    target: User | None,
    candidate_id: UUID,
    *,
    current_reviewer_id: UUID | None = None,
) -> User:
    fixed_names = {name for name, _ in FIXED_USERS}
    if (
        target is None
        or not target.active
        or target.name not in fixed_names
        or target.id == current_reviewer_id
    ):
        raise AdminConflict("REVIEWER_NOT_ELIGIBLE")
    busy = await session.scalar(
        select(exists().where(Candidate.current_reviewer_id == target.id))
    )
    prior = await session.scalar(
        select(
            exists().where(
                Review.candidate_id == candidate_id,
                Review.reviewer_id == target.id,
            )
        )
    )
    if busy or prior:
        raise AdminConflict("REVIEWER_NOT_ELIGIBLE")
    return target


async def patch_candidate(
    session: AsyncSession,
    actor_id: UUID,
    candidate_id: UUID,
    payload: CandidatePatchRequest,
) -> CandidateAdminResponse:
    try:
        target = await _lock_target(session, payload.new_reviewer_id)
        candidate = await session.scalar(
            select(Candidate)
            .where(Candidate.id == candidate_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if candidate is None:
            raise AdminObjectNotFound
        if candidate.version != payload.version:
            latest = (await _candidate_response(session, candidate)).model_dump(
                mode="json"
            )
            raise AdminConflict("STALE_CANDIDATE_VERSION", latest)
        if candidate.current_reviewer_id is not None:
            raise AdminConflict("CANDIDATE_CURRENTLY_OPEN")

        before_candidate = candidate_snapshot(candidate)
        species_changed = (
            "species_id" in payload.model_fields_set
            and payload.species_id != candidate.species_id
        )
        has_invalidation_controls = (
            payload.confirm_review_invalidation or payload.new_reviewer_id is not None
        )
        if has_invalidation_controls and not species_changed:
            raise AdminConflict("REVIEW_INVALIDATION_REQUIRES_SPECIES_CHANGE")

        review_before = None
        review_after = None
        if species_changed:
            species = await lock_species_for_assignment(session, payload.species_id)
            if species is None:
                raise AdminObjectNotFound
            if not species.active:
                raise AdminConflict("SPECIES_NOT_ACTIVE")
            current_review = await session.scalar(
                select(Review)
                .where(
                    Review.candidate_id == candidate.id,
                    Review.is_current.is_(True),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if current_review is not None:
                if not candidate.active:
                    raise AdminConflict("CANDIDATE_NOT_ACTIVE")
                if not payload.confirm_review_invalidation:
                    raise AdminConflict("REVIEW_INVALIDATION_CONFIRMATION_REQUIRED")
                target = await _ensure_target_eligible(session, target, candidate.id)
                review_before = review_snapshot(current_review)
                current_review.is_current = False
                current_review.version += 1
                review_after = review_snapshot(current_review)
                session.add(
                    ReviewRevision(
                        candidate_id=current_review.candidate_id,
                        review_id=current_review.id,
                        reviewer_id=current_review.reviewer_id,
                        actor_id=actor_id,
                        decision=current_review.decision,
                        rejection_reason=current_review.rejection_reason,
                        notes=current_review.notes,
                        whole_fish=current_review.whole_fish,
                        exact_species_verified=current_review.exact_species_verified,
                        is_current=current_review.is_current,
                        review_version=current_review.version,
                        snapshot_json={
                            "before": review_before,
                            "after": review_after,
                        },
                        reason=payload.reason,
                    )
                )
                candidate.current_reviewer_id = target.id
                candidate.current_started_at = utc_now()
            elif has_invalidation_controls:
                raise AdminConflict("REVIEW_INVALIDATION_NOT_APPLICABLE")

        field_map = {
            "species_id": "species_id",
            "source_dataset": "source_dataset",
            "source_record_id": "source_record_id",
            "preview_url": "preview_url",
            "original_url": "original_url",
            "source_url": "source_url",
            "creator": "creator",
            "license": "license",
            "license_url": "license_url",
            "attribution": "attribution",
            "location": "location",
            "observed_on": "observed_on",
            "metadata": "metadata_json",
            "active": "active",
        }
        for payload_field, model_field in field_map.items():
            if payload_field in payload.model_fields_set:
                setattr(candidate, model_field, getattr(payload, payload_field))
        changed_candidate = candidate_snapshot(candidate)
        if changed_candidate == before_candidate:
            raise AdminConflict("CANDIDATE_NO_CHANGES")
        candidate.version += 1
        after_candidate = candidate_snapshot(candidate)
        if review_before is None:
            audit_before = before_candidate
            audit_after = after_candidate
        else:
            audit_before = {
                "candidate": before_candidate,
                "invalidated_review": review_before,
            }
            audit_after = {
                "candidate": after_candidate,
                "invalidated_review": review_after,
            }
        await audited_change(
            session,
            action="CANDIDATE_UPDATE",
            actor_id=actor_id,
            object_type="Candidate",
            object_id=candidate.id,
            reason=payload.reason,
            before=audit_before,
            after=audit_after,
        )
        duplicate = await session.scalar(
            select(Candidate.id).where(
                Candidate.source_dataset == candidate.source_dataset,
                Candidate.source_record_id == candidate.source_record_id,
                Candidate.id != candidate.id,
            )
        )
        if duplicate is not None:
            raise AdminConflict("CANDIDATE_SOURCE_CONFLICT")
        await session.commit()
        return await _candidate_response(session, candidate)
    except IntegrityError as exc:
        await session.rollback()
        raise AdminConflict("CANDIDATE_SOURCE_CONFLICT") from exc
    except BaseException:
        if session.in_transaction():
            await session.rollback()
        raise


def _review_conditions(filters: AdminReviewFilters):
    conditions = []
    if filters.reviewer_id is not None:
        conditions.append(Review.reviewer_id == filters.reviewer_id)
    if filters.species_code is not None:
        conditions.append(Species.code == filters.species_code)
    if filters.source_dataset is not None:
        conditions.append(Candidate.source_dataset == filters.source_dataset)
    if filters.decision is not None:
        conditions.append(Review.decision == filters.decision)
    if filters.current is not None:
        conditions.append(Review.is_current.is_(filters.current))
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


async def list_admin_reviews(
    session: AsyncSession, filters: AdminReviewFilters
) -> AdminReviewListResponse:
    conditions = _review_conditions(filters)
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
            select(Review, Candidate, Species, User)
            .join(Candidate, Candidate.id == Review.candidate_id)
            .join(Species, Species.id == Candidate.species_id)
            .join(User, User.id == Review.reviewer_id)
            .where(*conditions)
            .order_by(Review.created_at.desc(), Review.id.desc())
            .offset(filters.offset)
            .limit(filters.limit)
        )
    ).all()
    return AdminReviewListResponse(
        total=total,
        items=[
            AdminReviewItem(
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
                candidate=_candidate_summary(candidate),
                species=_species_summary(species),
                reviewer=_user_summary(reviewer),
            )
            for review, candidate, species, reviewer in rows
        ],
    )


async def edit_admin_review(
    session: AsyncSession,
    actor_id: UUID,
    review_id: UUID,
    payload: AdminReviewPatchRequest,
) -> ReviewResponse:
    try:
        review = await session.scalar(
            select(Review)
            .where(Review.id == review_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if review is None:
            raise AdminObjectNotFound
        if not review.is_current:
            raise AdminConflict("REVIEW_NOT_CURRENT")
        if review.version != payload.version:
            raise AdminConflict(
                "STALE_REVIEW_VERSION",
                ReviewResponse.from_review(review).model_dump(mode="json"),
            )
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
        after = review_snapshot(review)
        session.add(
            ReviewRevision(
                candidate_id=review.candidate_id,
                review_id=review.id,
                reviewer_id=review.reviewer_id,
                actor_id=actor_id,
                decision=review.decision,
                rejection_reason=review.rejection_reason,
                notes=review.notes,
                whole_fish=review.whole_fish,
                exact_species_verified=review.exact_species_verified,
                is_current=review.is_current,
                review_version=review.version,
                snapshot_json={"before": before, "after": after},
                reason=payload.reason,
            )
        )
        await audited_change(
            session,
            action="REVIEW_ADMIN_UPDATE",
            actor_id=actor_id,
            object_type="Review",
            object_id=review.id,
            reason=payload.reason,
            before=before,
            after=after,
        )
        await session.commit()
        return ReviewResponse.from_review(review)
    except BaseException:
        if session.in_transaction():
            await session.rollback()
        raise


async def list_current(
    session: AsyncSession, filters: CurrentFilters
) -> CurrentListResponse:
    reviewed = exists().where(
        Review.candidate_id == Candidate.id, Review.is_current.is_(True)
    )
    conditions = [Candidate.current_reviewer_id.is_not(None), ~reviewed]
    if filters.species_code is not None:
        conditions.append(Species.code == filters.species_code)
    if filters.source_dataset is not None:
        conditions.append(Candidate.source_dataset == filters.source_dataset)
    if filters.reviewer_id is not None:
        conditions.append(Candidate.current_reviewer_id == filters.reviewer_id)
    if filters.search is not None:
        pattern = f"%{filters.search}%"
        conditions.append(
            or_(
                Candidate.source_dataset.ilike(pattern),
                Candidate.source_record_id.ilike(pattern),
                User.name.ilike(pattern),
            )
        )
    total = int(
        await session.scalar(
            select(func.count(Candidate.id))
            .join(Species, Species.id == Candidate.species_id)
            .join(User, User.id == Candidate.current_reviewer_id)
            .where(*conditions)
        )
        or 0
    )
    rows = (
        await session.execute(
            select(Candidate, Species, User)
            .join(Species, Species.id == Candidate.species_id)
            .join(User, User.id == Candidate.current_reviewer_id)
            .where(*conditions)
            .order_by(Candidate.current_started_at, Candidate.id)
            .offset(filters.offset)
            .limit(filters.limit)
        )
    ).all()
    return CurrentListResponse(
        total=total,
        items=[
            CurrentItem(
                candidate=_candidate_summary(candidate),
                species=_species_summary(species),
                reviewer=_user_summary(reviewer),
                current_started_at=candidate.current_started_at,
            )
            for candidate, species, reviewer in rows
        ],
    )


async def _current_review(
    session: AsyncSession, candidate_id: UUID, *, lock: bool = False
) -> Review | None:
    statement = select(Review).where(
        Review.candidate_id == candidate_id, Review.is_current.is_(True)
    )
    if lock:
        statement = statement.with_for_update().execution_options(
            populate_existing=True
        )
    return await session.scalar(statement)


async def release_current(
    session: AsyncSession,
    actor_id: UUID,
    candidate_id: UUID,
    payload: CandidateVersionReason,
) -> CandidateAdminResponse:
    try:
        candidate = await session.scalar(
            select(Candidate)
            .where(Candidate.id == candidate_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if candidate is None:
            raise AdminObjectNotFound
        if candidate.version != payload.version:
            raise AdminConflict(
                "STALE_CANDIDATE_VERSION",
                (await _candidate_response(session, candidate)).model_dump(mode="json"),
            )
        if candidate.current_reviewer_id is None:
            raise AdminConflict("CANDIDATE_NOT_OPEN")
        if await _current_review(session, candidate.id, lock=True) is not None:
            raise AdminConflict("CANDIDATE_ALREADY_REVIEWED")
        before = candidate_snapshot(candidate)
        candidate.current_reviewer_id = None
        candidate.current_started_at = None
        candidate.version += 1
        after = candidate_snapshot(candidate)
        await audited_change(
            session,
            action="CURRENT_RELEASE",
            actor_id=actor_id,
            object_type="Candidate",
            object_id=candidate.id,
            reason=payload.reason,
            before=before,
            after=after,
        )
        await session.commit()
        return await _candidate_response(session, candidate)
    except BaseException:
        if session.in_transaction():
            await session.rollback()
        raise


async def transfer_current(
    session: AsyncSession,
    actor_id: UUID,
    candidate_id: UUID,
    payload: TransferRequest,
) -> CandidateAdminResponse:
    try:
        target = await _lock_target(session, payload.new_reviewer_id)
        candidate = await session.scalar(
            select(Candidate)
            .where(Candidate.id == candidate_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if candidate is None:
            raise AdminObjectNotFound
        if candidate.version != payload.version:
            raise AdminConflict(
                "STALE_CANDIDATE_VERSION",
                (await _candidate_response(session, candidate)).model_dump(mode="json"),
            )
        if candidate.current_reviewer_id is None:
            raise AdminConflict("CANDIDATE_NOT_OPEN")
        species = await lock_species_for_assignment(session, candidate.species_id)
        if species is None:
            raise AdminObjectNotFound
        if not candidate.active or not species.active:
            raise AdminConflict("SPECIES_NOT_ACTIVE")
        if await _current_review(session, candidate.id, lock=True) is not None:
            raise AdminConflict("CANDIDATE_ALREADY_REVIEWED")
        target = await _ensure_target_eligible(
            session,
            target,
            candidate.id,
            current_reviewer_id=candidate.current_reviewer_id,
        )
        before = candidate_snapshot(candidate)
        candidate.current_reviewer_id = target.id
        candidate.current_started_at = utc_now()
        candidate.version += 1
        after = candidate_snapshot(candidate)
        await audited_change(
            session,
            action="CURRENT_TRANSFER",
            actor_id=actor_id,
            object_type="Candidate",
            object_id=candidate.id,
            reason=payload.reason,
            before=before,
            after=after,
        )
        await session.commit()
        return await _candidate_response(session, candidate)
    except IntegrityError as exc:
        await session.rollback()
        raise AdminConflict("REVIEWER_NOT_ELIGIBLE") from exc
    except BaseException:
        if session.in_transaction():
            await session.rollback()
        raise


async def reopen_review(
    session: AsyncSession,
    actor_id: UUID,
    review_id: UUID,
    payload: ReopenRequest,
) -> CandidateAdminResponse:
    try:
        target = await _lock_target(session, payload.new_reviewer_id)
        candidate_id = await session.scalar(
            select(Review.candidate_id).where(Review.id == review_id)
        )
        if candidate_id is None:
            raise AdminObjectNotFound
        candidate = await session.scalar(
            select(Candidate)
            .where(Candidate.id == candidate_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if candidate is None:
            raise AdminObjectNotFound
        if candidate.version != payload.candidate_version:
            raise AdminConflict(
                "STALE_CANDIDATE_VERSION",
                (await _candidate_response(session, candidate)).model_dump(mode="json"),
            )
        if candidate.current_reviewer_id is not None:
            raise AdminConflict("CANDIDATE_CURRENTLY_OPEN")
        species = await lock_species_for_assignment(session, candidate.species_id)
        if species is None:
            raise AdminObjectNotFound
        if not candidate.active or not species.active:
            raise AdminConflict("SPECIES_NOT_ACTIVE")
        review = await session.scalar(
            select(Review)
            .where(Review.id == review_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if review is None:
            raise AdminObjectNotFound
        if not review.is_current:
            raise AdminConflict("REVIEW_NOT_CURRENT")
        if review.version != payload.review_version:
            raise AdminConflict(
                "STALE_REVIEW_VERSION",
                ReviewResponse.from_review(review).model_dump(mode="json"),
            )
        target = await _ensure_target_eligible(session, target, candidate.id)
        candidate_before = candidate_snapshot(candidate)
        review_before = review_snapshot(review)
        review.is_current = False
        review.version += 1
        review_after = review_snapshot(review)
        session.add(
            ReviewRevision(
                candidate_id=review.candidate_id,
                review_id=review.id,
                reviewer_id=review.reviewer_id,
                actor_id=actor_id,
                decision=review.decision,
                rejection_reason=review.rejection_reason,
                notes=review.notes,
                whole_fish=review.whole_fish,
                exact_species_verified=review.exact_species_verified,
                is_current=review.is_current,
                review_version=review.version,
                snapshot_json={"before": review_before, "after": review_after},
                reason=payload.reason,
            )
        )
        candidate.current_reviewer_id = target.id
        candidate.current_started_at = utc_now()
        candidate.version += 1
        candidate_after = candidate_snapshot(candidate)
        await audited_change(
            session,
            action="REVIEW_REOPEN",
            actor_id=actor_id,
            object_type="Review",
            object_id=review.id,
            reason=payload.reason,
            before={"candidate": candidate_before, "review": review_before},
            after={"candidate": candidate_after, "review": review_after},
        )
        await session.commit()
        return await _candidate_response(session, candidate)
    except IntegrityError as exc:
        await session.rollback()
        raise AdminConflict("REVIEWER_NOT_ELIGIBLE") from exc
    except BaseException:
        if session.in_transaction():
            await session.rollback()
        raise


async def reset_password_transaction(
    session: AsyncSession,
    *,
    actor_id: UUID | None,
    reason: str,
    allow_admin: bool,
    target_user_id: UUID | None = None,
    target_name: str | None = None,
) -> str:
    if (target_user_id is None) == (target_name is None):
        raise ValueError("exactly one password reset target is required")
    try:
        condition = (
            User.id == target_user_id
            if target_user_id is not None
            else User.name == target_name
        )
        user = await session.scalar(
            select(User)
            .where(condition)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        fixed_names = {name for name, _ in FIXED_USERS}
        if user is None or user.name not in fixed_names:
            raise AdminObjectNotFound
        if not allow_admin and user.role == "admin":
            raise AdminConflict("ADMIN_PASSWORD_RESET_REQUIRES_CLI")
        active_sessions = int(
            await session.scalar(
                select(func.count(Session.id)).where(
                    Session.user_id == user.id, Session.revoked_at.is_(None)
                )
            )
            or 0
        )
        temporary_password = generate_temporary_password()
        user.password_hash = hash_password(temporary_password)
        user.password_version += 1
        user.must_change_password = True
        user.failed_login_count = 0
        user.locked_until = None
        now = utc_now()
        await session.execute(
            update(Session)
            .where(Session.user_id == user.id, Session.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        action = (
            "USER_PASSWORD_RESET_SYSTEM" if actor_id is None else "USER_PASSWORD_RESET"
        )
        await audited_change(
            session,
            action=action,
            actor_id=actor_id,
            object_type="User",
            object_id=user.id,
            reason=reason,
            before={
                "user_id": str(user.id),
                "name": user.name,
                "active_sessions": active_sessions,
            },
            after={
                "user_id": str(user.id),
                "name": user.name,
                "active_sessions": 0,
                "sessions_revoked": active_sessions,
                "requires_credential_change": True,
            },
        )
        await session.commit()
        return temporary_password
    except BaseException:
        if session.in_transaction():
            await session.rollback()
        raise
