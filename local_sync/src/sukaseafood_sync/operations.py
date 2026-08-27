from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Iterator, Literal
from uuid import UUID
import warnings

import imagehash
from PIL import Image, ImageOps

from .downloader import DownloadResult
from .index import AddIntent, SyncIndex, SyncRecord, SyncResult
from .manifest import (
    ManifestError,
    ManifestRow,
    SUPPORTED_SUFFIXES,
    resolve_inside,
    validate_relative_path,
)


_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_HEX_32 = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_HEX_64 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_HEX_16 = re.compile(r"[0-9a-f]{16}\Z", re.ASCII)
_FORMAT_SUFFIXES = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
_FORMAT_COMPATIBLE_SUFFIXES = {
    "JPEG": frozenset({".jpg", ".jpeg"}),
    "PNG": frozenset({".png"}),
    "WEBP": frozenset({".webp"}),
}
_MAX_RECOVERY_IMAGE_BYTES = 100 * 1024 * 1024
_LOG_FIELDS = (
    "candidate_id",
    "action",
    "status",
    "previous_relative_path",
    "relative_path",
    "sha256",
    "error",
    "timestamp",
)


class OperationError(RuntimeError):
    """A stable, secret-free local operation failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT)


def _regular(metadata: os.stat_result) -> bool:
    return stat.S_ISREG(metadata.st_mode) and not _is_reparse(metadata)


def _directory(metadata: os.stat_result) -> bool:
    return stat.S_ISDIR(metadata.st_mode) and not _is_reparse(metadata)


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _lstat(path: Path, code: str) -> os.stat_result | None:
    failed = False
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError:
        failed = True
        metadata = None
    if failed:
        raise OperationError(code)
    return metadata


def _validated_root(root: Path, index: SyncIndex | None = None) -> Path:
    selected = Path(root)
    metadata = _lstat(selected, "ROOT_UNSAFE")
    if metadata is None or not _directory(metadata) or stat.S_ISLNK(metadata.st_mode):
        raise OperationError("ROOT_UNSAFE")
    failed = False
    try:
        resolved = selected.resolve(strict=True)
    except (OSError, RuntimeError):
        failed = True
        resolved = selected
    if failed or (index is not None and resolved != index.root):
        raise OperationError("ROOT_UNSAFE")
    return resolved


def _safe_relative(value: PurePosixPath | str, field: str) -> PurePosixPath:
    failed = False
    try:
        relative = validate_relative_path(value, field)
    except (ManifestError, TypeError, ValueError):
        failed = True
        relative = PurePosixPath("invalid")
    if failed:
        raise OperationError("PATH_UNSAFE")
    return relative


def _windows_path_identity(relative: PurePosixPath) -> str:
    return relative.as_posix().casefold()


def _same_windows_path(left: PurePosixPath, right: PurePosixPath) -> bool:
    return _windows_path_identity(left) == _windows_path_identity(right)


def _resolved_path(root: Path, relative: PurePosixPath) -> Path:
    lexical = root.joinpath(*relative.parts)
    failed = False
    try:
        resolved = resolve_inside(root, relative)
    except (ManifestError, OSError, RuntimeError, ValueError):
        failed = True
        resolved = lexical
    if failed or resolved != lexical:
        raise OperationError("PATH_UNSAFE")
    return resolved


def _ensure_parents(root: Path, relative: PurePosixPath, code: str = "PARENT_UNSAFE") -> Path:
    current_relative = PurePosixPath()
    for component in relative.parent.parts:
        current_relative /= component
        current = _resolved_path(root, current_relative)
        metadata = _lstat(current, code)
        if metadata is None:
            failed = False
            try:
                os.mkdir(current, 0o700)
            except FileExistsError:
                pass
            except OSError:
                failed = True
            if failed:
                raise OperationError(code)
            metadata = _lstat(current, code)
        if metadata is None or not _directory(metadata) or stat.S_ISLNK(metadata.st_mode):
            raise OperationError(code)
        if _resolved_path(root, current_relative) != current:
            raise OperationError(code)
    return _resolved_path(root, relative)


def _hash_regular(path: Path, unsafe_code: str) -> tuple[str, int, os.stat_result]:
    before = _lstat(path, unsafe_code)
    if before is None or not _regular(before) or stat.S_ISLNK(before.st_mode):
        raise OperationError(unsafe_code)
    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    open_failed = False
    try:
        descriptor = os.open(path, flags)
    except OSError:
        open_failed = True
        descriptor = -1
    if open_failed:
        raise OperationError(unsafe_code)

    read_failed = False
    digest = hashlib.sha256()
    size = 0
    opened: os.stat_result | None = None
    after: os.stat_result | None = None
    try:
        opened = os.fstat(descriptor)
        if not _regular(opened) or not _same_file(before, opened):
            read_failed = True
        else:
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
            after = os.fstat(descriptor)
    except OSError:
        read_failed = True
    finally:
        try:
            os.close(descriptor)
        except OSError:
            read_failed = True
    current = _lstat(path, unsafe_code)
    if (
        read_failed
        or opened is None
        or after is None
        or current is None
        or not _regular(current)
        or not _same_file(before, after)
        or not _same_file(before, current)
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or size != after.st_size
    ):
        raise OperationError(unsafe_code)
    return digest.hexdigest(), size, before


def _read_recovery_image(
    path: Path, relative: PurePosixPath | None
) -> tuple[str, str, int, int, int, str, os.stat_result]:
    before = _lstat(path, "TARGET_UNSAFE")
    if (
        before is None
        or not _regular(before)
        or stat.S_ISLNK(before.st_mode)
        or not 0 < before.st_size <= _MAX_RECOVERY_IMAGE_BYTES
    ):
        raise OperationError("ADD_RECOVERY_INVALID")
    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    open_failed = False
    try:
        descriptor = os.open(path, flags)
    except OSError:
        open_failed = True
        descriptor = -1
    if open_failed:
        raise OperationError("TARGET_UNSAFE")

    content = bytearray()
    read_failed = False
    opened: os.stat_result | None = None
    after: os.stat_result | None = None
    try:
        opened = os.fstat(descriptor)
        if not _regular(opened) or not _same_file(before, opened):
            read_failed = True
        else:
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                content.extend(chunk)
                if len(content) > _MAX_RECOVERY_IMAGE_BYTES:
                    read_failed = True
                    break
            after = os.fstat(descriptor)
    except OSError:
        read_failed = True
    finally:
        try:
            os.close(descriptor)
        except OSError:
            read_failed = True
    current = _lstat(path, "TARGET_UNSAFE")
    if (
        read_failed
        or opened is None
        or after is None
        or current is None
        or not _regular(current)
        or not _same_file(before, after)
        or not _same_file(before, current)
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or len(content) != after.st_size
    ):
        raise OperationError("TARGET_UNSAFE")

    decode_failed = False
    decoded_format: str | None = None
    perceptual_hash = ""
    width = height = 0
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                image.verify()
            with Image.open(BytesIO(content)) as image:
                decoded_format = image.format
                if decoded_format not in _FORMAT_SUFFIXES:
                    decode_failed = True
                else:
                    image.load()
                    transposed = ImageOps.exif_transpose(image)
                    width, height = transposed.size
                    if width <= 0 or height <= 0:
                        decode_failed = True
                    else:
                        rgb = transposed.convert("RGB")
                        rgb.load()
                        perceptual_hash = str(imagehash.phash(rgb)).lower()
    except Exception:
        decode_failed = True
    if decoded_format is None or decode_failed:
        raise OperationError("ADD_RECOVERY_INVALID")
    suffix = _FORMAT_SUFFIXES[decoded_format]
    if (
        relative is not None
        and relative.suffix.casefold() not in _FORMAT_COMPATIBLE_SUFFIXES[decoded_format]
    ):
        raise OperationError("ADD_RECOVERY_INVALID")
    digest = hashlib.sha256(content).hexdigest()
    return digest, perceptual_hash, len(content), width, height, suffix, before


@contextmanager
def _pin_link_parents(source: Path, target: Path) -> Iterator[None]:
    """On Windows, deny rename/delete while pathname-based linking is in flight."""

    if os.name != "nt":
        yield
        return

    import ctypes
    from ctypes import wintypes

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("file_attributes", wintypes.DWORD),
            ("creation_time", wintypes.FILETIME),
            ("last_access_time", wintypes.FILETIME),
            ("last_write_time", wintypes.FILETIME),
            ("volume_serial_number", wintypes.DWORD),
            ("file_size_high", wintypes.DWORD),
            ("file_size_low", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    )
    get_information.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    common = Path(os.path.commonpath((source.parent, target.parent)))
    directories: list[Path] = [common]
    for parent in (source.parent, target.parent):
        try:
            relative = parent.relative_to(common)
        except ValueError:
            raise OperationError("PATH_UNSAFE") from None
        cursor = common
        for part in relative.parts:
            cursor /= part
            if cursor not in directories:
                directories.append(cursor)

    handles: list[int] = []
    invalid_handle = ctypes.c_void_p(-1).value
    try:
        for directory in directories:
            before = _lstat(directory, "PARENT_UNSAFE")
            if before is None or not stat.S_ISDIR(before.st_mode):
                raise OperationError("PARENT_UNSAFE")
            handle = create_file(
                str(directory),
                0x80000000,  # GENERIC_READ
                0x0001 | 0x0002,  # share read/write, intentionally not delete
                None,
                3,  # OPEN_EXISTING
                0x02000000 | 0x00200000,  # BACKUP_SEMANTICS | OPEN_REPARSE_POINT
                None,
            )
            if handle == invalid_handle:
                raise OperationError("PARENT_UNSAFE")
            handles.append(handle)
            information = _ByHandleFileInformation()
            if not get_information(handle, ctypes.byref(information)):
                raise OperationError("PARENT_UNSAFE")
            current = _lstat(directory, "PARENT_UNSAFE")
            file_index = (
                int(information.file_index_high) << 32
            ) | int(information.file_index_low)
            if (
                current is None
                or not stat.S_ISDIR(current.st_mode)
                or information.file_attributes & _REPARSE_POINT
                or not _same_file(before, current)
                or int(current.st_ino) != file_index
            ):
                raise OperationError("PARENT_UNSAFE")
        yield
    finally:
        for handle in reversed(handles):
            close_handle(handle)


@contextmanager
def _pin_owned_file(
    path: Path, owned: os.stat_result, code: str
) -> Iterator[None]:
    """Deny new Windows writers while an owned file is proved and unlinked."""

    if os.name != "nt":
        yield
        return

    import ctypes
    from ctypes import wintypes

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("file_attributes", wintypes.DWORD),
            ("creation_time", wintypes.FILETIME),
            ("last_access_time", wintypes.FILETIME),
            ("last_write_time", wintypes.FILETIME),
            ("volume_serial_number", wintypes.DWORD),
            ("file_size_high", wintypes.DWORD),
            ("file_size_low", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    )
    get_information.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    handle = create_file(
        str(path),
        0x80000000,  # GENERIC_READ
        0x0001 | 0x0004,  # share read/delete, intentionally not write
        None,
        3,  # OPEN_EXISTING
        0x00200000,  # FILE_FLAG_OPEN_REPARSE_POINT
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise OperationError(code)
    try:
        information = _ByHandleFileInformation()
        if not get_information(handle, ctypes.byref(information)):
            raise OperationError(code)
        current = _lstat(path, code)
        file_index = (
            int(information.file_index_high) << 32
        ) | int(information.file_index_low)
        if (
            current is None
            or not _regular(current)
            or information.file_attributes & _REPARSE_POINT
            or not _same_file(owned, current)
            or int(current.st_ino) != file_index
        ):
            raise OperationError(code)
        yield
    finally:
        close_handle(handle)


def _unlink_owned(
    path: Path,
    owned: os.stat_result,
    code: str,
    *,
    expected_sha: str | None = None,
) -> None:
    current = _lstat(path, code)
    if current is None:
        return
    if not _regular(current) or not _same_file(current, owned):
        raise OperationError(code)
    failed = False
    with _pin_owned_file(path, owned, code):
        if expected_sha is not None:
            digest, _size, current = _hash_regular(path, code)
            if digest != expected_sha or not _same_file(current, owned):
                raise OperationError("SOURCE_STATE_MISMATCH")
        with _pin_link_parents(path, path):
            try:
                path.unlink()
            except FileNotFoundError:
                return
            except OSError:
                failed = True
    if failed:
        raise OperationError(code)


def _link_no_clobber(
    source: Path,
    source_metadata: os.stat_result,
    target: Path,
    expected_sha: str,
) -> os.stat_result:
    failed = False
    exists = False
    with _pin_link_parents(source, target):
        try:
            os.link(source, target, follow_symlinks=False)
        except FileExistsError:
            exists = True
        except OSError:
            failed = True
        if failed:
            raise OperationError("FILESYSTEM_OPERATION_FAILED")
        if exists:
            target_sha, _size, target_metadata = _hash_regular(
                target, "TARGET_UNSAFE"
            )
            if target_sha != expected_sha:
                raise OperationError("TARGET_CONFLICT")
            return target_metadata
        target_sha, _size, target_metadata = _hash_regular(target, "TARGET_UNSAFE")
        if (
            not _same_file(source_metadata, target_metadata)
            or target_sha != expected_sha
        ):
            if _same_file(source_metadata, target_metadata):
                _unlink_owned(
                    target, target_metadata, "FILESYSTEM_OPERATION_FAILED"
                )
            raise OperationError("FILESYSTEM_OPERATION_FAILED")
        return target_metadata


def _validated_row_identity(row: ManifestRow, expected_action: str) -> None:
    if not isinstance(row, ManifestRow):
        raise OperationError("INVALID_ROW")
    if row.action != expected_action:
        raise OperationError("ACTION_MISMATCH")
    valid = (
        isinstance(row.batch_id, UUID)
        and isinstance(row.candidate_id, UUID)
        and isinstance(row.review_id, UUID)
        and isinstance(row.review_version, int)
        and not isinstance(row.review_version, bool)
        and row.review_version > 0
        and isinstance(row.species_code, str)
        and bool(row.species_code)
    )
    if not valid:
        raise OperationError("INVALID_ROW")


def _validated_row_path(
    row: ManifestRow, expected_action: str
) -> tuple[PurePosixPath, PurePosixPath | None]:
    _validated_row_identity(row, expected_action)
    target = _safe_relative(row.target_relative_path, "target_relative_path")
    previous = (
        _safe_relative(row.previous_relative_path, "previous_relative_path")
        if row.previous_relative_path is not None
        else None
    )
    suffix = target.suffix.lower()
    candidate_name = str(row.candidate_id)
    if suffix not in SUPPORTED_SUFFIXES or target.stem != candidate_name:
        raise OperationError("PATH_UNSAFE")
    if expected_action in {"ADD", "MOVE"}:
        if len(target.parts) != 3 or target.parts[:2] != ("images", row.species_code):
            raise OperationError("PATH_UNSAFE")
    elif (
        len(target.parts) != 3
        or target.parts[:2] != ("_removed", str(row.batch_id))
    ):
        raise OperationError("PATH_UNSAFE")
    if expected_action in {"MOVE", "REMOVE"} and previous is None:
        raise OperationError("SOURCE_STATE_MISSING")
    if previous is not None and (
        len(previous.parts) != 3
        or previous.parts[0] != "images"
        or previous.stem != candidate_name
        or previous.suffix.lower() not in SUPPORTED_SUFFIXES
    ):
        raise OperationError("PATH_UNSAFE")
    return target, previous


def _index_exact(index: SyncIndex, row: ManifestRow) -> SyncRecord | None:
    failed = False
    try:
        record = index.get_completed(
            row.candidate_id, row.review_id, row.review_version, row.action
        )
    except Exception:
        failed = True
        record = None
    if failed:
        raise OperationError("INDEX_READ_FAILED")
    return record


def _latest(index: SyncIndex, row: ManifestRow) -> SyncRecord | None:
    failed = False
    try:
        record = index.latest_for_candidate(row.candidate_id)
    except Exception:
        failed = True
        record = None
    if failed:
        raise OperationError("INDEX_READ_FAILED")
    return record


def _record(index: SyncIndex, result: SyncResult) -> None:
    failed = False
    try:
        index.record_success(result)
    except Exception:
        failed = True
    if failed:
        raise OperationError("INDEX_WRITE_FAILED")


def _add_intent(index: SyncIndex, row: ManifestRow) -> AddIntent | None:
    try:
        return index.get_add_intent(
            row.candidate_id, row.review_id, row.review_version, row.action
        )
    except Exception:
        raise OperationError("INDEX_READ_FAILED") from None


def _candidate_add_intents(index: SyncIndex, row: ManifestRow) -> tuple[AddIntent, ...]:
    try:
        return index.list_add_intents_for_candidate(row.candidate_id)
    except Exception:
        raise OperationError("INDEX_READ_FAILED") from None


def _record_add_intent(
    index: SyncIndex,
    result: SyncResult,
    target_relative: PurePosixPath,
) -> AddIntent:
    try:
        return index.record_add_intent(result, target_relative)
    except Exception:
        raise OperationError("INDEX_WRITE_FAILED") from None


def _record_replacement_intent(
    index: SyncIndex,
    row: ManifestRow,
    result: SyncResult,
    prior: SyncRecord,
    backup_relative: PurePosixPath,
) -> AddIntent:
    try:
        return index.record_add_intent(
            result,
            row.target_relative_path,
            prior_relative_path=prior.relative_path,
            prior_sha256=prior.sha256,
            backup_relative_path=backup_relative,
        )
    except Exception:
        raise OperationError("INDEX_WRITE_FAILED") from None


def _clear_add_intent(index: SyncIndex, expected: AddIntent) -> bool:
    try:
        return index.clear_add_intent_if_matches(expected)
    except Exception:
        raise OperationError("INDEX_WRITE_FAILED") from None


def _stored_path_allowed(row: ManifestRow, stored: PurePosixPath) -> bool:
    if row.action != "ADD":
        return stored == row.target_relative_path
    target = row.target_relative_path
    return (
        stored.parent == target.parent
        and stored.stem == target.stem
        and stored.suffix.casefold()
        in frozenset().union(*_FORMAT_COMPATIBLE_SUFFIXES.values())
    )


def _decoded_relative(
    target_relative: PurePosixPath, decoded_suffix: str
) -> PurePosixPath:
    target_suffix = target_relative.suffix
    compatible = (
        {".jpg", ".jpeg"} if decoded_suffix == ".jpg" else {decoded_suffix}
    )
    if target_suffix.casefold() in compatible:
        return target_relative
    return target_relative.with_suffix(decoded_suffix)


def _completed_skip(root: Path, row: ManifestRow, index: SyncIndex) -> SyncResult | None:
    stored = _index_exact(index, row)
    if stored is None:
        return None
    if not _stored_path_allowed(row, stored.relative_path):
        raise OperationError("COMPLETED_STATE_STALE")
    path = _resolved_path(root, stored.relative_path)
    try:
        digest, _size, _metadata = _hash_regular(path, "COMPLETED_STATE_STALE")
    except OperationError:
        raise OperationError("COMPLETED_STATE_STALE") from None
    if digest != stored.sha256:
        raise OperationError("COMPLETED_STATE_STALE")
    return _skipped_result(stored)


def _skipped_result(stored: SyncRecord) -> SyncResult:
    return SyncResult(
        candidate_id=stored.candidate_id,
        review_id=stored.review_id,
        review_version=stored.review_version,
        action=stored.action,
        batch_id=stored.batch_id,
        relative_path=stored.relative_path,
        sha256=stored.sha256,
        perceptual_hash=stored.perceptual_hash,
        completed_at=stored.completed_at,
        status="SKIPPED_ALREADY_COMPLETED",
    )


@dataclass(frozen=True, slots=True)
class OperationLogger:
    """Run-scoped, fsync-backed operation JSONL logger."""

    root: Path
    batch_id: UUID
    path: Path

    def __init__(self, root: Path, batch_id: UUID | str) -> None:
        safe_root = _validated_root(root)
        invalid_batch = False
        try:
            parsed_batch = batch_id if isinstance(batch_id, UUID) else UUID(batch_id)
        except (TypeError, ValueError, AttributeError):
            invalid_batch = True
            parsed_batch = UUID(int=0)
        if invalid_batch or str(parsed_batch) != str(batch_id):
            raise OperationError("LOG_PATH_UNSAFE")
        logs_relative = PurePosixPath("logs")
        _ensure_parents(safe_root, logs_relative / "placeholder", "LOG_PATH_UNSAFE")
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        relative = logs_relative / f"sync-{parsed_batch}-{timestamp}.jsonl"
        log_path = _resolved_path(safe_root, relative)
        existing = _lstat(log_path, "LOG_PATH_UNSAFE")
        if existing is not None and not _regular(existing):
            raise OperationError("LOG_PATH_UNSAFE")
        object.__setattr__(self, "root", safe_root)
        object.__setattr__(self, "batch_id", parsed_batch)
        object.__setattr__(self, "path", log_path)

    def validate(self) -> None:
        relative = PurePosixPath("logs") / self.path.name
        expected = _ensure_parents(self.root, relative, "LOG_PATH_UNSAFE")
        if expected != self.path:
            raise OperationError("LOG_PATH_UNSAFE")
        metadata = _lstat(self.path, "LOG_PATH_UNSAFE")
        if metadata is not None and not _regular(metadata):
            raise OperationError("LOG_PATH_UNSAFE")

    def append(
        self,
        *,
        candidate_id: UUID,
        action: str,
        status: str,
        previous_relative_path: PurePosixPath | None,
        relative_path: PurePosixPath,
        sha256: str | None,
        error: str | None,
    ) -> None:
        self.validate()
        entry = dict(
            zip(
                _LOG_FIELDS,
                (
                    str(candidate_id),
                    action,
                    status,
                    previous_relative_path.as_posix()
                    if previous_relative_path is not None
                    else None,
                    relative_path.as_posix(),
                    sha256,
                    error,
                    datetime.now(timezone.utc).isoformat(timespec="microseconds"),
                ),
                strict=True,
            )
        )
        payload = (json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        flags = os.O_CREAT | os.O_APPEND | os.O_WRONLY
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        failed = False
        descriptor = -1
        try:
            descriptor = os.open(self.path, flags, 0o600)
            opened = os.fstat(descriptor)
            current = os.lstat(self.path)
            if not _regular(opened) or not _regular(current) or not _same_file(opened, current):
                failed = True
            else:
                position = 0
                while position < len(payload):
                    written = os.write(descriptor, payload[position:])
                    if written <= 0:
                        failed = True
                        break
                    position += written
                if not failed:
                    os.fsync(descriptor)
        except OSError:
            failed = True
        finally:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    failed = True
        if failed:
            raise OperationError("LOG_WRITE_FAILED")


def _operation_logger(
    root: Path, row: ManifestRow, logger: OperationLogger | None
) -> OperationLogger:
    failure: OperationError | None = None
    active: OperationLogger | None = None
    try:
        if logger is None:
            active = OperationLogger(root, row.batch_id)
        elif (
            not isinstance(logger, OperationLogger)
            or logger.root != root
            or logger.batch_id != row.batch_id
        ):
            failure = OperationError("LOG_PATH_UNSAFE")
        else:
            logger.validate()
            active = logger
    except OperationError:
        failure = OperationError("LOG_PATH_UNSAFE")
    except Exception:
        failure = OperationError("LOG_SETUP_FAILED")
    if failure is not None:
        raise failure
    assert active is not None
    return active


def _safe_log(
    logger: OperationLogger,
    candidate_id: UUID,
    action: str,
    previous_relative_path: PurePosixPath | None,
    status: str,
    relative: PurePosixPath,
    sha256: str | None,
    error: str | None,
) -> None:
    """Append best-effort without changing an already determined outcome."""

    try:
        logger.append(
            candidate_id=candidate_id,
            action=action,
            status=status,
            previous_relative_path=previous_relative_path,
            relative_path=relative,
            sha256=sha256,
            error=error,
        )
    except Exception:
        pass


def _desired_result(
    row: ManifestRow, relative: PurePosixPath, sha256: str, phash: str
) -> SyncResult:
    return SyncResult(
        candidate_id=row.candidate_id,
        review_id=row.review_id,
        review_version=row.review_version,
        action=row.action,
        batch_id=row.batch_id,
        relative_path=relative,
        sha256=sha256,
        perceptual_hash=phash,
    )


def _cleanup_composite_previous(
    root: Path,
    row: ManifestRow,
    actual_relative: PurePosixPath,
    index: SyncIndex,
) -> None:
    if row.previous_relative_path is None or _same_windows_path(
        row.previous_relative_path, actual_relative
    ):
        return
    prior = _latest(index, row)
    if (
        prior is None
        or not prior.present
        or prior.relative_path != row.previous_relative_path
    ):
        return
    previous_path = _resolved_path(root, row.previous_relative_path)
    metadata = _lstat(previous_path, "SOURCE_UNSAFE")
    if metadata is None:
        return
    digest, _size, owned = _hash_regular(previous_path, "SOURCE_UNSAFE")
    if digest != prior.sha256:
        raise OperationError("SOURCE_STATE_MISMATCH")
    _unlink_owned(
        previous_path,
        owned,
        "FILESYSTEM_OPERATION_FAILED",
        expected_sha=prior.sha256,
    )


def _validated_download(
    root: Path,
    row: ManifestRow,
    target_relative: PurePosixPath,
    result: DownloadResult,
    *,
    isolated_staging_path: Path | None = None,
) -> tuple[PurePosixPath, Path, str, str, os.stat_result]:
    if not isinstance(result, DownloadResult):
        raise OperationError("DOWNLOAD_RESULT_INVALID")
    valid_metadata = (
        result.format in _FORMAT_SUFFIXES
        and _FORMAT_SUFFIXES.get(result.format) == result.suffix
        and _HEX_64.fullmatch(result.sha256) is not None
        and _HEX_16.fullmatch(result.phash) is not None
        and isinstance(result.byte_count, int)
        and not isinstance(result.byte_count, bool)
        and result.byte_count > 0
        and isinstance(result.width, int)
        and not isinstance(result.width, bool)
        and result.width > 0
        and isinstance(result.height, int)
        and not isinstance(result.height, bool)
        and result.height > 0
    )
    if not valid_metadata:
        raise OperationError("DOWNLOAD_RESULT_INVALID")
    if isolated_staging_path is None:
        staging_relative = target_relative.with_name(target_relative.name + ".part")
        expected_staging = _resolved_path(root, staging_relative)
    else:
        expected_staging = _validated_isolated_staging_path(
            root, target_relative, isolated_staging_path
        )
    if not isinstance(result.staging_path, Path) or result.staging_path != expected_staging:
        raise OperationError("STAGING_PATH_INVALID")
    digest, size, staging_metadata = _hash_regular(
        expected_staging, "STAGING_FILE_UNSAFE"
    )
    if digest != result.sha256 or size != result.byte_count:
        raise OperationError("STAGING_CONTENT_MISMATCH")
    actual_relative = _decoded_relative(target_relative, result.suffix)
    return actual_relative, expected_staging, result.sha256, result.phash, staging_metadata


def _validated_isolated_staging_path(
    root: Path, target_relative: PurePosixPath, expected_staging: Path
) -> Path:
    if not isinstance(expected_staging, Path):
        raise OperationError("STAGING_PATH_INVALID")
    expected_parent = root.joinpath(*target_relative.parent.parts)
    prefix = f".{target_relative.name}."
    suffix = ".sync-download.part"
    name = expected_staging.name
    token = (
        name[len(prefix) : -len(suffix)]
        if name.startswith(prefix) and name.endswith(suffix)
        else ""
    )
    if expected_staging.parent != expected_parent or _HEX_32.fullmatch(token) is None:
        raise OperationError("STAGING_PATH_INVALID")
    try:
        lexical_relative = expected_staging.relative_to(root)
        staging_relative = PurePosixPath(*lexical_relative.parts)
    except ValueError:
        raise OperationError("STAGING_PATH_INVALID") from None
    if _resolved_path(root, staging_relative) != expected_staging:
        raise OperationError("STAGING_PATH_INVALID")
    return expected_staging


def discard_isolated_staging(
    root: Path,
    row: ManifestRow,
    downloaded: DownloadResult,
    download_destination: Path,
) -> None:
    """Delete only the exact verified private stage owned by this download."""

    target_relative, _previous = _validated_row_path(row, "ADD")
    safe_root = _validated_root(root)
    if not isinstance(download_destination, Path):
        raise OperationError("STAGING_PATH_INVALID")
    expected = download_destination.with_name(download_destination.name + ".part")
    if not isinstance(downloaded, DownloadResult) or downloaded.staging_path != expected:
        raise OperationError("STAGING_PATH_INVALID")
    _validated_isolated_staging_path(safe_root, target_relative, expected)
    if _lstat(expected, "STAGING_FILE_UNSAFE") is None:
        return
    _actual, staging, sha256, _phash, owned = _validated_download(
        safe_root,
        row,
        target_relative,
        downloaded,
        isolated_staging_path=expected,
    )
    _unlink_owned(
        staging,
        owned,
        "STAGING_FILE_UNSAFE",
        expected_sha=sha256,
    )


def promote_isolated_staging(
    root: Path,
    row: ManifestRow,
    destination: Path,
    download_destination: Path,
    downloaded: DownloadResult,
) -> DownloadResult:
    """No-clobber promotion from a private verified stage to shared recovery."""

    target_relative, _previous = _validated_row_path(row, "ADD")
    safe_root = _validated_root(root)
    if _ensure_parents(safe_root, target_relative) != destination:
        raise OperationError("PATH_UNSAFE")
    if not isinstance(download_destination, Path):
        raise OperationError("STAGING_PATH_INVALID")
    source = download_destination.with_name(download_destination.name + ".part")
    actual_relative, source, sha256, phash, source_owned = _validated_download(
        safe_root,
        row,
        target_relative,
        downloaded,
        isolated_staging_path=source,
    )
    shared_relative = target_relative.with_name(target_relative.name + ".part")
    shared = _resolved_path(safe_root, shared_relative)
    shared_owned = _link_no_clobber(source, source_owned, shared, sha256)
    try:
        shared_image = _read_recovery_image(shared, None)
    except OperationError:
        raise OperationError("STAGING_CONTENT_MISMATCH") from None
    if (
        shared_image[0] != sha256
        or shared_image[1] != phash
        or _decoded_relative(target_relative, shared_image[5]) != actual_relative
        or not _same_file(shared_owned, shared_image[6])
    ):
        raise OperationError("STAGING_CONTENT_MISMATCH")
    _unlink_owned(
        source,
        source_owned,
        "STAGING_FILE_UNSAFE",
        expected_sha=sha256,
    )
    return replace(downloaded, staging_path=shared)


def _managed_replacement_evidence(
    root: Path,
    row: ManifestRow,
    result: SyncResult,
    index: SyncIndex,
) -> tuple[SyncRecord, Path, os.stat_result, PurePosixPath]:
    """Prove the old managed target before persisting replacement intent."""

    prior = _latest(index, row)
    if prior is None or not prior.present or prior.relative_path != result.relative_path:
        raise OperationError("SOURCE_STATE_MISMATCH")
    if row.review_version <= prior.review_version:
        raise OperationError("STALE_GENERATION")
    target = _resolved_path(root, result.relative_path)
    target_sha, _target_size, target_owned = _hash_regular(target, "SOURCE_UNSAFE")
    if target_sha != prior.sha256:
        raise OperationError("SOURCE_STATE_MISMATCH")
    backup_relative = PurePosixPath(
        "_removed", str(row.batch_id), result.relative_path.name
    )
    return prior, target, target_owned, backup_relative


def _apply_managed_replacement(
    root: Path,
    row: ManifestRow,
    result: SyncResult,
    staging: Path,
    staging_metadata: os.stat_result,
    index: SyncIndex,
) -> None:
    prior, target, target_owned, backup_relative = (
        _managed_replacement_evidence(root, row, result, index)
    )
    if result.sha256 == prior.sha256:
        if result.perceptual_hash != prior.perceptual_hash:
            raise OperationError("STAGING_CONTENT_MISMATCH")
        return
    backup = _ensure_parents(root, backup_relative)
    _record_replacement_intent(index, row, result, prior, backup_relative)
    _link_no_clobber(target, target_owned, backup, prior.sha256)
    _unlink_owned(
        target,
        target_owned,
        "FILESYSTEM_OPERATION_FAILED",
        expected_sha=prior.sha256,
    )
    _link_no_clobber(staging, staging_metadata, target, result.sha256)


def _apply_add(
    root: Path, row: ManifestRow, download_result: DownloadResult, index: SyncIndex
) -> SyncResult:
    target_relative, _previous = _validated_row_path(row, "ADD")
    skipped = _completed_skip(root, row, index)
    if skipped is not None:
        return skipped
    (
        actual_relative,
        staging,
        sha256,
        phash,
        staging_metadata,
    ) = _validated_download(root, row, target_relative, download_result)
    result = _desired_result(row, actual_relative, sha256, phash)
    if row.previous_relative_path is not None and _same_windows_path(
        row.previous_relative_path, actual_relative
    ):
        _apply_managed_replacement(
            root, row, result, staging, staging_metadata, index
        )
        _record(index, result)
        try:
            _unlink_owned(staging, staging_metadata, "FILESYSTEM_OPERATION_FAILED")
        except OperationError:
            pass
        return result
    target = _ensure_parents(root, actual_relative)
    target_metadata = _lstat(target, "TARGET_UNSAFE")
    if target_metadata is not None and (
        not _regular(target_metadata) or stat.S_ISLNK(target_metadata.st_mode)
    ):
        raise OperationError("TARGET_UNSAFE")
    _record_add_intent(index, result, target_relative)
    if target_metadata is not None:
        target_sha, _size, _target_owned = _hash_regular(target, "TARGET_UNSAFE")
        if target_sha != sha256:
            raise OperationError("TARGET_CONFLICT")
    else:
        try:
            dedupe = index.find_present_by_sha256(sha256)
        except Exception:
            dedupe = None
        dedupe_source: tuple[Path, os.stat_result] | None = None
        if dedupe is not None and dedupe.relative_path != actual_relative:
            try:
                source_path = _resolved_path(root, dedupe.relative_path)
                source_sha, _source_size, source_metadata = _hash_regular(
                    source_path, "SOURCE_UNSAFE"
                )
            except OperationError:
                source_path = target
                source_sha = ""
                source_metadata = staging_metadata
            if source_sha == sha256:
                dedupe_source = (source_path, source_metadata)
        if dedupe_source is not None:
            _link_no_clobber(dedupe_source[0], dedupe_source[1], target, sha256)
        else:
            _link_no_clobber(staging, staging_metadata, target, sha256)
    _cleanup_composite_previous(root, row, actual_relative, index)
    _record(index, result)
    try:
        _unlink_owned(staging, staging_metadata, "FILESYSTEM_OPERATION_FAILED")
    except OperationError:
        pass
    return result


def _clear_replacement_intent(
    root: Path,
    row: ManifestRow,
    index: SyncIndex,
    intent: AddIntent,
    prior: SyncRecord | None,
) -> SyncResult | None:
    _clear_add_intent(index, intent)
    current = _add_intent(index, row)
    stored = _index_exact(index, row)
    latest = _latest(index, row)
    if current is not None:
        raise OperationError("ADD_RECOVERY_STATE_CHANGED")

    prior_relative = intent.prior_relative_path
    prior_sha = intent.prior_sha256
    backup_relative = intent.backup_relative_path
    if prior_relative is None or prior_sha is None or backup_relative is None:
        raise OperationError("ADD_RECOVERY_STATE_CHANGED")

    try:
        target_lexical = root.joinpath(*intent.actual_relative_path.parts)
        target_metadata = _lstat(target_lexical, "ADD_RECOVERY_STATE_CHANGED")
        target: Path | None = None
        target_sha: str | None = None
        if target_metadata is not None:
            target = _resolved_path(root, intent.actual_relative_path)
            target_sha, _target_size, target_metadata = _hash_regular(
                target, "ADD_RECOVERY_STATE_CHANGED"
            )

        backup_lexical = root.joinpath(*backup_relative.parts)
        backup_metadata = _lstat(backup_lexical, "ADD_RECOVERY_STATE_CHANGED")
        backup_sha: str | None = None
        if backup_metadata is not None:
            backup = _resolved_path(root, backup_relative)
            backup_sha, _backup_size, backup_metadata = _hash_regular(
                backup, "ADD_RECOVERY_STATE_CHANGED"
            )

        staging_relative = intent.target_relative_path.with_name(
            intent.target_relative_path.name + ".part"
        )
        staging_lexical = root.joinpath(*staging_relative.parts)
        staging_metadata = _lstat(staging_lexical, "ADD_RECOVERY_STATE_CHANGED")
        staging: Path | None = None
        staging_image: tuple[str, str, int, int, int, str, os.stat_result] | None = (
            None
        )
        if staging_metadata is not None:
            staging = _resolved_path(root, staging_relative)
            staging_image = _read_recovery_image(staging, None)
    except OperationError:
        raise OperationError("ADD_RECOVERY_STATE_CHANGED") from None

    if stored is None:
        if (
            prior is not None
            and latest == prior
            and target_sha == prior_sha
            and backup_sha == prior_sha
            and staging_metadata is None
        ):
            return None
        raise OperationError("ADD_RECOVERY_STATE_CHANGED")

    if (
        stored.batch_id != intent.batch_id
        or stored.relative_path != intent.actual_relative_path
        or stored.sha256 != intent.sha256
        or stored.perceptual_hash != intent.perceptual_hash
        or target is None
        or target_sha != stored.sha256
        or backup_sha != prior_sha
    ):
        raise OperationError("ADD_RECOVERY_STATE_CHANGED")
    try:
        target_image = _read_recovery_image(target, stored.relative_path)
    except OperationError:
        raise OperationError("ADD_RECOVERY_STATE_CHANGED") from None
    if (
        target_image[0] != stored.sha256
        or target_image[1] != stored.perceptual_hash
        or _decoded_relative(intent.target_relative_path, target_image[5])
        != stored.relative_path
    ):
        raise OperationError("ADD_RECOVERY_STATE_CHANGED")
    if staging is not None and staging_image is not None:
        if (
            staging_image[0] != stored.sha256
            or staging_image[1] != stored.perceptual_hash
            or _decoded_relative(intent.target_relative_path, staging_image[5])
            != stored.relative_path
        ):
            raise OperationError("ADD_RECOVERY_STATE_CHANGED")
        _unlink_owned(staging, staging_image[6], "ADD_RECOVERY_STATE_CHANGED")
    return _skipped_result(stored)


def _recover_managed_replacement(
    root: Path,
    row: ManifestRow,
    index: SyncIndex,
    intent: AddIntent,
) -> SyncResult | None:
    prior_relative = intent.prior_relative_path
    prior_sha = intent.prior_sha256
    backup_relative = intent.backup_relative_path
    if prior_relative is None or prior_sha is None or backup_relative is None:
        raise OperationError("ADD_RECOVERY_INTENT_CONFLICT")
    if (
        intent.target_relative_path != row.target_relative_path
        or row.previous_relative_path is None
        or not _same_windows_path(row.previous_relative_path, intent.actual_relative_path)
        or not _same_windows_path(prior_relative, intent.actual_relative_path)
    ):
        raise OperationError("ADD_RECOVERY_INTENT_CONFLICT")

    stored = _index_exact(index, row)
    if stored is not None:
        if (
            stored.batch_id != intent.batch_id
            or stored.relative_path != intent.actual_relative_path
            or stored.sha256 != intent.sha256
            or stored.perceptual_hash != intent.perceptual_hash
        ):
            raise OperationError("COMPLETED_STATE_STALE")
        target = _resolved_path(root, stored.relative_path)
        target_sha, _target_size, _target_owned = _hash_regular(
            target, "COMPLETED_STATE_STALE"
        )
        if target_sha != stored.sha256:
            raise OperationError("COMPLETED_STATE_STALE")
        return _clear_replacement_intent(root, row, index, intent, None)

    if intent.batch_id != row.batch_id:
        raise OperationError("ADD_RECOVERY_INTENT_CONFLICT")

    prior = _latest(index, row)
    if prior is None:
        raise OperationError("SOURCE_STATE_MISMATCH")
    if row.review_version <= prior.review_version:
        raise OperationError("STALE_GENERATION")
    if (
        not prior.present
        or prior.relative_path != prior_relative
        or prior.sha256 != prior_sha
    ):
        raise OperationError("SOURCE_STATE_MISMATCH")

    target = _resolved_path(root, intent.actual_relative_path)
    backup = _ensure_parents(root, backup_relative)
    staging_relative = intent.target_relative_path.with_name(
        intent.target_relative_path.name + ".part"
    )
    staging_lexical = root.joinpath(*staging_relative.parts)

    target_metadata = _lstat(target, "TARGET_UNSAFE")
    target_sha: str | None = None
    target_owned: os.stat_result | None = None
    if target_metadata is not None:
        target_sha, _target_size, target_owned = _hash_regular(target, "TARGET_UNSAFE")
        if target_sha not in {prior_sha, intent.sha256}:
            raise OperationError("SOURCE_STATE_MISMATCH")

    backup_metadata = _lstat(backup, "SOURCE_UNSAFE")
    backup_sha: str | None = None
    if backup_metadata is not None:
        backup_sha, _backup_size, backup_metadata = _hash_regular(
            backup, "SOURCE_UNSAFE"
        )
        if backup_sha != prior_sha:
            raise OperationError("SOURCE_STATE_MISMATCH")

    staging_metadata = _lstat(staging_lexical, "STAGING_FILE_UNSAFE")
    staging: Path | None = None
    staging_image: tuple[str, str, int, int, int, str, os.stat_result] | None = None
    if staging_metadata is not None:
        staging = _resolved_path(root, staging_relative)
        staging_image = _read_recovery_image(staging, None)
        if (
            staging_image[0] != intent.sha256
            or staging_image[1] != intent.perceptual_hash
            or _decoded_relative(intent.target_relative_path, staging_image[5])
            != intent.actual_relative_path
        ):
            raise OperationError("ADD_RECOVERY_INTENT_CONFLICT")

    if target_sha == intent.sha256:
        if backup_sha != prior_sha:
            raise OperationError("ADD_RECOVERY_INTENT_CONFLICT")
    elif target_sha == prior_sha:
        assert target_owned is not None
        if backup_sha is None:
            backup_metadata = _link_no_clobber(
                target, target_owned, backup, prior_sha
            )
            backup_sha = prior_sha
        if staging is None or staging_image is None:
            return _clear_replacement_intent(root, row, index, intent, prior)
        _unlink_owned(
            target,
            target_owned,
            "FILESYSTEM_OPERATION_FAILED",
            expected_sha=prior_sha,
        )
        _link_no_clobber(staging, staging_image[6], target, intent.sha256)
    elif target_sha is None:
        if backup_sha != prior_sha or backup_metadata is None:
            raise OperationError("ADD_RECOVERY_INTENT_CONFLICT")
        if staging is None or staging_image is None:
            _link_no_clobber(backup, backup_metadata, target, prior_sha)
            restored_sha, _restored_size, _restored_owned = _hash_regular(
                target, "SOURCE_UNSAFE"
            )
            if restored_sha != prior_sha:
                raise OperationError("SOURCE_STATE_MISMATCH")
            return _clear_replacement_intent(root, row, index, intent, prior)
        _link_no_clobber(staging, staging_image[6], target, intent.sha256)
    else:
        raise OperationError("ADD_RECOVERY_INTENT_CONFLICT")

    installed_sha, _installed_size, _installed_owned = _hash_regular(
        target, "TARGET_UNSAFE"
    )
    if installed_sha != intent.sha256:
        raise OperationError("ADD_RECOVERY_INTENT_CONFLICT")
    result = _desired_result(
        row, intent.actual_relative_path, intent.sha256, intent.perceptual_hash
    )
    _record(index, result)
    if staging is not None and staging_image is not None:
        try:
            _unlink_owned(
                staging, staging_image[6], "FILESYSTEM_OPERATION_FAILED"
            )
        except OperationError:
            pass
    return result


def _reconcile_superseded_replacement_intent(
    root: Path,
    index: SyncIndex,
    intent: AddIntent,
) -> None:
    prior_relative = intent.prior_relative_path
    prior_sha = intent.prior_sha256
    backup_relative = intent.backup_relative_path
    if prior_relative is None or prior_sha is None or backup_relative is None:
        raise OperationError("ADD_RECOVERY_INTENT_CONFLICT")
    if not _same_windows_path(prior_relative, intent.actual_relative_path):
        raise OperationError("ADD_RECOVERY_INTENT_CONFLICT")

    try:
        stored = index.get_completed(
            intent.candidate_id,
            intent.review_id,
            intent.review_version,
            intent.action,
        )
        latest = index.latest_for_candidate(intent.candidate_id)
    except Exception:
        raise OperationError("INDEX_READ_FAILED") from None

    target = _resolved_path(root, intent.actual_relative_path)
    target_metadata = _lstat(target, "ADD_RECOVERY_STATE_CHANGED")
    target_sha: str | None = None
    if target_metadata is not None:
        target_sha, _target_size, target_metadata = _hash_regular(
            target, "ADD_RECOVERY_STATE_CHANGED"
        )

    backup = _resolved_path(root, backup_relative)
    backup_metadata = _lstat(backup, "ADD_RECOVERY_STATE_CHANGED")
    backup_sha: str | None = None
    if backup_metadata is not None:
        backup_sha, _backup_size, backup_metadata = _hash_regular(
            backup, "ADD_RECOVERY_STATE_CHANGED"
        )
        if backup_sha != prior_sha:
            raise OperationError("ADD_RECOVERY_STATE_CHANGED")

    staging_relative = intent.target_relative_path.with_name(
        intent.target_relative_path.name + ".part"
    )
    staging = _resolved_path(root, staging_relative)
    staging_metadata = _lstat(staging, "ADD_RECOVERY_STATE_CHANGED")
    staging_owned: os.stat_result | None = None
    if staging_metadata is not None:
        staging_image = _read_recovery_image(staging, None)
        if staging_image[0] == intent.sha256:
            if (
                staging_image[1] != intent.perceptual_hash
                or _decoded_relative(intent.target_relative_path, staging_image[5])
                != intent.actual_relative_path
            ):
                raise OperationError("ADD_RECOVERY_STATE_CHANGED")
            staging_owned = staging_image[6]

    if stored is not None:
        if (
            stored.relative_path != intent.actual_relative_path
            or stored.sha256 != intent.sha256
            or stored.perceptual_hash != intent.perceptual_hash
            or target_sha != stored.sha256
        ):
            raise OperationError("ADD_RECOVERY_STATE_CHANGED")
    elif latest is not None and latest.review_version > intent.review_version:
        if (
            not latest.present
            or latest.relative_path != intent.actual_relative_path
            or target_sha != latest.sha256
        ):
            raise OperationError("ADD_RECOVERY_STATE_CHANGED")
        if backup_metadata is not None:
            _unlink_owned(
                backup,
                backup_metadata,
                "ADD_RECOVERY_STATE_CHANGED",
                expected_sha=prior_sha,
            )
    else:
        if (
            latest is None
            or not latest.present
            or latest.review_version >= intent.review_version
            or latest.relative_path != prior_relative
            or latest.sha256 != prior_sha
        ):
            raise OperationError("ADD_RECOVERY_STATE_CHANGED")
        if target_sha == intent.sha256:
            if backup_metadata is None or backup_sha != prior_sha:
                raise OperationError("ADD_RECOVERY_STATE_CHANGED")
            assert target_metadata is not None
            _unlink_owned(
                target,
                target_metadata,
                "ADD_RECOVERY_STATE_CHANGED",
                expected_sha=intent.sha256,
            )
            _link_no_clobber(backup, backup_metadata, target, prior_sha)
            target_metadata = _lstat(target, "ADD_RECOVERY_STATE_CHANGED")
            target_sha = prior_sha
        elif target_sha is None:
            if backup_metadata is None or backup_sha != prior_sha:
                raise OperationError("ADD_RECOVERY_STATE_CHANGED")
            _ensure_parents(root, intent.actual_relative_path)
            _link_no_clobber(backup, backup_metadata, target, prior_sha)
            target_metadata = _lstat(target, "ADD_RECOVERY_STATE_CHANGED")
            target_sha = prior_sha
        elif target_sha != prior_sha:
            raise OperationError("ADD_RECOVERY_STATE_CHANGED")
        if target_metadata is None or target_sha != prior_sha:
            raise OperationError("ADD_RECOVERY_STATE_CHANGED")
        if backup_metadata is not None:
            _unlink_owned(
                backup,
                backup_metadata,
                "ADD_RECOVERY_STATE_CHANGED",
                expected_sha=prior_sha,
            )

    if staging_owned is not None:
        _unlink_owned(
            staging,
            staging_owned,
            "ADD_RECOVERY_STATE_CHANGED",
            expected_sha=intent.sha256,
        )
    if not _clear_add_intent(index, intent):
        raise OperationError("ADD_RECOVERY_STATE_CHANGED")


def reconcile_older_add_intents(
    root: Path, row: ManifestRow, index: SyncIndex
) -> None:
    """Resolve abandoned older replacements before a newer ADD proceeds.

    Callers hold the root state lock, so intent enumeration, rollback/cleanup,
    and the subsequent generation check form one serialized state transition.
    """

    _validated_row_path(row, "ADD")
    safe_root = _validated_root(root, index)
    intents = _candidate_add_intents(index, row)
    for intent in intents:
        if intent.review_version >= row.review_version:
            continue
        _reconcile_superseded_replacement_intent(safe_root, index, intent)


def _recover_add(root: Path, row: ManifestRow, index: SyncIndex) -> SyncResult | None:
    target_relative, _previous = _validated_row_path(row, "ADD")
    stored = _index_exact(index, row)
    intent = _add_intent(index, row)

    if intent is not None and intent.prior_relative_path is not None:
        return _recover_managed_replacement(root, row, index, intent)

    staging_relative = target_relative.with_name(target_relative.name + ".part")
    staging_lexical = root.joinpath(*staging_relative.parts)
    staging_metadata = _lstat(staging_lexical, "STAGING_FILE_UNSAFE")
    exact_metadata = _lstat(root.joinpath(*target_relative.parts), "TARGET_UNSAFE")
    staging: Path | None = None
    staging_image: tuple[str, str, int, int, int, str, os.stat_result] | None = None
    if staging_metadata is not None and (
        intent is not None or exact_metadata is not None or stored is not None
    ):
        staging = _resolved_path(root, staging_relative)
        staging_image = _read_recovery_image(staging, None)

    target_image: tuple[str, str, int, int, int, str, os.stat_result]
    if exact_metadata is not None:
        target = _resolved_path(root, target_relative)
        target_image = _read_recovery_image(target, target_relative)
        actual_relative = _decoded_relative(target_relative, target_image[5])
        if staging_image is not None and staging_image[0] != target_image[0]:
            raise OperationError("ADD_RECOVERY_CONFLICT")
    elif stored is not None:
        if not _stored_path_allowed(row, stored.relative_path):
            raise OperationError("COMPLETED_STATE_STALE")
        actual_relative = stored.relative_path
        target = _resolved_path(root, actual_relative)
        if _lstat(target, "COMPLETED_STATE_STALE") is None:
            raise OperationError("COMPLETED_STATE_STALE")
        target_image = _read_recovery_image(target, actual_relative)
    elif intent is not None:
        if (
            intent.batch_id != row.batch_id
            or intent.target_relative_path != target_relative
        ):
            raise OperationError("ADD_RECOVERY_INTENT_CONFLICT")
        actual_relative = intent.actual_relative_path
        target = _ensure_parents(root, actual_relative)
        target_metadata = _lstat(target, "TARGET_UNSAFE")
        if target_metadata is not None:
            target_image = _read_recovery_image(target, actual_relative)
        else:
            if staging is None or staging_image is None:
                cleared = _clear_add_intent(index, intent)
                current_intent = _add_intent(index, row)
                current_stored = _index_exact(index, row)
                current_target = _lstat(target, "TARGET_UNSAFE")
                current_exact = _lstat(
                    root.joinpath(*target_relative.parts), "TARGET_UNSAFE"
                )
                current_staging = _lstat(
                    staging_lexical, "STAGING_FILE_UNSAFE"
                )
                if current_intent is not None:
                    raise OperationError("ADD_RECOVERY_INTENT_CONFLICT")
                if (
                    current_stored is not None
                    or current_target is not None
                    or current_exact is not None
                    or current_staging is not None
                ):
                    raise OperationError("ADD_RECOVERY_STATE_CHANGED")
                if cleared or current_intent is None:
                    return None
                raise OperationError("ADD_RECOVERY_STATE_CHANGED")
            if (
                staging_image[0] != intent.sha256
                or staging_image[1] != intent.perceptual_hash
                or _decoded_relative(target_relative, staging_image[5]) != actual_relative
            ):
                raise OperationError("ADD_RECOVERY_INTENT_CONFLICT")
            _link_no_clobber(staging, staging_image[6], target, staging_image[0])
            target_image = _read_recovery_image(target, actual_relative)
    else:
        return None

    if row.previous_relative_path is not None and _same_windows_path(
        row.previous_relative_path, actual_relative
    ):
        if stored is None and intent is None:
            return None
        if stored is None:
            raise OperationError("ADD_TARGET_COLLIDES_PREVIOUS")

    sha256, phash, _byte_count, _width, _height, _suffix, _owned = target_image
    if intent is not None and (
        intent.actual_relative_path != actual_relative
        or intent.sha256 != sha256
        or intent.perceptual_hash != phash
    ):
        raise OperationError("ADD_RECOVERY_INTENT_CONFLICT")
    cleanup_staging = stored is None
    if stored is not None:
        if (
            stored.relative_path != actual_relative
            or stored.sha256 != sha256
            or stored.perceptual_hash != phash
        ):
            raise OperationError("COMPLETED_STATE_STALE")
        cleanup_staging = staging_image is not None and (
            staging_image[0] == stored.sha256
            and staging_image[1] == stored.perceptual_hash
            and _decoded_relative(target_relative, staging_image[5])
            == stored.relative_path
        )
    if stored is None:
        result = _desired_result(row, actual_relative, sha256, phash)
    else:
        result = _skipped_result(stored)
    _cleanup_composite_previous(root, row, actual_relative, index)
    if stored is None:
        _record(index, result)
    if staging is not None and staging_image is not None and cleanup_staging:
        try:
            _unlink_owned(
                staging, staging_image[6], "FILESYSTEM_OPERATION_FAILED"
            )
        except OperationError:
            pass
    return result


def _apply_relocation(
    root: Path, row: ManifestRow, index: SyncIndex, expected_action: str
) -> SyncResult:
    target_relative, previous_relative = _validated_row_path(row, expected_action)
    skipped = _completed_skip(root, row, index)
    if skipped is not None:
        return skipped
    assert previous_relative is not None
    prior = _latest(index, row)
    if prior is None:
        raise OperationError("SOURCE_STATE_MISSING")
    if not prior.present or prior.relative_path != previous_relative:
        raise OperationError("SOURCE_STATE_MISMATCH")

    source = _resolved_path(root, previous_relative)
    source_metadata = _lstat(source, "SOURCE_UNSAFE")
    if source_metadata is not None:
        source_sha, _source_size, source_owned = _hash_regular(source, "SOURCE_UNSAFE")
        if source_sha != prior.sha256:
            raise OperationError("SOURCE_STATE_MISMATCH")
    else:
        source_owned = None

    target = _ensure_parents(root, target_relative)
    target_metadata = _lstat(target, "TARGET_UNSAFE")
    if target_metadata is not None:
        target_sha, _target_size, _target_owned = _hash_regular(target, "TARGET_UNSAFE")
        if target_sha != prior.sha256:
            raise OperationError("TARGET_CONFLICT")
    elif source_owned is None:
        raise OperationError("SOURCE_STATE_MISSING")
    else:
        _link_no_clobber(source, source_owned, target, prior.sha256)

    if source_owned is not None:
        _unlink_owned(
            source,
            source_owned,
            "FILESYSTEM_OPERATION_FAILED",
            expected_sha=prior.sha256,
        )
    result = _desired_result(
        row, target_relative, prior.sha256, prior.perceptual_hash
    )
    _record(index, result)
    return result


def _run(
    root: Path,
    row: ManifestRow,
    index: SyncIndex,
    operation,
    *,
    expected_action: str,
    logger: OperationLogger | None,
) -> SyncResult:
    setup_failure: OperationError | None = None
    safe_root: Path | None = None
    active_logger: OperationLogger | None = None
    target_relative: PurePosixPath | None = None
    previous_relative: PurePosixPath | None = None
    try:
        target_relative, previous_relative = _validated_row_path(
            row, expected_action
        )
        safe_root = _validated_root(root, index)
        active_logger = _operation_logger(safe_root, row, logger)
    except OperationError as error:
        setup_failure = error
    except Exception:
        setup_failure = OperationError("OPERATION_SETUP_FAILED")
    if setup_failure is not None:
        raise setup_failure from None
    assert safe_root is not None
    assert active_logger is not None
    assert target_relative is not None
    failure: OperationError | None = None
    result: SyncResult | None = None
    try:
        result = operation(safe_root)
    except OperationError as error:
        failure = error
    except Exception:
        failure = OperationError("UNEXPECTED_OPERATION_FAILURE")
    if failure is not None:
        _safe_log(
            active_logger,
            row.candidate_id,
            row.action,
            previous_relative,
            "FAILED",
            target_relative,
            None,
            failure.code,
        )
        raise failure from None
    assert result is not None
    _safe_log(
        active_logger,
        row.candidate_id,
        row.action,
        previous_relative,
        result.status,
        result.relative_path,
        result.sha256,
        None,
    )
    return result


def apply_add(
    root: Path,
    row: ManifestRow,
    download_result: DownloadResult,
    index: SyncIndex,
    *,
    logger: OperationLogger | None = None,
) -> SyncResult:
    """Safely converge one ADD operation and record it in the local index."""

    return _run(
        root,
        row,
        index,
        lambda safe_root: _apply_add(safe_root, row, download_result, index),
        expected_action="ADD",
        logger=logger,
    )


def prepare_add_intent(
    root: Path,
    row: ManifestRow,
    download_result: DownloadResult,
    index: SyncIndex,
    *,
    isolated_staging_path: Path | None = None,
) -> SyncResult | None:
    """Persist exact recovery evidence after download but before cancellation."""

    _validated_row_path(row, "ADD")
    safe_root = _validated_root(root, index)
    target_relative = row.target_relative_path
    actual_relative, _staging, sha256, phash, _owned = _validated_download(
        safe_root,
        row,
        target_relative,
        download_result,
        isolated_staging_path=isolated_staging_path,
    )
    result = _desired_result(row, actual_relative, sha256, phash)
    if row.previous_relative_path is not None and _same_windows_path(
        row.previous_relative_path, actual_relative
    ):
        prior, _target, _target_owned, backup_relative = (
            _managed_replacement_evidence(safe_root, row, result, index)
        )
        if result.sha256 == prior.sha256:
            if result.perceptual_hash != prior.perceptual_hash:
                raise OperationError("STAGING_CONTENT_MISMATCH")
            _record(index, result)
            return result
        _record_replacement_intent(index, row, result, prior, backup_relative)
        return None
    target = _ensure_parents(safe_root, actual_relative)
    target_metadata = _lstat(target, "TARGET_UNSAFE")
    if target_metadata is not None and (
        not _regular(target_metadata) or stat.S_ISLNK(target_metadata.st_mode)
    ):
        raise OperationError("TARGET_UNSAFE")
    _record_add_intent(index, result, target_relative)
    return None


def recover_add(
    root: Path,
    row: ManifestRow,
    index: SyncIndex,
    *,
    operation_log: OperationLogger | None = None,
) -> SyncResult | None:
    """Recover an ADD filesystem commit without accessing the network."""

    setup_failure: OperationError | None = None
    safe_root: Path | None = None
    active_logger: OperationLogger | None = None
    target_relative: PurePosixPath | None = None
    previous_relative: PurePosixPath | None = None
    try:
        target_relative, previous_relative = _validated_row_path(row, "ADD")
        safe_root = _validated_root(root, index)
        active_logger = _operation_logger(safe_root, row, operation_log)
    except OperationError as error:
        setup_failure = error
    except Exception:
        setup_failure = OperationError("OPERATION_SETUP_FAILED")
    if setup_failure is not None:
        raise setup_failure from None
    assert safe_root is not None
    assert active_logger is not None
    assert target_relative is not None

    failure: OperationError | None = None
    result: SyncResult | None = None
    try:
        result = _recover_add(safe_root, row, index)
    except OperationError as error:
        failure = error
    except Exception:
        failure = OperationError("UNEXPECTED_OPERATION_FAILURE")
    if failure is not None:
        _safe_log(
            active_logger,
            row.candidate_id,
            row.action,
            previous_relative,
            "FAILED",
            target_relative,
            None,
            failure.code,
        )
        raise failure from None
    if result is not None:
        _safe_log(
            active_logger,
            row.candidate_id,
            row.action,
            previous_relative,
            result.status,
            result.relative_path,
            result.sha256,
            None,
        )
    return result


def apply_move(
    root: Path,
    row: ManifestRow,
    index: SyncIndex,
    *,
    logger: OperationLogger | None = None,
) -> SyncResult:
    """Safely converge one MOVE operation and record it in the local index."""

    return _run(
        root,
        row,
        index,
        lambda safe_root: _apply_relocation(safe_root, row, index, "MOVE"),
        expected_action="MOVE",
        logger=logger,
    )


def apply_remove(
    root: Path,
    row: ManifestRow,
    index: SyncIndex,
    *,
    logger: OperationLogger | None = None,
) -> SyncResult:
    """Safely converge one recoverable REMOVE and record it in the local index."""

    return _run(
        root,
        row,
        index,
        lambda safe_root: _apply_relocation(safe_root, row, index, "REMOVE"),
        expected_action="REMOVE",
        logger=logger,
    )
