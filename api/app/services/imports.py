from __future__ import annotations

import asyncio
import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
from ipaddress import ip_address
import io
import json
import re
import secrets
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AuditEvent,
    Candidate,
    CandidateImportPreview,
    Session,
    Species,
    User,
)
from app.schemas.imports import ImportIssue, ImportPreview, ImportResult, NormalizedCandidate
from app.services.auth import as_utc
from app.species_codes import is_safe_species_code


MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_ROWS = 50_000
MAX_ISSUE_DETAILS = 100
MAX_COUNT_KEYS = 1_000
PREVIEW_TTL = timedelta(minutes=15)
SUPPORTED_SOURCES = (
    "FISH_VISTA",
    "GBIF",
    "INATURALIST",
    "WIKIMEDIA_COMMONS",
)
REQUIRED_HEADERS = {
    "seafood_code",
    "source_dataset",
    "source_record_id",
    "source_url",
    "image_url",
    "license",
}
FIELD_LIMITS = {
    "seafood_code": 32,
    "source_dataset": 128,
    "source_record_id": 255,
    "source_url": 2048,
    "image_url": 2048,
    "creator": 512,
    "license": 255,
    "license_url": 2048,
    "attribution": 1024,
    "source_location": 512,
    "source_date": 512,
}
CANDIDATE_FINAL_LIMITS = {
    "source_dataset": 128,
    "source_record_id": 255,
    "preview_url": 2048,
    "original_url": 2048,
    "source_url": 2048,
    "creator": 512,
    "license": 255,
    "license_url": 2048,
    "attribution": 1024,
    "location": 512,
}
MAPPED_FIELDS = {
    "seafood_code",
    "source_dataset",
    "source_record_id",
    "source_url",
    "image_url",
    "creator",
    "license",
    "license_url",
    "attribution",
    "source_location",
    "source_date",
}
LOCAL_OR_REVIEW_FIELDS = {
    "local_path",
    "sha256",
    "perceptual_hash",
    "split",
    "status",
    "rejection_reason",
    "whole_fish",
    "exact_species_verified",
    "verified_by",
    "verification_notes",
}
MAPPED_FIELDS_CASEFOLD = {item.casefold() for item in MAPPED_FIELDS}
LOCAL_OR_REVIEW_FIELDS_CASEFOLD = {
    item.casefold() for item in LOCAL_OR_REVIEW_FIELDS
}
LICENSE_PATTERN = re.compile(
    r"^(?:CC-(?:BY|BY-NC|BY-NC-SA|BY-SA)|CC0|PUBLIC-DOMAIN)(?:[- ]\d+(?:\.\d+)?)?$",
    re.IGNORECASE,
)
INATURALIST_SIZE = re.compile(
    r"/(?:square|small|medium|large|original)\.([A-Za-z0-9]{1,10})$",
    re.IGNORECASE,
)
TRACKING_QUERY_NAMES = {"fbclid", "gclid"}


@dataclass(frozen=True)
class ImportConflict(Exception):
    code: str


@dataclass(frozen=True)
class ImportFileFatal(Exception):
    code: str
    status_code: int
    report: dict[str, Any]


@dataclass(frozen=True)
class RowProblem(ValueError):
    code: str
    category: str
    message: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _token_digest(value: str) -> str:
    return _digest(value.encode("utf-8"))


def _trim(value: str | None) -> str:
    return value.strip() if value is not None else ""


def _contains_control(value: str) -> bool:
    return any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)


def _normalize_identifier(value: str | None, field: str) -> str:
    raw = value or ""
    if _contains_control(raw):
        raise RowProblem(
            "INVALID_CONTROL_CHARACTER",
            "parse_errors",
            f"{field} contains a control character",
        )
    return raw.strip()


def _normalize_human_text(value: str | None, field: str) -> str:
    raw = value or ""
    raw = raw.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    if _contains_control(raw):
        raise RowProblem(
            "INVALID_CONTROL_CHARACTER",
            "parse_errors",
            f"{field} contains a control character",
        )
    return " ".join(raw.split())


