from __future__ import annotations

import codecs
from contextlib import contextmanager
import csv
import io
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import stat
import tempfile
import time
from typing import Iterator
from uuid import UUID

from .engine import ReceiptItem
from .manifest import (
    ExportManifest,
    MAX_SAFE_INTEGER,
    ManifestError,
    _bounded_text,
    _https_url,
    _species_code,
    validate_relative_path,
)


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
_DECODED_SUFFIXES = frozenset({".jpg", ".png", ".webp"})
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_LOCK_FILENAME = ".sukaseafood-canonical.lock"
_LOCK_TIMEOUT_SECONDS = 5.0
_LOCK_POLL_SECONDS = 0.05


class CanonicalManifestError(RuntimeError):
    """The canonical training manifest could not be updated safely."""


def _is_regular(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and not bool(getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT)
    )


def _validated_root(root: Path) -> Path:
    selected = Path(root)
    try:
        metadata = selected.lstat()
        resolved = selected.resolve(strict=True)
    except (OSError, RuntimeError):
        raise CanonicalManifestError("CANONICAL_ROOT_UNSAFE") from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or bool(getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT)
    ):
        raise CanonicalManifestError("CANONICAL_ROOT_UNSAFE")
    return resolved


def _try_lock(descriptor: int) -> bool:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN)


@contextmanager
def _canonical_lock(root: Path) -> Iterator[None]:
    lock_path = root / _LOCK_FILENAME
    try:
        selected = lock_path.lstat()
    except FileNotFoundError:
        selected = None
    except OSError:
        raise CanonicalManifestError("CANONICAL_LOCK_UNSAFE") from None
    if selected is not None and not _is_regular(selected):
        raise CanonicalManifestError("CANONICAL_LOCK_UNSAFE")

    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError:
        raise CanonicalManifestError("CANONICAL_LOCK_UNSAFE") from None
    acquired = False
    try:
        opened = os.fstat(descriptor)
        current = lock_path.lstat()
        if (
            not _is_regular(opened)
            or not _is_regular(current)
            or not os.path.samestat(opened, current)
            or (selected is not None and not os.path.samestat(selected, current))
        ):
            raise CanonicalManifestError("CANONICAL_LOCK_UNSAFE")
        if opened.st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
        while not _try_lock(descriptor):
            if time.monotonic() >= deadline:
                raise CanonicalManifestError("CANONICAL_LOCK_TIMEOUT")
            time.sleep(_LOCK_POLL_SECONDS)
        acquired = True
        opened = os.fstat(descriptor)
        current = lock_path.lstat()
        if not _is_regular(current) or not os.path.samestat(opened, current):
            raise CanonicalManifestError("CANONICAL_LOCK_UNSAFE")
        yield
    except CanonicalManifestError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise CanonicalManifestError("CANONICAL_LOCK_FAILED") from None
    finally:
        if acquired:
            try:
                _unlock(descriptor)
            except OSError:
                pass
        try:
            os.close(descriptor)
        except OSError:
            pass


def _validate_canonical_row(raw: dict[str, str]) -> dict[str, str]:
    if set(raw) != set(CANONICAL_COLUMNS) or any(
        not isinstance(value, str) for value in raw.values()
    ):
        raise ValueError("invalid canonical row")
    candidate_id = str(UUID(raw["candidate_id"]))
    review_id = str(UUID(raw["review_id"]))
    if candidate_id != raw["candidate_id"] or review_id != raw["review_id"]:
        raise ValueError("non-canonical identifier")
    if not raw["review_version"].isdigit():
        raise ValueError("invalid review version")
    review_version = int(raw["review_version"])
    if not 1 <= review_version <= MAX_SAFE_INTEGER:
        raise ValueError("invalid review version")
    species_code = _species_code(raw["species_code"])
    relative = validate_relative_path(raw["relative_path"], "relative_path")
    if relative.suffix not in _DECODED_SUFFIXES:
        raise ValueError("invalid decoded suffix")
    expected = PurePosixPath("images") / species_code / f"{candidate_id}{relative.suffix}"
    if relative != expected or raw["relative_path"] != expected.as_posix():
        raise ValueError("canonical path mapping mismatch")
    if _SHA256.fullmatch(raw["sha256"]) is None:
        raise ValueError("invalid sha256")
    _https_url(raw["source_url"], "source_url")
    _bounded_text(raw["creator"], "creator", required=False)
    _bounded_text(raw["license"], "license", required=True)
    _https_url(raw["license_url"], "license_url", required=False)
    _bounded_text(raw["attribution"], "attribution", required=True, allow_newlines=True)
    return dict(raw)


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
        path_identities: set[str] = set()
        for raw in reader:
            if any(value is None for value in raw.values()):
                raise ValueError("invalid canonical row")
            validated = _validate_canonical_row(raw)  # type: ignore[arg-type]
            candidate_id = validated["candidate_id"]
            if candidate_id in rows:
                raise ValueError("duplicate candidate")
            path_identity = validated["relative_path"].casefold()
            if path_identity in path_identities:
                raise ValueError("duplicate Windows path identity")
            path_identities.add(path_identity)
            rows[candidate_id] = validated
        return rows
    except (ManifestError, OSError, UnicodeError, ValueError, csv.Error):
        raise CanonicalManifestError("CANONICAL_READ_FAILED") from None


def _encode(rows: dict[str, dict[str, str]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CANONICAL_COLUMNS, lineterminator="\r\n")
    writer.writeheader()
    for candidate_id in sorted(rows):
        writer.writerow(_validate_canonical_row(rows[candidate_id]))
    return codecs.BOM_UTF8 + output.getvalue().encode("utf-8")


def _write_canonical_manifest_locked(
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


def write_canonical_manifest(
    root: Path,
    manifest: ExportManifest,
    receipt_items: tuple[ReceiptItem, ...],
) -> Path:
    """Serialize and atomically merge successful outcomes for one training root."""

    safe_root = _validated_root(root)
    with _canonical_lock(safe_root):
        return _write_canonical_manifest_locked(safe_root, manifest, receipt_items)
