from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Candidate,
    Decision,
    IdempotencyCommand,
    Review,
    ReviewRevision,
    User,
)
from app.schemas.review import DecisionRequest, RejectionReason, ReviewResponse


class IdempotencyConflict(Exception):
    pass


class ReviewAssignmentConflict(Exception):
    pass


@dataclass(frozen=True)
class SubmissionResult:
    review: Review
    response_status: int
    response_json: dict[str, Any]


def canonical_facts(payload: DecisionRequest) -> tuple[str, str]:
    if payload.decision == Decision.APPROVED:
        return "YES", "YES"
    if payload.decision == Decision.UNSURE:
        return "REVIEW", "REVIEW"
    if payload.rejection_reason == RejectionReason.WRONG_SPECIES:
        return "REVIEW", "NO"
    if payload.rejection_reason == RejectionReason.NOT_WHOLE_FISH:
        return "NO", "REVIEW"
    return "REVIEW", "REVIEW"


def request_digest(
    user_id: UUID,
    candidate_id: UUID,
    payload: DecisionRequest,
) -> str:
    canonical = {
        "user_id": str(user_id),
        "candidate_id": str(candidate_id),
        "payload": payload.model_dump(mode="json"),
    }
    encoded = json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def review_snapshot(review: Review) -> dict[str, object]:
    decision = (
        review.decision.value
        if isinstance(review.decision, Decision)
        else str(review.decision)
    )
    return {
        "candidate_id": str(review.candidate_id),
        "review_id": str(review.id),
        "reviewer_id": str(review.reviewer_id),
        "decision": decision,
        "rejection_reason": review.rejection_reason,
        "notes": review.notes,
        "whole_fish": review.whole_fish,
        "exact_species_verified": review.exact_species_verified,
        "is_current": review.is_current,
        "version": review.version,
    }


async def submit_decision(
    session: AsyncSession,
    user_id: UUID,
    candidate_id: UUID,
    command_id: str,
    payload: DecisionRequest,
) -> SubmissionResult:
    normalized_command = command_id.strip()
    if not normalized_command or len(normalized_command) > 255:
        raise ValueError("invalid idempotency key")
    digest = request_digest(user_id, candidate_id, payload)

    try:
        user = await session.scalar(
            select(User).where(User.id == user_id).with_for_update()
        )
        if user is None or not user.active:
            await session.rollback()
            raise ReviewAssignmentConflict

        command = await session.scalar(
            select(IdempotencyCommand).where(
                IdempotencyCommand.user_id == user_id,
                IdempotencyCommand.command_key == normalized_command,
            )
        )
        if command is not None:
            if command.request_hash != digest:
                await session.rollback()
                raise IdempotencyConflict
            review = await session.get(
                Review, UUID(str(command.response_json["id"]))
            )
            if review is None:
                await session.rollback()
                raise RuntimeError("persisted idempotency response is invalid")
            await session.commit()
            return SubmissionResult(
                review=review,
                response_status=command.response_status,
                response_json=dict(command.response_json),
            )

        candidate = await session.scalar(
            select(Candidate)
            .where(Candidate.id == candidate_id)
            .with_for_update()
        )
        if candidate is None or candidate.current_reviewer_id != user_id:
            await session.rollback()
            raise ReviewAssignmentConflict

        whole_fish, exact_species = canonical_facts(payload)
        review = Review(
            candidate_id=candidate.id,
            reviewer_id=user_id,
            decision=payload.decision,
            rejection_reason=(
                payload.rejection_reason.value
                if payload.rejection_reason is not None
                else None
            ),
            notes=payload.notes,
            whole_fish=whole_fish,
            exact_species_verified=exact_species,
            is_current=True,
            version=1,
        )
        session.add(review)
        await session.flush()
        snapshot = review_snapshot(review)
        session.add(
            ReviewRevision(
                candidate_id=candidate.id,
                review_id=review.id,
                reviewer_id=user_id,
                actor_id=user_id,
                decision=review.decision,
                rejection_reason=review.rejection_reason,
                notes=review.notes,
                whole_fish=review.whole_fish,
                exact_species_verified=review.exact_species_verified,
                is_current=True,
                review_version=review.version,
                snapshot_json=snapshot,
            )
        )
        candidate.current_reviewer_id = None
        candidate.current_started_at = None
        candidate.version += 1
        response_json = ReviewResponse.from_review(review).model_dump(mode="json")
        session.add(
            IdempotencyCommand(
                user_id=user_id,
                command_key=normalized_command,
                request_hash=digest,
                response_status=201,
                response_json=response_json,
            )
        )
        await session.commit()
        return SubmissionResult(
            review=review,
            response_status=201,
            response_json=response_json,
        )
    except BaseException:
        if session.in_transaction():
            await session.rollback()
        raise