def _bounded_issue(
    report: ImportPreview,
    *,
    row: int | None,
    code: str,
    message: str,
    blocking: bool = True,
) -> None:
    if len(report.issues) < MAX_ISSUE_DETAILS:
        report.issues.append(
            ImportIssue(row=row, code=code, message=message, blocking=blocking)
        )
        return
    report.issues_truncated = True
    report.omitted_issue_details += 1
    if blocking:
        for index in range(len(report.issues) - 1, -1, -1):
            if not report.issues[index].blocking:
                report.issues[index] = ImportIssue(
                    row=row, code=code, message=message, blocking=True
                )
                break


def _increment_count(counts: dict[str, int], key: str) -> None:
    if key in counts or len(counts) < MAX_COUNT_KEYS:
        counts[key] = counts.get(key, 0) + 1
    else:
        counts["__OTHER__"] = counts.get("__OTHER__", 0) + 1


def _normalize_url(value: str | None, *, optional: bool = False) -> str | None:
    if value is not None and _contains_control(value):
        raise RowProblem(
            "INVALID_CONTROL_CHARACTER",
            "missing_urls",
            "URL contains a control character",
        )
    if value is not None and any(character.isspace() for character in value):
        raise RowProblem("UNSAFE_URL", "missing_urls", "URL contains whitespace")
    raw = _trim(value)
    if not raw:
        if optional:
            return None
        raise RowProblem("MISSING_URL", "missing_urls", "A required URL is missing")
    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise RowProblem("UNSAFE_URL", "missing_urls", "URL must be absolute HTTP(S)")
    if parsed.username is not None or parsed.password is not None:
        raise RowProblem("UNSAFE_URL", "missing_urls", "URL credentials are forbidden")
    try:
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise RowProblem("UNSAFE_URL", "missing_urls", "URL host or port is malformed") from exc
    if (
        hostname is None
        or hostname.lower() == "localhost"
        or hostname.lower().endswith(".localhost")
    ):
        raise RowProblem("UNSAFE_URL", "missing_urls", "Local URL hosts are forbidden")
    try:
        address = ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise RowProblem("UNSAFE_URL", "missing_urls", "Non-public literal IPs are forbidden")
    if address is None:
        try:
            ascii_host = hostname.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise RowProblem("UNSAFE_URL", "missing_urls", "URL host is malformed") from exc
        labels = ascii_host.split(".")
        if (
            len(ascii_host) > 253
            or len(labels) < 2
            or any(
                not label
                or len(label) > 63
                or label.startswith("-")
                or label.endswith("-")
                or re.fullmatch(r"[a-z0-9-]+", label) is None
                for label in labels
            )
        ):
            raise RowProblem("UNSAFE_URL", "missing_urls", "URL host is malformed")
        hostname = ascii_host
    host = hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    if port is not None:
        host = f"{host}:{port}"
    return urlunsplit(("https", host, parsed.path or "/", parsed.query, parsed.fragment))


def _strip_commons_tracking(value: str) -> str:
    parsed = urlsplit(value)
    clean_query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_QUERY_NAMES
    ]
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(clean_query), parsed.fragment)
    )


def _inat_urls(value: str) -> tuple[str, str] | None:
    parsed = urlsplit(value)
    if "inaturalist" not in (parsed.hostname or "").lower():
        return None
    match = INATURALIST_SIZE.search(parsed.path)
    if match is None:
        return None
    extension = match.group(1).lower()
    prefix = parsed.path[: match.start()]
    preview = urlunsplit(
        (parsed.scheme, parsed.netloc, f"{prefix}/large.{extension}", parsed.query, "")
    )
    original = urlunsplit(
        (parsed.scheme, parsed.netloc, f"{prefix}/original.{extension}", parsed.query, "")
    )
    return preview, original


def _normalize_license(value: str | None) -> str:
    license_value = _normalize_identifier(value, "license").upper().replace("_", "-")
    if not license_value or LICENSE_PATTERN.fullmatch(license_value) is None:
        raise RowProblem(
            "INVALID_LICENSE", "invalid_licenses", "License is not redistributable"
        )
    return license_value.replace(" ", "-")


