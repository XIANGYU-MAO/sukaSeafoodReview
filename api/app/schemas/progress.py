from pydantic import BaseModel, Field


class DecisionCounts(BaseModel):
    APPROVED: int = 0
    REJECTED: int = 0
    UNSURE: int = 0


class MemberProgress(BaseModel):
    name: str
    completed: int = 0
    approved: int = 0
    rejected: int = 0
    unsure: int = 0
    today: int = 0


class ProgressResponse(BaseModel):
    total: int
    reviewed: int
    pending: int
    currently_open: int
    completion_percent: float = Field(
        description="Reviewed / total as a percentage, rounded to two decimals."
    )
    decision_counts: DecisionCounts
    today_count: int
    members: list[MemberProgress]
