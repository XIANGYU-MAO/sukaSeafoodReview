from __future__ import annotations

import csv
from dataclasses import dataclass, field
import io
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Literal
import unicodedata
from urllib.parse import urlsplit
from uuid import UUID


EXPORT_COLUMNS = (
    "batch_id",
    "receipt_token",
    "action",
    "candidate_id",
    "review_id",
    "review_version",
    "species_code",
    "target_relative_path",
    "previous_relative_path",
    "preview_url",
    "original_url",
    "source_url",
    "creator",
    "license",
    "license_url",
    "attribution",
)
MAX_MANIFEST_BYTES = 20 * 1024 * 1024
MAX_MANIFEST_ROWS = 10_000
MAX_FIELD_CHARS = 4_096
MAX_SAFE_INTEGER = 2**63 - 1
SUPPORTED_SUFFIXES = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".gif", ".tif", ".tiff", ".bmp", ".image"}
)

_SPECIES_PATTERN = re.compile(r"[A-Z][A-Z0-9_-]{0,31}\Z", re.ASCII)
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{20,512}\Z", re.ASCII)
_INTEGER_PATTERN = re.compile(r"[0-9]+\Z", re.ASCII)
_DRIVE_PATTERN = re.compile(r"[A-Za-z]:")
_WINDOWS_INVALID = frozenset('<>:"|?*')
_WINDOWS_RESERVED = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CONIN$",
        "CONOUT$",
        "CLOCK$",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
        *(f"COM{number}" for number in "¹²³"),
        *(f"LPT{number}" for number in "¹²³"),
    }
)


class ManifestError(ValueError):
    """The export is not safe to consume locally."""


@dataclass(frozen=True, slots=True)
class ManifestRow:
    batch_id: UUID
    action: Literal["ADD", "MOVE", "REMOVE"]
    candidate_id: UUID
    review_id: UUID
    review_version: int
    species_code: str
    target_relative_path: PurePosixPath
    previous_relative_path: PurePosixPath | None
    preview_url: str = field(repr=False)
    original_url: str = field(repr=False)
    source_url: str = field(repr=False)
    creator: str | None
    license: str
    license_url: str | None = field(repr=False)
    attribution: str


@dataclass(frozen=True, slots=True)
class ExportManifest:
    rows: tuple[ManifestRow, ...]
    batch_id: UUID
    receipt_token: str = field(repr=False)


def _error(field_name: str, reason: str) -> ManifestError:
    return ManifestError(f"{field_name}: {reason}")


def _bounded_text(
    value: str,
    field_name: str,
    *,
    required: bool,
    allow_newlines: bool = False,
) -> str | None:
    if len(value) > MAX_FIELD_CHARS:
        raise _error(field_name, f"must not exceed {MAX_FIELD_CHARS} characters")
    forbidden = any(
        unicodedata.category(character) == "Cc"
        and (not allow_newlines or character not in {"\r", "\n"})
        for character in value
    )
    if forbidden:
        raise _error(field_name, "contains a control character")
    if not value.strip():
        if required:
            raise _error(field_name, "must not be blank")
        return None
    return value


def _uuid(value: str, field_name: str) -> UUID:
    _bounded_text(value, field_name, required=True, allow_newlines=False)
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        parsed = None
    if parsed is None:
        raise _error(field_name, "must be a canonical UUID") from None
    if str(parsed) != value:
        raise _error(field_name, "must be a canonical UUID")
    return parsed


def _positive_integer(value: str, field_name: str) -> int:
    _bounded_text(value, field_name, required=True, allow_newlines=False)
    if _INTEGER_PATTERN.fullmatch(value) is None:
        raise _error(field_name, "must be a positive integer")
    try:
        parsed = int(value)
    except ValueError:
        parsed = None
    if parsed is None:
        raise _error(field_name, "must be a positive safe integer") from None
    if not 1 <= parsed <= MAX_SAFE_INTEGER:
        raise _error(field_name, "must be a positive safe integer")
    return parsed