def _normalize_date(value: str | None) -> tuple[date | None, list[str]]:
    raw = _normalize_human_text(value, "source_date")
    if not raw:
        return None, []
    if re.match(r"^\d{4}-\d{2}-\d{2}(?:\D|$)", raw):
        try:
            return date.fromisoformat(raw[:10]), []
        except ValueError:
            pass
    dotted = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})(?:\D|$)", raw)
    if dotted:
        try:
            return date(int(dotted.group(3)), int(dotted.group(2)), int(dotted.group(1))), []
        except ValueError:
            pass
    named = re.search(r"\b(\d{1,2}) ([A-Za-z]+) (\d{4})\b", raw)
    if named:
        for pattern in ("%d %B %Y", "%d %b %Y"):
            try:
                return datetime.strptime(named.group(0), pattern).date(), []
            except ValueError:
                continue
    return None, ["UNPARSED_SOURCE_DATE"]


def _validate_final_candidate_values(values: dict[str, str | None]) -> None:
    for field, limit in CANDIDATE_FINAL_LIMITS.items():
        value = values[field]
        if value is not None and len(value) > limit:
            raise RowProblem(
                "FIELD_TOO_LONG", "parse_errors", f"{field} exceeds its database limit"
            )
        if value is not None and _contains_control(value):
            raise RowProblem(
                "INVALID_CONTROL_CHARACTER",
                "parse_errors",
                f"{field} contains a control character",
            )


def normalize_legacy_row(row: dict[str, str]) -> NormalizedCandidate:
    for name, limit in FIELD_LIMITS.items():
        if len(row.get(name, "")) > limit:
            raise RowProblem("FIELD_TOO_LONG", "parse_errors", f"{name} exceeds its limit")

    species_code = _normalize_identifier(row.get("seafood_code"), "seafood_code")
    if not is_safe_species_code(species_code):
        raise RowProblem(
            "INVALID_SPECIES",
            "invalid_species",
            "Species code is not a safe ASCII identifier",
        )
    source_dataset = _normalize_identifier(
        row.get("source_dataset"), "source_dataset"
    ).upper()
    if source_dataset not in SUPPORTED_SOURCES:
        raise RowProblem(
            "UNSUPPORTED_SOURCE", "invalid_sources", "Source dataset is unsupported"
        )
    source_record_id = _normalize_identifier(
        row.get("source_record_id"), "source_record_id"
    )
    if not source_record_id:
        raise RowProblem(
            "MISSING_SOURCE_IDENTITY", "parse_errors", "Source record ID is missing"
        )

    raw_source_url = _trim(row.get("source_url"))
    raw_image_url = _trim(row.get("image_url"))
    raw_license_url = _trim(row.get("license_url"))
    source_url = _normalize_url(raw_source_url)
    image_url = _normalize_url(raw_image_url)
    assert source_url is not None and image_url is not None
    license_url: str | None = None
    if raw_license_url.lower().startswith(("http://", "https://")):
        try:
            license_url = _normalize_url(raw_license_url, optional=True)
        except RowProblem as exc:
            raise RowProblem(
                "INVALID_LICENSE_URL",
                "invalid_licenses",
                "License URL is unsafe or malformed",
            ) from exc

    if source_dataset == "INATURALIST":
        mapped = _inat_urls(image_url)
        if mapped is None:
            raise RowProblem(
                "UNSAFE_URL", "missing_urls", "iNaturalist image URL has no known size"
            )
        preview_url, original_url = mapped
    elif source_dataset == "GBIF":
        mapped = _inat_urls(image_url)
        preview_url, original_url = mapped or (image_url, image_url)
    elif source_dataset == "WIKIMEDIA_COMMONS":
        preview_url = original_url = _strip_commons_tracking(image_url)
    else:
        preview_url = original_url = image_url

    creator = _normalize_human_text(row.get("creator"), "creator") or None
    license_value = _normalize_license(row.get("license"))
    attribution = _normalize_human_text(row.get("attribution"), "attribution") or creator
    if attribution is None:
        attribution = f"{source_dataset} {source_record_id}"
    location = _normalize_human_text(row.get("source_location"), "source_location") or None

    metadata: dict[str, Any] = {}
    for key, value in row.items():
        if _contains_control(key):
            raise RowProblem(
                "INVALID_CONTROL_CHARACTER",
                "parse_errors",
                "Metadata key contains a control character",
            )
        normalized_key = key.strip()
        lowered_key = normalized_key.casefold()
        unsafe_local_or_review_key = (
            "path" in lowered_key
            or "hash" in lowered_key
            or lowered_key.startswith("download")
            or lowered_key.startswith("review")
            or lowered_key.endswith("_review")
        )
        if (
            lowered_key not in MAPPED_FIELDS_CASEFOLD
            and lowered_key not in LOCAL_OR_REVIEW_FIELDS_CASEFOLD
            and not unsafe_local_or_review_key
            and value is not None
            and _normalize_human_text(value, normalized_key)
        ):
            metadata[normalized_key] = _normalize_human_text(value, normalized_key)
    raw_urls = {}
    if raw_source_url != source_url:
        raw_urls["source_url"] = raw_source_url
    if raw_image_url not in {preview_url, original_url}:
        raw_urls["image_url"] = raw_image_url
    if license_url is not None and raw_license_url != license_url:
        raw_urls["license_url"] = raw_license_url
    if raw_urls:
        metadata["raw_urls"] = raw_urls
    observed_on, warnings = _normalize_date(row.get("source_date"))
    if warnings:
        metadata["raw_source_date"] = _normalize_human_text(
            row.get("source_date"), "source_date"
        )

    _validate_final_candidate_values(
        {
            "source_dataset": source_dataset,
            "source_record_id": source_record_id,
            "preview_url": preview_url,
            "original_url": original_url,
            "source_url": source_url,
            "creator": creator,
            "license": license_value,
            "license_url": license_url,
            "attribution": attribution,
            "location": location,
        }
    )
    if len(json.dumps(metadata, ensure_ascii=False).encode("utf-8")) > 65_536:
        raise RowProblem("METADATA_TOO_LARGE", "parse_errors", "Metadata is too large")

    return NormalizedCandidate(
        species_code=species_code,
        source_dataset=source_dataset,
        source_record_id=source_record_id,
        preview_url=preview_url,
        original_url=original_url,
        source_url=source_url,
        creator=creator,
        license=license_value,
        license_url=license_url,
        attribution=attribution,
        location=location,
        observed_on=observed_on,
        metadata_json=metadata,
        normalization_warnings=warnings,
    )


