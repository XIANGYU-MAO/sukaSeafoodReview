from __future__ import annotations

import codecs
import csv
import io
import os
from pathlib import Path
import stat
import tempfile
from uuid import UUID

from .engine import ReceiptItem
from .manifest import ExportManifest, validate_relative_path


CANONICAL_FILENAME = "canonical_manifest.csv"
CANONICAL_COLUMNS = (
    "candidate_id",
    "review_id",
    "review_version",
    "species_code",
    "relative_path",
    "sha256",
    "source_url",
    "creator",
    "license",
    "license_url",
    "attribution",
)
_MAX_CANONICAL_BYTES = 20 * 1024 * 1024
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class CanonicalManifestError(RuntimeError):
    """The canonical training manifest could not be updated safely."""


def _is_regular(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and not bool(getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT)
    )


def _read_existing(path: Path) -> dict[str, dict[str, str]]:
    try:
        selected = path.lstat()
    except FileNotFoundError:
        return {}
    except OSError:
        raise CanonicalManifestError("CANONICAL_READ_FAILED") from None
    if not _is_regular(selected) or selected.st_size > _MAX_CANONICAL_BYTES:
        raise CanonicalManifestError("CANONICAL_FILE_UNSAFE")
    try:
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if not _is_regular(opened) or not os.path.samestat(selected, opened):
                raise OSError("canonical manifest changed")
            encoded = stream.read(_MAX_CANONICAL_BYTES + 1)
        if len(encoded) > _MAX_CANONICAL_BYTES:
            raise OSError("canonical manifest too large")
        text = encoded.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text, newline=""))
        if tuple(reader.fieldnames or ()) != CANONICAL_COLUMNS:
            raise ValueError("unexpected canonical columns")
        rows: dict[str, dict[str, str]] = {}
        for raw in reader:
            if set(raw) != set(CANONICAL_COLUMNS) or any(value is None for value in raw.values()):
                raise ValueError("invalid canonical row")
            candidate_id = str(UUID(raw["candidate_id"]))
            review_id = str(UUID(raw["review_id"]))
            if candidate_id != raw["candidate_id"] or review_id != raw["review_id"]:
                raise ValueError("non-canonical identifier")
            if not raw["review_version"].isdigit() or int(raw["review_version"]) < 1:
                raise ValueError("invalid review version")
            validate_relative_path(raw["relative_path"], "relative_path")
            if candidate_id in rows:
                raise ValueError("duplicate candidate")
            rows[candidate_id] = dict(raw)
        return rows
    except (OSError, UnicodeError, ValueError, csv.Error):
        raise CanonicalManifestError("CANONICAL_READ_FAILED") from None


def _encode(rows: dict[str, dict[str, str]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CANONICAL_COLUMNS, lineterminator="\r\n")
    writer.writeheader()
    for candidate_id in sorted(rows):
        writer.writerow(rows[candidate_id])
    return codecs.BOM_UTF8 + output.getvalue().encode("utf-8")


def write_canonical_manifest(
    root: Path,
    manifest: ExportManifest,
    receipt_items: tuple[ReceiptItem, ...],
) -> Path:
    """Atomically merge successful batch outcomes into the local training manifest."""

    target = root / CANONICAL_FILENAME
    rows = _read_existing(target)
    manifest_rows = {str(row.candidate_id): row for row in manifest.rows}
    for item in receipt_items:
        row = manifest_rows.get(item.candidate_id)
        if row is None or item.status != "SUCCEEDED":
            continue
        if row.action == "REMOVE":
            rows.pop(item.candidate_id, None)
            continue
        if item.relative_path is None or item.sha256 is None:
            raise CanonicalManifestError("CANONICAL_ITEM_INVALID")
        relative = validate_relative_path(item.relative_path, "relative_path")
        rows[item.candidate_id] = {
            "candidate_id": item.candidate_id,
            "review_id": item.review_id,
            "review_version": str(item.review_version),
            "species_code": row.species_code,
            "relative_path": relative.as_posix(),
            "sha256": item.sha256,
            "source_url": row.source_url,
            "creator": row.creator or "",
            "license": row.license,
            "license_url": row.license_url or "",
            "attribution": row.attribution,
        }

    payload = _encode(rows)
    descriptor = -1
    temporary: Path | None = None
    try:
        descriptor, raw_name = tempfile.mkstemp(
            prefix=f".{CANONICAL_FILENAME}.", suffix=".tmp", dir=root
        )
        temporary = Path(raw_name)
        metadata = temporary.lstat()
        if not _is_regular(metadata):
            raise OSError("unsafe canonical temporary")
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            current = target.lstat()
        except FileNotFoundError:
            current = None
        if current is not None and not _is_regular(current):
            raise OSError("unsafe canonical target")
        os.replace(temporary, target)
        temporary = None
    except (OSError, RuntimeError, ValueError):
        raise CanonicalManifestError("CANONICAL_WRITE_FAILED") from None
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
    return target
