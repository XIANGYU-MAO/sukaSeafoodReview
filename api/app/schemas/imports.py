from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ImportIssue(BaseModel):
    row: int | None = None
    code: str
    message: str
    blocking: bool = True


class NormalizedCandidate(BaseModel):
    species_code: str
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
    metadata_json: dict[str, Any]
    active: bool = True
    version: int = 1
    current_reviewer_id: UUID | None = None
    current_started_at: datetime | None = None
    normalization_warnings: list[str] = Field(default_factory=list, exclude=True)
    source_row: int | None = Field(default=None, exclude=True)


class ImportPreview(BaseModel):
    total: int = 0
    new_rows: int = 0
    exact_duplicates: int = 0
    possible_url_duplicates: int = 0
    invalid_species: int = 0
    missing_urls: int = 0
    invalid_licenses: int = 0
    invalid_sources: int = 0
    conflicting_identities: int = 0
    parse_errors: int = 0
    warnings: int = 0
    source_counts: dict[str, int] = Field(default_factory=dict)
    species_counts: dict[str, int] = Field(default_factory=dict)
    blocking_errors: int = 0
    can_commit: bool = False
    file_sha256: str
    issues: list[ImportIssue] = Field(default_factory=list)
    issues_truncated: bool = False
    omitted_issue_details: int = 0
    preview_token: str | None = None
    normalized_rows: list[NormalizedCandidate] = Field(
        default_factory=list, exclude=True
    )
    new_normalized_rows: list[NormalizedCandidate] = Field(
        default_factory=list, exclude=True
    )
    database_fingerprint: str = Field(default="", exclude=True)
    fatal_file_code: str | None = Field(default=None, exclude=True)


class ImportCommitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_token: str = Field(min_length=32, max_length=512)


class ImportResult(BaseModel):
    total: int
    inserted: int
    skipped_exact: int
    possible_url_duplicates: int
    file_sha256: str