def _species_code(value: str) -> str:
    _bounded_text(value, "species_code", required=True, allow_newlines=False)
    if (
        _SPECIES_PATTERN.fullmatch(value) is None
        or value in _WINDOWS_RESERVED
    ):
        raise _error("species_code", "must be a Windows-safe server species code")
    return value


def _https_url(value: str, field_name: str, *, required: bool = True) -> str | None:
    checked = _bounded_text(
        value, field_name, required=required, allow_newlines=False
    )
    if checked is None:
        return None
    if any(character.isspace() for character in checked):
        raise _error(field_name, "must not contain whitespace")
    try:
        parsed = urlsplit(checked)
        _ = parsed.port
    except ValueError:
        parsed = None
    if parsed is None:
        raise _error(field_name, "must be an absolute HTTPS URL") from None
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise _error(field_name, "must be an absolute HTTPS URL without credentials")
    return checked


def _is_windows_reserved_component(component: str) -> bool:
    device_stem = component.partition(".")[0].rstrip(" .").upper()
    return device_stem in _WINDOWS_RESERVED


def _utf16_code_units(value: str) -> int:
    return sum(2 if ord(character) > 0xFFFF else 1 for character in value)


def _validate_windows_component(component: str, field_name: str) -> None:
    if not component or component in {".", ".."}:
        raise _error(field_name, "must be a safe relative path without empty or dot segments")
    if _utf16_code_units(component) > 255:
        raise _error(field_name, "contains a component exceeding 255 UTF-16 code units")
    if component.endswith((".", " ")):
        raise _error(field_name, "contains a Windows-unsafe trailing dot or space")
    if any(character in _WINDOWS_INVALID for character in component):
        raise _error(field_name, "contains a Windows-invalid path character")
    if any(unicodedata.category(character) == "Cc" for character in component):
        raise _error(field_name, "contains a control character")
    if _is_windows_reserved_component(component):
        raise _error(field_name, "contains a Windows reserved device name")


def validate_relative_path(
    value: str | PurePosixPath,
    field_name: str = "relative_path",
) -> PurePosixPath:
    """Validate server POSIX text without normalizing it."""

    if isinstance(value, PurePosixPath):
        raw = value.as_posix()
    elif isinstance(value, str):
        raw = value
    else:
        raise _error(field_name, "must be POSIX path text")
    if not raw or len(raw) > MAX_FIELD_CHARS:
        raise _error(field_name, "must be a bounded nonblank relative path")
    if "\\" in raw or raw.startswith(("/", "//")) or _DRIVE_PATTERN.match(raw):
        raise _error(field_name, "must be a safe relative path")
    parts = raw.split("/")
    for component in parts:
        _validate_windows_component(component, field_name)
    path = PurePosixPath(raw)
    if path.as_posix() != raw or path.is_absolute():
        raise _error(field_name, "must preserve safe POSIX relative path text")
    return path


def resolve_inside(root: Path, relative: str | PurePosixPath) -> Path:
    """Resolve a validated POSIX relative path and reject root/symlink escapes."""

    safe = validate_relative_path(relative)
    try:
        resolved_root = Path(root).resolve()
        resolved = resolved_root
        for component in safe.parts:
            resolved = resolved.joinpath(component).resolve(strict=False)
        resolved.relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError):
        resolved = None
    if resolved is None:
        raise ManifestError("relative_path resolves outside the selected root") from None
    return resolved


def _image_suffix(path: PurePosixPath, field_name: str) -> str:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise _error(field_name, "must use a server-supported image suffix")
    return suffix


def _validate_previous(path: PurePosixPath | None, field_name: str) -> None:
    if path is None or len(path.parts) < 3 or path.parts[0] != "images":
        raise _error(field_name, "must be a safe images/... relative path")
    _image_suffix(path, field_name)


