from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models import Decision, Review


class RejectionReason(StrEnum):
    WRONG_SPECIES = "WRONG_SPECIES"
    NOT_WHOLE_FISH = "NOT_WHOLE_FISH"
    COOKED_OR_PROCESSED = "COOKED_OR_PROCESSED"
    TOO_OCCLUDED = "TOO_OCCLUDED"
    TOO_SMALL_OR_BLURRY = "TOO_SMALL_OR_BLURRY"
    DUPLICATE = "DUPLICATE"
    ARTWORK_OR_DIAGRAM = "ARTWORK_OR_DIAGRAM"
    LICENSE_OR_SOURCE_CONCERN = "LICENSE_OR_SOURCE_CONCERN"
    IMAGE_URL_UNAVAILABLE = "IMAGE_URL_UNAVAILABLE"
    OTHER = "OTHER"


class ReviewFilters(BaseModel):
    species_code: str | None = Field(default=None, min_length=1, max_length=32)
    source_dataset: str | None = Field(
        default=None, min_length=1, max_length=128
    )


class DecisionRequest(BaseModel):
    decision: Decision
    rejection_reason: RejectionReason | None = None
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_decision(self) -> "DecisionRequest":
        if self.notes is not None:
            self.notes = self.notes.strip() or None
        if self.decision in {Decision.APPROVED, Decision.UNSURE}:
            if self.rejection_reason is not None:
                raise ValueError("rejection_reason is only valid for REJECTED")
        elif self.rejection_reason is None:
            raise ValueError("rejection_reason is required for REJECTED")
        if self.rejection_reason == RejectionReason.OTHER and not self.notes:
            raise ValueError("notes are required for OTHER")
        return self


class SpeciesSummary(BaseModel):
    code: str
    name_zh: str
    name_en: str
    scientific_name: str


class CandidateResponse(BaseModel):
    id: UUID
    species: SpeciesSummary
    source_dataset: str
    source_record_id: str
    preview_url: str
    original_url: str
    source_url: str
    creator: str | None
    license: str
    license_url: str | None
    attribution: str
    location: str | None
    observed_on: date | None
    metadata: dict[str, Any]


class ReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    candidate_id: UUID
    reviewer_id: UUID
    decision: Decision
    rejection_reason: str | None
    notes: str | None
    whole_fish: str
    exact_species_verified: str
    is_current: bool
    version: int

    @classmethod
    def from_review(cls, review: Review) -> "ReviewResponse":
        return cls.model_validate(review)
