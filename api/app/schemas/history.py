from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models import Decision
from app.schemas.review import DecisionRequest, SpeciesSummary


class HistoryFilters(BaseModel):
    species_code: str | None = Field(default=None, min_length=1, max_length=32)
    source_dataset: str | None = Field(default=None, min_length=1, max_length=128)
    decision: Decision | None = None
    date_from: date | None = None
    date_to: date | None = None
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    reviewer: str | None = None

class HistoryItem(BaseModel):
    id: UUID
    candidate_id: UUID
    reviewer_id: UUID
    decision: Decision
    rejection_reason: str | None
    notes: str | None
    whole_fish: str
    exact_species_verified: str
    is_current: bool
    read_only: bool
    version: int
    created_at: datetime
    updated_at: datetime
    species: SpeciesSummary
    source_dataset: str
    source_record_id: str
    preview_url: str
    original_url: str
    source_url: str


class HistoryResponse(BaseModel):
    total: int
    items: list[HistoryItem]


class HistoryEditRequest(DecisionRequest):
    version: int = Field(ge=1)