def _validate_action_shape(row: ManifestRow) -> None:
    target = row.target_relative_path
    previous = row.previous_relative_path
    target_field = "target_relative_path"
    previous_field = "previous_relative_path"
    suffix = _image_suffix(target, target_field)
    candidate_name = f"{row.candidate_id}{suffix}"
    if row.action in {"ADD", "MOVE"}:
        if (
            len(target.parts) != 3
            or target.parts[0] != "images"
            or target.parts[1] != row.species_code
            or target.name.lower() != candidate_name.lower()
            or target.stem != str(row.candidate_id)
        ):
            raise _error(
                target_field,
                "must exactly match images/{species_code}/{candidate_id}.<supported suffix>",
            )
        if row.action == "MOVE":
            _validate_previous(previous, previous_field)
        elif previous is not None:
            _validate_previous(previous, previous_field)
    else:
        if (
            len(target.parts) != 3
            or target.parts[0] != "_removed"
            or target.parts[1] != str(row.batch_id)
            or target.name.lower() != candidate_name.lower()
            or target.stem != str(row.candidate_id)
        ):
            raise _error(
                target_field,
                "must exactly match _removed/{batch_id}/{candidate_id}.<supported suffix>",
            )
        _validate_previous(previous, previous_field)
    if previous is not None and previous.as_posix().casefold() == target.as_posix().casefold():
        raise _error(previous_field, "must be different from target_relative_path")


def _parse_row(raw: dict[str, str], row_number: int) -> tuple[ManifestRow, str]:
    for field_name, value in raw.items():
        if len(value) > MAX_FIELD_CHARS:
            raise _error(field_name, f"row {row_number} exceeds the field limit")
    batch_id = _uuid(raw["batch_id"], "batch_id")
    token = raw["receipt_token"]
    _bounded_text(token, "receipt_token", required=True, allow_newlines=False)
    if _TOKEN_PATTERN.fullmatch(token) is None:
        raise _error("receipt_token", "must be base64url-like token text")
    action = raw["action"]
    if action not in {"ADD", "MOVE", "REMOVE"}:
        raise _error("action", "must be exactly ADD, MOVE, or REMOVE")
    previous_raw = raw["previous_relative_path"]
    row = ManifestRow(
        batch_id=batch_id,
        action=action,  # type: ignore[arg-type]
        candidate_id=_uuid(raw["candidate_id"], "candidate_id"),
        review_id=_uuid(raw["review_id"], "review_id"),
        review_version=_positive_integer(raw["review_version"], "review_version"),
        species_code=_species_code(raw["species_code"]),
        target_relative_path=validate_relative_path(
            raw["target_relative_path"], "target_relative_path"
        ),
        previous_relative_path=(
            validate_relative_path(previous_raw, "previous_relative_path")
            if previous_raw
            else None
        ),
        preview_url=_https_url(raw["preview_url"], "preview_url") or "",
        original_url=_https_url(raw["original_url"], "original_url") or "",
        source_url=_https_url(raw["source_url"], "source_url") or "",
        creator=_bounded_text(
            raw["creator"], "creator", required=False, allow_newlines=True
        ),
        license=_bounded_text(
            raw["license"], "license", required=True, allow_newlines=True
        )
        or "",
        license_url=_https_url(raw["license_url"], "license_url", required=False),
        attribution=_bounded_text(
            raw["attribution"],
            "attribution",
            required=True,
            allow_newlines=True,
        )
        or "",
    )
    return row, token


def _read_manifest_bytes(path: Path) -> bytes:
    read_failed = False
    try:
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ManifestError("manifest must be a regular file")
            content = stream.read(MAX_MANIFEST_BYTES + 1)
            after = os.fstat(stream.fileno())
    except ManifestError:
        raise
    except OSError:
        read_failed = True
    if read_failed:
        raise ManifestError("manifest file cannot be read") from None
    if len(content) > MAX_MANIFEST_BYTES:
        raise ManifestError("manifest file exceeds 20 MiB")
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or len(content) != after.st_size
    ):
        raise ManifestError("manifest file changed while it was being read")
    return content


