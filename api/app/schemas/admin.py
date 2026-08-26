from __future__ import annotations

from datetime import date, datetime
import json
import re
from typing import Annotated, Any
from uuid import UUID

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from app.models import Decision
from app.image_origins import ImageOriginError, require_public_image_url
from app.schemas.filters import MAX_FILTER_DATE
from app.schemas.review import DecisionRequest
from app.species_codes import require_safe_species_code


TrimmedReason = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
]
TrimmedCode = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)
]
TrimmedName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]


def _validate_https(value: str | None) -> str | None:
    if value is None:
        return None
    if value != value.strip() or any(character.isspace() for character in value):
        raise ValueError("URL must be an absolute HTTPS URL without credentials")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise ValueError("URL must not contain control characters")
    if re.match(r"^https://[^/?#]+", value, flags=re.IGNORECASE) is None:
        raise ValueError("URL must contain an explicit HTTPS authority")
    try:
        parsed = TypeAdapter(AnyHttpUrl).validate_python(value)
    except ValidationError as exc:
        raise ValueError("URL must be a valid absolute HTTPS URL") from exc
    if (
        parsed.scheme != "https"
        or parsed.host is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("URL must be an absolute HTTPS URL without credentials")
    return str(parsed)


class SpeciesFilters(BaseModel):
    search: str | None = Field(default=None, min_length=1, max_length=255)
    active: bool | None = None
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)

    @field_validator("search")
    @classmethod
    def trim_search(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class SpeciesResponse(BaseModel):
    id: UUID
    code: str
    name_zh: str
    name_en: str
    scientific_name: str
    active: bool
    sort_order: int
    candidate_count: int


class SpeciesListResponse(BaseModel):
    total: int
    items: list[SpeciesResponse]


class SpeciesCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: TrimmedCode
    name_zh: TrimmedName
    name_en: TrimmedName
    scientific_name: TrimmedName
    active: bool = True
    sort_order: int = 0
    reason: TrimmedReason

    @field_validator("code")
    @classmethod
    def safe_code(cls, value: str) -> str:
        return require_safe_species_code(value)


class SpeciesPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name_zh: TrimmedName | None = None
    name_en: TrimmedName | None = None
    scientific_name: TrimmedName | None = None
    active: bool | None = None
    sort_order: int | None = None
    reason: TrimmedReason

    @model_validator(mode="after")
    def require_change(self) -> "SpeciesPatchRequest":
        if not self.model_fields_set.difference({"reason"}):
            raise ValueError("at least one species field is required")
        if any(
            field in self.model_fields_set and getattr(self, field) is None
            for field in (
                "name_zh",
                "name_en",
                "scientific_name",
                "active",
                "sort_order",
            )
        ):
            raise ValueError("species fields cannot be cleared")
        return self


class AdminUserSummary(BaseModel):
    id: UUID
    display_name: str
    active: bool


class AdminUserDirectoryItem(AdminUserSummary):
    role: str


class AdminUserListResponse(BaseModel):
    total: int
    items: list[AdminUserDirectoryItem]


class AdminSourceListResponse(BaseModel):
    sources: list[Annotated[str, StringConstraints(min_length=1, max_length=128)]] = Field(
        max_length=1000
    )


class AdminSpeciesSummary(BaseModel):
    id: UUID
    code: str
    name_zh: str
    name_en: str
    scientific_name: str
    active: bool


class AdminReviewSummary(BaseModel):
    id: UUID
    decision: Decision
    rejection_reason: str | None
    notes: str | None
    is_current: bool
    version: int
    reviewer: AdminUserSummary


class CandidateFilters(BaseModel):
    species_code: str | None = Field(default=None, min_length=1, max_length=32)
    source_dataset: str | None = Field(default=None, min_length=1, max_length=128)
    active: bool | None = None
    reviewed: bool | None = None
    decision: Decision | None = None
    current_reviewer_id: UUID | None = None
    search: str | None = Field(default=None, min_length=1, max_length=255)
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)

    @field_validator("species_code", "source_dataset", "search")
    @classmethod
    def trim_filters(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class CandidateAdminResponse(BaseModel):
    id: UUID
    species: AdminSpeciesSummary
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
    active: bool
    version: int
    current_started_at: datetime | None
    current_reviewer: AdminUserSummary | None
    current_review: AdminReviewSummary | None


class CandidateListResponse(BaseModel):
    total: int
    items: list[CandidateAdminResponse]


class CandidatePatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    species_id: UUID | None = None
    source_dataset: (
        Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
        ]
        | None
    ) = None
    source_record_id: (
        Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
        ]
        | None
    ) = None
    preview_url: str | None = Field(default=None, max_length=2048)
    original_url: str | None = Field(default=None, max_length=2048)
    source_url: str | None = Field(default=None, max_length=2048)
    creator: str | None = Field(default=None, max_length=512)
    license: str | None = Field(default=None, min_length=1, max_length=255)
    license_url: str | None = Field(default=None, max_length=2048)
    attribution: str | None = Field(default=None, min_length=1, max_length=1024)
    location: str | None = Field(default=None, max_length=512)
    observed_on: date | None = None
    metadata: dict[str, Any] | None = None
    active: bool | None = None
    confirm_review_invalidation: bool = False
    new_reviewer_id: UUID | None = None
    reason: TrimmedReason

    @field_validator("preview_url", "original_url")
    @classmethod
    def public_image_urls(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = _validate_https(value)
        assert normalized is not None
        try:
            return require_public_image_url(normalized)
        except ImageOriginError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("source_url", "license_url")
    @classmethod
    def https_urls(cls, value: str | None) -> str | None:
        return _validate_https(value)

    @field_validator("creator", "license", "attribution", "location")
    @classmethod
    def trim_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def validate_patch(self) -> "CandidatePatchRequest":
        control = {
            "version",
            "reason",
            "confirm_review_invalidation",
            "new_reviewer_id",
        }
        if not self.model_fields_set.difference(control):
            raise ValueError("at least one candidate field is required")
        required_fields = {
            "species_id",
            "source_dataset",
            "source_record_id",
            "preview_url",
            "original_url",
            "source_url",
            "license",
            "attribution",
            "metadata",
        }
        if any(
            field in self.model_fields_set and getattr(self, field) is None
            for field in required_fields
        ):
            raise ValueError("required candidate fields cannot be cleared")
        if self.confirm_review_invalidation and self.new_reviewer_id is None:
            raise ValueError("new_reviewer_id is required when invalidating a review")
        if (
            self.confirm_review_invalidation
            and "species_id" not in self.model_fields_set
        ):
            raise ValueError("species_id is required when invalidating a review")
        if self.new_reviewer_id is not None and not self.confirm_review_invalidation:
            raise ValueError("new_reviewer_id is only valid for review invalidation")
        if self.metadata is not None:
            encoded = json.dumps(
                self.metadata,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(encoded) > 65_536:
                raise ValueError("metadata must not exceed 65536 bytes")
        return self


class AdminCandidateSummary(BaseModel):
    id: UUID
    source_dataset: str
    source_record_id: str
    preview_url: str
    original_url: str
    source_url: str
    active: bool
    version: int


class AdminReviewItem(BaseModel):
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
    candidate: AdminCandidateSummary
    species: AdminSpeciesSummary
    reviewer: AdminUserSummary


class AdminReviewFilters(BaseModel):
    reviewer_id: UUID | None = None
    species_code: str | None = Field(default=None, min_length=1, max_length=32)
    source_dataset: str | None = Field(default=None, min_length=1, max_length=128)
    decision: Decision | None = None
    current: bool | None = None
    date_from: date | None = None
    date_to: date | None = Field(default=None, le=MAX_FILTER_DATE)
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class AdminReviewListResponse(BaseModel):
    total: int
    items: list[AdminReviewItem]


class AdminReviewPatchRequest(DecisionRequest):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    reason: TrimmedReason


class CurrentFilters(BaseModel):
    species_code: str | None = Field(default=None, min_length=1, max_length=32)
    source_dataset: str | None = Field(default=None, min_length=1, max_length=128)
    reviewer_id: UUID | None = None
    search: str | None = Field(default=None, min_length=1, max_length=255)
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class CurrentItem(BaseModel):
    candidate: AdminCandidateSummary
    species: AdminSpeciesSummary
    reviewer: AdminUserSummary
    current_started_at: datetime


class CurrentListResponse(BaseModel):
    total: int
    items: list[CurrentItem]


class CandidateVersionReason(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    reason: TrimmedReason


class TransferRequest(CandidateVersionReason):
    new_reviewer_id: UUID


class ReopenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_version: int = Field(ge=1)
    review_version: int = Field(ge=1)
    new_reviewer_id: UUID
    reason: TrimmedReason


class ResetPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: TrimmedReason


class ResetPasswordResponse(BaseModel):
    temporary_password: str
