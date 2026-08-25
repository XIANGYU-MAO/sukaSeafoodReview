from __future__ import annotations

from datetime import timedelta

from sqlalchemy import case, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Candidate, Decision, Review, Species, User
from app.schemas.progress import (
    DecisionCounts,
    MemberProgress,
    ProgressResponse,
)
from app.services.auth import utc_now


MEMBER_NAMES = ("Hassan", "Mao", "Xinhui", "Wahid", "Sharmaa", "Yiming")


async def get_progress(session: AsyncSession) -> ProgressResponse:
    """Return current active-dataset totals and all-attempt member credit.

    The UTC day is half-open [00:00, next 00:00). Member totals intentionally
    include non-current attempts, so their sum may exceed unique reviewed items.
    Completion percentage is rounded to two decimal places.
    """
    now = utc_now()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    current_review_exists = exists(
        select(Review.id).where(
            Review.candidate_id == Candidate.id,
            Review.is_current.is_(True),
        )
    )
    overview = (
        await session.execute(
            select(
                func.count(Candidate.id),
                func.sum(case((current_review_exists, 1), else_=0)),
                func.sum(
                    case(
                        (
                            ~current_review_exists
                            & Candidate.current_reviewer_id.is_not(None),
                            1,
                        ),
                        else_=0,
                    )
                ),
            )
            .join(Species, Species.id == Candidate.species_id)
            .where(Candidate.active.is_(True), Species.active.is_(True))
        )
    ).one()
    total = int(overview[0] or 0)
    reviewed = int(overview[1] or 0)
    currently_open = int(overview[2] or 0)

    decision_rows = (
        await session.execute(
            select(Review.decision, func.count(Review.id))
            .join(Candidate, Candidate.id == Review.candidate_id)
            .join(Species, Species.id == Candidate.species_id)
            .where(
                Review.is_current.is_(True),
                Candidate.active.is_(True),
                Species.active.is_(True),
            )
            .group_by(Review.decision)
        )
    ).all()
    decisions = {decision.value: int(count) for decision, count in decision_rows}
    today_count = int(
        await session.scalar(
            select(func.count(Review.id))
            .join(Candidate, Candidate.id == Review.candidate_id)
            .join(Species, Species.id == Candidate.species_id)
            .where(
                Review.is_current.is_(True),
                Candidate.active.is_(True),
                Species.active.is_(True),
                Review.created_at >= day_start,
                Review.created_at < day_end,
            )
        )
        or 0
    )

    member_rows = (
        await session.execute(
            select(
                User.name,
                Review.decision,
                func.count(Review.id),
                func.sum(
                    case(
                        (
                            (Review.created_at >= day_start)
                            & (Review.created_at < day_end),
                            1,
                        ),
                        else_=0,
                    )
                ),
            )
            .join(Review, Review.reviewer_id == User.id)
            .where(User.name.in_(MEMBER_NAMES))
            .group_by(User.name, Review.decision)
        )
    ).all()
    member_values = {
        name: {
            "completed": 0,
            "approved": 0,
            "rejected": 0,
            "unsure": 0,
            "today": 0,
        }
        for name in MEMBER_NAMES
    }
    for name, decision, count, today in member_rows:
        values = member_values[name]
        values[decision.value.lower()] = int(count)
        values["completed"] += int(count)
        values["today"] += int(today or 0)

    return ProgressResponse(
        total=total,
        reviewed=reviewed,
        pending=max(0, total - reviewed - currently_open),
        currently_open=currently_open,
        completion_percent=round(reviewed / total * 100, 2) if total else 0.0,
        decision_counts=DecisionCounts(
            APPROVED=decisions.get(Decision.APPROVED.value, 0),
            REJECTED=decisions.get(Decision.REJECTED.value, 0),
            UNSURE=decisions.get(Decision.UNSURE.value, 0),
        ),
        today_count=today_count,
        members=[
            MemberProgress(name=name, **member_values[name]) for name in MEMBER_NAMES
        ],
    )