def _consume_record_newline(text: str, position: int) -> int:
    if text[position] == "\n":
        return position + 1
    if position + 1 < len(text) and text[position + 1] == "\n":
        return position + 2
    raise ManifestError("manifest contains malformed CSV")


def _validate_csv_lexemes(text: str) -> None:
    field_start = "field_start"
    unquoted = "unquoted"
    quoted = "quoted"
    after_quote = "after_quote"
    state = field_start
    position = 0
    while position < len(text):
        character = text[position]
        if state == field_start:
            if character == '"':
                state = quoted
                position += 1
            elif character == ",":
                position += 1
            elif character in {"\r", "\n"}:
                position = _consume_record_newline(text, position)
            else:
                state = unquoted
                position += 1
        elif state == unquoted:
            if character == '"':
                raise ManifestError("manifest contains malformed CSV")
            if character == ",":
                state = field_start
                position += 1
            elif character in {"\r", "\n"}:
                state = field_start
                position = _consume_record_newline(text, position)
            else:
                position += 1
        elif state == quoted:
            if character == '"':
                state = after_quote
            position += 1
        else:
            if character == '"':
                state = quoted
                position += 1
            elif character == ",":
                state = field_start
                position += 1
            elif character in {"\r", "\n"}:
                state = field_start
                position = _consume_record_newline(text, position)
            else:
                raise ManifestError("manifest contains malformed CSV")
    if state == quoted:
        raise ManifestError("manifest contains malformed CSV")


def load_manifest(path: Path) -> ExportManifest:
    """Load an exact fail-closed server export CSV."""

    manifest_path = Path(path)
    try:
        decoded = _read_manifest_bytes(manifest_path).decode("utf-8-sig")
    except UnicodeDecodeError:
        decoded = None
    if decoded is None:
        raise ManifestError("manifest must be valid UTF-8") from None
    _validate_csv_lexemes(decoded)

    rows: list[ManifestRow] = []
    tokens: list[str] = []
    csv_failed = False
    try:
        with io.StringIO(decoded, newline="") as stream:
            reader = csv.reader(stream, strict=True)
            header = next(reader, None)
            if header is None or tuple(header) != EXPORT_COLUMNS:
                raise ManifestError("CSV header must exactly match the server export contract")
            for row_number, cells in enumerate(reader, start=2):
                if len(rows) >= MAX_MANIFEST_ROWS:
                    raise ManifestError("manifest must contain at most 10,000 rows")
                if len(cells) != len(EXPORT_COLUMNS):
                    raise ManifestError(
                        f"CSV row {row_number} has an invalid column count"
                    )
                parsed, token = _parse_row(
                    dict(zip(EXPORT_COLUMNS, cells, strict=True)), row_number
                )
                rows.append(parsed)
                tokens.append(token)
    except csv.Error:
        csv_failed = True
    if csv_failed:
        raise ManifestError("manifest contains malformed CSV") from None

    if not rows:
        raise ManifestError("manifest must contain at least one row")
    batch_id = rows[0].batch_id
    if any(row.batch_id != batch_id for row in rows[1:]):
        raise ManifestError("batch mismatch between manifest rows")
    token = tokens[0]
    if any(candidate != token for candidate in tokens[1:]):
        raise ManifestError("receipt token mismatch between manifest rows")

    seen_operations: set[tuple[UUID, UUID, int]] = set()
    seen_targets: set[str] = set()
    for row in rows:
        operation = (row.candidate_id, row.review_id, row.review_version)
        if operation in seen_operations:
            raise ManifestError("duplicate operation triple in manifest")
        seen_operations.add(operation)
        folded_target = row.target_relative_path.as_posix().casefold()
        if folded_target in seen_targets:
            raise ManifestError("case-insensitive target collision in manifest")
        seen_targets.add(folded_target)
    for row in rows:
        _validate_action_shape(row)
    return ExportManifest(rows=tuple(rows), batch_id=batch_id, receipt_token=token)