def _file_error(content: bytes, code: str, message: str) -> ImportPreview:
    return ImportPreview(
        file_sha256=_digest(content),
        parse_errors=1,
        blocking_errors=1,
        can_commit=False,
        source_counts={source: 0 for source in SUPPORTED_SOURCES},
        issues=[ImportIssue(code=code, message=message)],
        fatal_file_code=code,
    )


def _parse_candidate_csv(content: bytes) -> ImportPreview:
    if len(content) > MAX_UPLOAD_BYTES and content.count(b"\n") <= MAX_ROWS:
        return _file_error(content, "CSV_TOO_LARGE", "CSV exceeds the upload limit")
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return _file_error(content, "CSV_INVALID_ENCODING", "CSV must be UTF-8")

    try:
        reader = csv.reader(io.StringIO(decoded, newline=""), strict=True)
        header = next(reader)
    except StopIteration:
        return _file_error(content, "CSV_MISSING_HEADERS", "CSV has no header")
    except csv.Error:
        return _file_error(content, "CSV_MALFORMED", "CSV syntax is malformed")
    if any(
        not name
        or name != name.strip()
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in name)
        for name in header
    ):
        return _file_error(content, "CSV_INVALID_HEADER", "CSV header is invalid")
    if len(header) != len(set(header)):
        return _file_error(content, "CSV_DUPLICATE_HEADERS", "CSV headers must be unique")
    if missing := sorted(REQUIRED_HEADERS.difference(header)):
        return _file_error(
            content,
            "CSV_MISSING_HEADERS",
            f"CSV is missing required headers: {', '.join(missing)}",
        )

    report = ImportPreview(
        file_sha256=_digest(content),
        source_counts={source: 0 for source in SUPPORTED_SOURCES},
    )
    try:
        for row_number, values in enumerate(reader, start=2):
            report.total += 1
            if report.total > MAX_ROWS:
                return _file_error(content, "CSV_TOO_MANY_ROWS", "CSV row limit exceeded")
            if len(values) != len(header):
                report.fatal_file_code = "CSV_MALFORMED"
                report.parse_errors += 1
                report.blocking_errors += 1
                _bounded_issue(
                    report,
                    row=row_number,
                    code="CSV_MALFORMED",
                    message="CSV row has the wrong number of fields",
                )
                continue
            row = dict(zip(header, values, strict=True))
            source = _trim(row.get("source_dataset")).upper()
            species_code = _trim(row.get("seafood_code"))
            if source:
                _increment_count(report.source_counts, source)
            if species_code:
                _increment_count(report.species_counts, species_code)
            try:
                normalized = normalize_legacy_row(row)
                normalized.source_row = row_number
                report.normalized_rows.append(normalized)
                for warning in normalized.normalization_warnings:
                    report.warnings += 1
                    _bounded_issue(
                        report,
                        row=row_number,
                        code=warning,
                        message="Source date was preserved as provenance but not imported",
                        blocking=False,
                    )
            except RowProblem as exc:
                setattr(report, exc.category, getattr(report, exc.category) + 1)
                report.blocking_errors += 1
                _bounded_issue(
                    report,
                    row=row_number,
                    code=exc.code,
                    message=exc.message,
                )
    except csv.Error:
        report.fatal_file_code = "CSV_MALFORMED"
        report.parse_errors += 1
        report.blocking_errors += 1
        _bounded_issue(
            report,
            row=None,
            code="CSV_MALFORMED",
            message="CSV syntax is malformed",
        )
    report.species_counts = dict(sorted(report.species_counts.items()))
    report.source_counts = dict(sorted(report.source_counts.items()))
    return report


