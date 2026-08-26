from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ExportCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    species_code: str | None = Field(default=None, min_length=1, max_length=32)

    @field_validator("species_code")
    @classmethod
    def normalize_species(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class ExportBatchResponse(BaseModel):
    id: UUID
    species_code: str | None
    status: Literal["pending", "completed", "expired"]
    created_at: datetime
    expires_at: datetime
    completed_at: datetime | None
    expired_at: datetime | None
    item_count: int
    pending_count: int
    created: bool = False


class ExportBatchListResponse(BaseModel):
    total: int
    items: list[ExportBatchResponse]


class ReceiptItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: UUID
    review_id: UUID
    review_version: int = Field(ge=1)
    status: Literal["SUCCEEDED", "FAILED"]
    sha256: str | None = Field(default=None, min_length=64, max_length=64)
    relative_path: str | None = Field(default=None, min_length=1, max_length=1024)
    error: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_result_fields(self) -> "ReceiptItem":
        if self.status == "SUCCEEDED":
            if self.sha256 is None or self.relative_path is None or self.error is not None:
                raise ValueError("successful items require sha256 and relative_path only")
        elif self.error is None or self.sha256 is not None or self.relative_path is not None:
            raise ValueError("failed items require error only")
        return self


class ReceiptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ReceiptItem] = Field(min_length=1, max_length=10_000)


class ReceiptFileRequest(ReceiptRequest):
    batch_id: UUID


class ReceiptResponse(BaseModel):
    batch_id: UUID
    status: Literal["pending", "completed"]
    accepted_candidate_ids: list[UUID]
    pending_candidate_ids: list[UUID]