def _material(row: NormalizedCandidate) -> dict[str, Any]:
    return {
        "species_code": row.species_code,
        "source_dataset": row.source_dataset,
        "source_record_id": row.source_record_id,
        "preview_url": row.preview_url,
        "original_url": row.original_url,
        "source_url": row.source_url,
        "creator": row.creator,
        "license": row.license,
        "license_url": row.license_url,
        "attribution": row.attribution,
        "location": row.location,
        "observed_on": row.observed_on.isoformat() if row.observed_on else None,
        "metadata_json": row.metadata_json,
        "active": row.active,
    }


def _canonical_url(value: str) -> str:
    parsed = urlsplit(value)
    hostname = (parsed.hostname or "").lower()
    host = f"[{hostname}]" if ":" in hostname else hostname
    if parsed.port not in {None, 443}:
        host = f"{host}:{parsed.port}"
    return urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))


def _classify(
    report: ImportPreview,
    *,
    species: dict[str, Species] | None = None,
    existing: list[tuple[Candidate, str]] | None = None,
) -> ImportPreview:
    identity_rows: dict[tuple[str, str], NormalizedCandidate] = {}
    url_identities: dict[str, set[tuple[str, str]]] = {}
    existing_identity: dict[tuple[str, str], NormalizedCandidate] = {}
    relevant_state: list[dict[str, Any]] = []

    if species is not None:
        relevant_codes = sorted({row.species_code for row in report.normalized_rows})
        relevant_state.append(
            {
                "species": [
                    {
                        "code": code,
                        "id": str(species[code].id) if code in species else None,
                        "active": species[code].active if code in species else None,
                    }
                    for code in relevant_codes
                ]
            }
        )
    wanted_identities = {
        (row.source_dataset, row.source_record_id) for row in report.normalized_rows
    }
    wanted_urls = {_canonical_url(row.original_url) for row in report.normalized_rows}
    for candidate, species_code in existing or []:
        identity = (candidate.source_dataset, candidate.source_record_id)
        canonical_existing_url = _canonical_url(candidate.original_url)
        if identity not in wanted_identities and canonical_existing_url not in wanted_urls:
            continue
        normalized = NormalizedCandidate(
            species_code=species_code,
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
            metadata_json=candidate.metadata_json,
            active=candidate.active,
        )
        existing_identity[identity] = normalized
        url_identities.setdefault(canonical_existing_url, set()).add(identity)
        relevant_state.append({"candidate": _material(normalized)})

    for index, row in enumerate(report.normalized_rows, start=2):
        source_row = row.source_row or index
        identity = (row.source_dataset, row.source_record_id)
        if species is not None and (
            row.species_code not in species or not species[row.species_code].active
        ):
            report.invalid_species += 1
            report.blocking_errors += 1
            _bounded_issue(
                report,
                row=source_row,
                code="INVALID_SPECIES",
                message="Species does not exist or is inactive",
            )
            continue

        prior = identity_rows.get(identity)
        database_row = existing_identity.get(identity)
        if prior is not None:
            if _material(prior) == _material(row):
                report.exact_duplicates += 1
                _bounded_issue(
                    report,
                    row=source_row,
                    code="EXACT_DUPLICATE",
                    message="Exact duplicate source record will be skipped",
                    blocking=False,
                )
            else:
                report.conflicting_identities += 1
                report.blocking_errors += 1
                _bounded_issue(
                    report,
                    row=source_row,
                    code="CONFLICTING_SOURCE_IDENTITY",
                    message="Source identity has conflicting normalized content",
                )
            continue
        identity_rows[identity] = row

        canonical = _canonical_url(row.original_url)
        other_identities = url_identities.get(canonical, set()).difference({identity})
        if other_identities:
            report.possible_url_duplicates += 1
            report.warnings += 1
            _bounded_issue(
                report,
                row=source_row,
                code="POSSIBLE_URL_DUPLICATE",
                message="Original URL is already associated with another source identity",
                blocking=False,
            )
        url_identities.setdefault(canonical, set()).add(identity)

        if database_row is not None:
            if _material(database_row) == _material(row):
                report.exact_duplicates += 1
                _bounded_issue(
                    report,
                    row=source_row,
                    code="EXACT_DUPLICATE",
                    message="Exact duplicate source record will be skipped",
                    blocking=False,
                )
            else:
                report.conflicting_identities += 1
                report.blocking_errors += 1
                _bounded_issue(
                    report,
                    row=source_row,
                    code="CONFLICTING_SOURCE_IDENTITY",
                    message="Source identity conflicts with an existing candidate",
                )
            continue
        report.new_normalized_rows.append(row)

    report.new_rows = len(report.new_normalized_rows)
    report.can_commit = report.blocking_errors == 0
    relevant_state.sort(
        key=lambda item: json.dumps(
            item, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
    )
    report.database_fingerprint = _digest(
        json.dumps(
            relevant_state,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    )
    return report


def preview_candidate_csv(content: bytes) -> ImportPreview:
    return _classify(_parse_candidate_csv(content))


async def _db_preview(
    session: AsyncSession, content: bytes, *, lock: bool = False
) -> ImportPreview:
    report = _parse_candidate_csv(content)
    if not report.normalized_rows:
        report.can_commit = report.blocking_errors == 0
        report.database_fingerprint = _digest(b"[]")
        return report
    species_statement = select(Species)
    candidate_statement = select(Candidate, Species.code).join(
        Species, Species.id == Candidate.species_id
    )
    if lock:
        species_statement = species_statement.with_for_update(read=True)
        candidate_statement = candidate_statement.with_for_update(read=True)
    species_rows = list((await session.scalars(species_statement)).all())
    candidates = list(
        (
            await session.execute(candidate_statement)
        ).all()
    )
    return _classify(
        report,
        species={record.code: record for record in species_rows},
        existing=[(candidate, species_code) for candidate, species_code in candidates],
    )


async def dry_run_candidate_csv(session: AsyncSession, content: bytes) -> ImportPreview:
    return await _db_preview(session, content)


def _safe_report(report: ImportPreview) -> dict[str, Any]:
    return report.model_dump(mode="json", exclude={"preview_token"})


def _sanitize_filename(value: str | None) -> str:
    basename = (value or "candidates.csv").replace("\\", "/").rsplit("/", 1)[-1]
    sanitized = re.sub(r"[^\w.\-]+", "_", basename, flags=re.UNICODE).strip("._")
    return (sanitized or "candidates.csv")[:255]


async def _lock_valid_mao_session(
    session: AsyncSession, actor_id: UUID, actor_session_id: UUID
) -> tuple[User, Session]:
    actor = await session.scalar(
        select(User)
        .where(User.id == actor_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        actor is None
        or actor.name != "Mao"
        or actor.role != "admin"
        or not actor.active
        or actor.must_change_password
    ):
        raise ImportConflict("IMPORT_ACTOR_NOT_ALLOWED")
    actor_session = await session.scalar(
        select(Session)
        .where(
            Session.id == actor_session_id,
            Session.user_id == actor_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        actor_session is None
        or actor_session.revoked_at is not None
        or as_utc(actor_session.expires_at) <= _now()
        or actor_session.password_version != actor.password_version
    ):
        raise ImportConflict("IMPORT_ACTOR_NOT_ALLOWED")
    return actor, actor_session


async def stage_candidate_csv(
    session: AsyncSession,
    content: bytes,
    *,
    actor_id: UUID,
    actor_session_id: UUID,
    filename: str | None,
) -> ImportPreview:
    try:
        await _lock_valid_mao_session(session, actor_id, actor_session_id)
        expired_ids = select(CandidateImportPreview.id).where(
            CandidateImportPreview.expires_at < _now(),
            CandidateImportPreview.committed_at.is_(None),
        ).limit(100)
        await session.execute(
            delete(CandidateImportPreview).where(CandidateImportPreview.id.in_(expired_ids))
        )
        report = await _db_preview(session, content)
        fatal_file_codes = {
            "CSV_TOO_LARGE",
            "CSV_TOO_MANY_ROWS",
            "CSV_INVALID_ENCODING",
            "CSV_INVALID_HEADER",
            "CSV_MISSING_HEADERS",
            "CSV_DUPLICATE_HEADERS",
            "CSV_MALFORMED",
        }
        if report.fatal_file_code in fatal_file_codes:
            await session.rollback()
            code = report.fatal_file_code
            assert code is not None
            raise ImportFileFatal(
                code=code,
                status_code=413
                if code in {"CSV_TOO_LARGE", "CSV_TOO_MANY_ROWS"}
                else 422,
                report=_safe_report(report),
            )
        raw_token = secrets.token_urlsafe(32)
        stage = CandidateImportPreview(
            actor_id=actor_id,
            actor_session_id=actor_session_id,
            token_digest=_token_digest(raw_token),
            file_sha256=report.file_sha256,
            filename=_sanitize_filename(filename),
            content=content,
            report_json=_safe_report(report),
            database_fingerprint=report.database_fingerprint,
            expires_at=_now() + PREVIEW_TTL,
        )
        session.add(stage)
        await session.commit()
        report.preview_token = raw_token
        return report
    except BaseException:
        if session.in_transaction():
            await session.rollback()
        raise


def _candidate_record(species_id: UUID, row: NormalizedCandidate) -> Candidate:
    return Candidate(
        species_id=species_id,
        source_dataset=row.source_dataset,
        source_record_id=row.source_record_id,
        preview_url=row.preview_url,
        original_url=row.original_url,
        source_url=row.source_url,
        creator=row.creator,
        license=row.license,
        license_url=row.license_url,
        attribution=row.attribution,
        location=row.location,
        observed_on=row.observed_on,
        metadata_json=row.metadata_json,
        active=True,
        version=1,
        current_reviewer_id=None,
        current_started_at=None,
    )


async def _committed_retry(
    session: AsyncSession,
    preview_token: str,
    actor_id: UUID,
    actor_session_id: UUID,
) -> ImportResult | None:
    stage = await session.scalar(
        select(CandidateImportPreview).where(
            CandidateImportPreview.token_digest == _token_digest(preview_token),
            CandidateImportPreview.actor_id == actor_id,
            CandidateImportPreview.actor_session_id == actor_session_id,
        )
    )
    if stage is not None and stage.committed_at is not None and stage.result_json is not None:
        return ImportResult.model_validate(stage.result_json)
    return None


async def _commit_once(
    session: AsyncSession,
    preview_token: str,
    actor_id: UUID,
    actor_session_id: UUID,
) -> ImportResult:
    condition = (
        CandidateImportPreview.token_digest == _token_digest(preview_token),
        CandidateImportPreview.actor_id == actor_id,
        CandidateImportPreview.actor_session_id == actor_session_id,
    )
    exists_for_actor = await session.scalar(
        select(CandidateImportPreview.id).where(*condition)
    )
    if exists_for_actor is None:
        raise ImportConflict("IMPORT_PREVIEW_NOT_FOUND")
    await _lock_valid_mao_session(session, actor_id, actor_session_id)
    stage = await session.scalar(
        select(CandidateImportPreview)
        .where(*condition)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if stage is None:
        raise ImportConflict("IMPORT_PREVIEW_NOT_FOUND")
    if stage.committed_at is not None and stage.result_json is not None:
        return ImportResult.model_validate(stage.result_json)
    if as_utc(stage.expires_at) <= _now():
        raise ImportConflict("IMPORT_PREVIEW_EXPIRED")
    if stage.content is None or _digest(stage.content) != stage.file_sha256:
        raise ImportConflict("IMPORT_PREVIEW_STALE")

    current = await _db_preview(session, stage.content, lock=True)
    if not bool(stage.report_json.get("can_commit")):
        raise ImportConflict("IMPORT_PREVIEW_BLOCKED")
    if (
        current.database_fingerprint != stage.database_fingerprint
        or _safe_report(current) != stage.report_json
    ):
        raise ImportConflict("IMPORT_PREVIEW_STALE")
    if not current.can_commit:
        raise ImportConflict("IMPORT_PREVIEW_STALE")

    species = {
        record.code: record.id for record in (await session.scalars(select(Species))).all()
    }
    session.add_all(
        [_candidate_record(species[row.species_code], row) for row in current.new_normalized_rows]
    )
    result = ImportResult(
        total=current.total,
        inserted=current.new_rows,
        skipped_exact=current.exact_duplicates,
        possible_url_duplicates=current.possible_url_duplicates,
        file_sha256=stage.file_sha256,
    )
    session.add(
        AuditEvent(
            actor_id=actor_id,
            action="CSV_IMPORT",
            object_type="CandidateImport",
            object_id=str(stage.id),
            reason="Validated candidate CSV import",
            before_json=None,
            after_json={
                "filename": stage.filename,
                "file_sha256": stage.file_sha256,
                "total": result.total,
                "inserted": result.inserted,
                "skipped_exact": result.skipped_exact,
                "possible_url_duplicates": result.possible_url_duplicates,
            },
        )
    )
    stage.content = None
    stage.committed_at = _now()
    stage.result_json = result.model_dump(mode="json")
    await session.commit()
    return result


async def commit_candidate_csv(
    session: AsyncSession,
    preview_token: str,
    actor_id: UUID,
    *,
    actor_session_id: UUID,
) -> ImportResult:
    for attempt in range(3):
        try:
            return await _commit_once(
                session, preview_token, actor_id, actor_session_id
            )
        except ImportConflict:
            if session.in_transaction():
                await session.rollback()
            raise
        except IntegrityError as exc:
            await session.rollback()
            retry = await _committed_retry(
                session, preview_token, actor_id, actor_session_id
            )
            if retry is not None:
                return retry
            message = str(exc).lower()
            if "unique" in message and "candidates" in message:
                raise ImportConflict("IMPORT_PREVIEW_STALE") from exc
            raise
        except OperationalError as exc:
            await session.rollback()
            if "locked" not in str(exc).lower() or attempt == 2:
                raise
            await asyncio.sleep(0.01 * (attempt + 1))
        except BaseException:
            if session.in_transaction():
                await session.rollback()
            raise
    raise RuntimeError("unreachable")
