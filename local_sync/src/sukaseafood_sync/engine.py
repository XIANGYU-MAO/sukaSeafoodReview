from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import stat
import threading
import time
from typing import Callable, Literal, Protocol
from urllib.parse import urlsplit

import requests

from .downloader import (
    DownloadCancelled,
    DownloadError,
    DownloadPolicy,
    DownloadResult,
    download_image,
)
from .index import SyncIndex, SyncIndexError, SyncResult
from .manifest import ExportManifest, ManifestRow
from .operations import (
    OperationError,
    OperationLogger,
    _ensure_parents,
    apply_add,
    apply_move,
    apply_remove,
    recover_add,
)


class CancelEvent(Protocol):
    def is_set(self) -> bool: ...


ProgressCallback = Callable[["ProgressEvent"], None]
Wait = Callable[[float, CancelEvent], bool]
Downloader = Callable[
    [
        requests.Session,
        ManifestRow,
        Path,
        DownloadPolicy,
        Callable[[int, int | None], None],
        Callable[[], bool],
    ],
    DownloadResult,
]


class SyncEngineError(RuntimeError):
    """A stable, secret-free error raised before row processing can begin."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    current: int
    total: int
    candidate_id: str | None
    species_code: str | None
    phase: str
    message: str
    downloaded_bytes: int | None = None
    total_bytes: int | None = None


def _ignore_progress(_event: ProgressEvent) -> None:
    return None


@dataclass(frozen=True, slots=True)
class SyncCallbacks:
    progress: ProgressCallback = _ignore_progress


@dataclass(frozen=True, slots=True)
class ReceiptItem:
    candidate_id: str
    review_id: str
    review_version: int
    status: Literal["SUCCEEDED", "FAILED"]
    sha256: str | None
    relative_path: str | None
    error: str | None


@dataclass(frozen=True, slots=True)
class BatchResult:
    batch_id: str
    counts: dict[str, int]
    receipt_items: tuple[ReceiptItem, ...]
    cancelled: bool
    processed: int
    total: int
    operation_log_path: Path = field(repr=False)


_WIKIMEDIA_DOMAINS = ("wikimedia.org", "wikipedia.org")
_KNOWN_SOURCE_DOMAINS = {
    "inaturalist": ("inaturalist.org",),
    "gbif": ("gbif.org",),
    "fish-vista": ("fishair.org", "fish-vista.org", "fishvista.org"),
}


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


def _source_bucket(row: ManifestRow) -> tuple[str, float]:
    hosts: list[str] = []
    for value in (row.source_url, row.original_url):
        try:
            host = (urlsplit(value).hostname or "").casefold().rstrip(".")
        except (TypeError, ValueError):
            host = ""
        if host:
            hosts.append(host)
    if any(
        _host_matches(host, domain)
        for host in hosts
        for domain in _WIKIMEDIA_DOMAINS
    ):
        return "wikimedia", 6.5
    for name, domains in _KNOWN_SOURCE_DOMAINS.items():
        if any(
            _host_matches(host, domain)
            for host in hosts
            for domain in domains
        ):
            return name, 1.0
    return f"host:{hosts[0] if hosts else 'unknown'}", 1.0


def _default_wait(delay: float, cancel_event: CancelEvent) -> bool:
    event_wait = getattr(cancel_event, "wait", None)
    if callable(event_wait):
        return bool(event_wait(delay))
    time.sleep(delay)
    return cancel_event.is_set()


def _download_failure_code(error: DownloadError) -> str:
    message = str(error).casefold()
    if "decod" in message or "image format" in message:
        return "INVALID_IMAGE"
    if any(
        marker in message
        for marker in ("network", "request", "http status", "redirect")
    ):
        return "NETWORK_ERROR"
    return "DOWNLOAD_ERROR"


def _operation_failure_code(error: OperationError) -> str:
    if error.code.startswith("INDEX_"):
        return "INDEX_ERROR"
    return "OPERATION_ERROR"


class SyncEngine:
    """Run one manifest serially while preserving durable per-row progress."""

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        session_factory: Callable[[], requests.Session] | None = None,
        policy: DownloadPolicy | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        wait: Wait | None = None,
        downloader: Downloader | None = None,
    ) -> None:
        self._session = session
        self._session_factory = session_factory or requests.Session
        self._policy = policy or DownloadPolicy()
        self._monotonic = monotonic
        self._wait = wait or _default_wait
        self._downloader = downloader or download_image
        self._progress_lock = threading.RLock()

    def _emit(
        self,
        callbacks: SyncCallbacks,
        *,
        current: int,
        total: int,
        row: ManifestRow | None,
        phase: str,
        downloaded_bytes: int | None = None,
        total_bytes: int | None = None,
    ) -> None:
        event = ProgressEvent(
            current=current,
            total=total,
            candidate_id=str(row.candidate_id) if row is not None else None,
            species_code=row.species_code if row is not None else None,
            phase=phase,
            message=f"sync.{phase.casefold()}",
            downloaded_bytes=downloaded_bytes,
            total_bytes=total_bytes,
        )
        try:
            with self._progress_lock:
                callbacks.progress(event)
        except Exception:
            pass

    @staticmethod
    def _validate_setup(
        manifest: ExportManifest,
        root: Path,
        callbacks: SyncCallbacks,
        cancel_event: CancelEvent,
    ) -> Path:
        if not isinstance(manifest, ExportManifest) or not manifest.rows:
            raise SyncEngineError("INVALID_MANIFEST")
        if any(
            not isinstance(row, ManifestRow) or row.batch_id != manifest.batch_id
            for row in manifest.rows
        ):
            raise SyncEngineError("MANIFEST_BATCH_MISMATCH")
        if any(row.action not in {"ADD", "MOVE", "REMOVE"} for row in manifest.rows):
            raise SyncEngineError("INVALID_MANIFEST")
        if not isinstance(callbacks, SyncCallbacks) or not callable(callbacks.progress):
            raise SyncEngineError("INVALID_CALLBACKS")
        if not callable(getattr(cancel_event, "is_set", None)):
            raise SyncEngineError("INVALID_CANCEL_EVENT")
        root_invalid = False
        selected = Path()
        try:
            selected = Path(root)
        except (TypeError, ValueError):
            root_invalid = True
        if root_invalid:
            raise SyncEngineError("ROOT_UNSAFE") from None
        metadata: os.stat_result | None = None
        missing = False
        try:
            metadata = os.lstat(selected)
        except FileNotFoundError:
            missing = True
        except OSError:
            root_invalid = True
        if missing:
            return selected
        if root_invalid or metadata is None:
            raise SyncEngineError("ROOT_UNSAFE") from None
        resolved = selected
        try:
            resolved = selected.resolve(strict=True)
        except (OSError, RuntimeError):
            root_invalid = True
        if root_invalid:
            raise SyncEngineError("ROOT_UNSAFE") from None
        attributes = getattr(metadata, "st_file_attributes", 0) if metadata else 0
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or attributes & reparse
        ):
            raise SyncEngineError("ROOT_UNSAFE")
        return resolved

    @staticmethod
    def _receipt(row: ManifestRow, result: SyncResult) -> ReceiptItem:
        return ReceiptItem(
            candidate_id=str(row.candidate_id),
            review_id=str(row.review_id),
            review_version=row.review_version,
            status="SUCCEEDED",
            sha256=result.sha256,
            relative_path=result.relative_path.as_posix(),
            error=None,
        )

    @staticmethod
    def _failed_receipt(row: ManifestRow, code: str) -> ReceiptItem:
        return ReceiptItem(
            candidate_id=str(row.candidate_id),
            review_id=str(row.review_id),
            review_version=row.review_version,
            status="FAILED",
            sha256=None,
            relative_path=None,
            error=code,
        )

    @staticmethod
    def _safe_failure_log(
        logger: OperationLogger, row: ManifestRow, code: str
    ) -> None:
        try:
            logger.append(
                candidate_id=row.candidate_id,
                action=row.action,
                status="FAILED",
                previous_relative_path=row.previous_relative_path,
                relative_path=row.target_relative_path,
                sha256=None,
                error=code,
            )
        except Exception:
            pass

    def run(
        self,
        manifest: ExportManifest,
        root: Path,
        callbacks: SyncCallbacks,
        cancel_event: CancelEvent,
    ) -> BatchResult:
        safe_root = self._validate_setup(manifest, root, callbacks, cancel_event)
        setup_code: str | None = None
        index: SyncIndex | None = None
        logger: OperationLogger | None = None
        try:
            index = SyncIndex(safe_root)
            safe_root = index.root
            logger = OperationLogger(safe_root, manifest.batch_id)
            logger.validate()
        except SyncIndexError:
            setup_code = "INDEX_SETUP_ERROR"
        except OperationError:
            setup_code = "OPERATION_LOG_SETUP_ERROR"
        except Exception:
            setup_code = "BATCH_SETUP_ERROR"
        if setup_code is not None:
            raise SyncEngineError(setup_code) from None
        assert index is not None
        assert logger is not None

        owned_session = self._session is None
        session: requests.Session | None = self._session
        if session is None:
            try:
                session = self._session_factory()
            except Exception:
                setup_code = "SESSION_SETUP_ERROR"
            if setup_code is not None:
                raise SyncEngineError(setup_code) from None
        assert session is not None

        counts = {"succeeded": 0, "failed": 0, "skipped": 0}
        receipts: list[ReceiptItem] = []
        total = len(manifest.rows)
        cancelled = False
        last_activity: dict[str, float] = {}

        try:
            for current, row in enumerate(manifest.rows, start=1):
                if cancel_event.is_set():
                    cancelled = True
                    self._emit(
                        callbacks, current=current, total=total, row=row, phase="CANCELLED"
                    )
                    break

                result: SyncResult | None = None
                operation_started = False
                failure_code: str | None = None
                try:
                    if row.action == "ADD":
                        self._emit(
                            callbacks,
                            current=current,
                            total=total,
                            row=row,
                            phase="RECOVERING",
                        )
                        operation_started = True
                        result = recover_add(
                            safe_root, row, index, operation_log=logger
                        )
                        operation_started = False
                        if result is None:
                            bucket, interval = _source_bucket(row)
                            previous = last_activity.get(bucket)
                            if previous is not None:
                                delay = max(
                                    0.0,
                                    interval - (self._monotonic() - previous),
                                )
                                if delay > 0:
                                    self._emit(
                                        callbacks,
                                        current=current,
                                        total=total,
                                        row=row,
                                        phase="WAITING",
                                    )
                                    if self._wait(delay, cancel_event) or cancel_event.is_set():
                                        cancelled = True
                                        self._emit(
                                            callbacks,
                                            current=current,
                                            total=total,
                                            row=row,
                                            phase="CANCELLED",
                                        )
                                        break
                            if cancel_event.is_set():
                                cancelled = True
                                self._emit(
                                    callbacks,
                                    current=current,
                                    total=total,
                                    row=row,
                                    phase="CANCELLED",
                                )
                                break
                            destination = _ensure_parents(
                                safe_root, row.target_relative_path
                            )
                            self._emit(
                                callbacks,
                                current=current,
                                total=total,
                                row=row,
                                phase="DOWNLOADING",
                            )

                            def download_progress(
                                downloaded: int, expected: int | None
                            ) -> None:
                                self._emit(
                                    callbacks,
                                    current=current,
                                    total=total,
                                    row=row,
                                    phase="DOWNLOADING",
                                    downloaded_bytes=downloaded,
                                    total_bytes=expected,
                                )

                            try:
                                downloaded = self._downloader(
                                    session,
                                    row,
                                    destination,
                                    self._policy,
                                    download_progress,
                                    cancel_event.is_set,
                                )
                            finally:
                                last_activity[bucket] = self._monotonic()
                            if cancel_event.is_set():
                                cancelled = True
                                self._emit(
                                    callbacks,
                                    current=current,
                                    total=total,
                                    row=row,
                                    phase="CANCELLED",
                                )
                                break
                            self._emit(
                                callbacks,
                                current=current,
                                total=total,
                                row=row,
                                phase="APPLYING",
                            )
                            operation_started = True
                            result = apply_add(
                                safe_root, row, downloaded, index, logger=logger
                            )
                    elif row.action == "MOVE":
                        self._emit(
                            callbacks,
                            current=current,
                            total=total,
                            row=row,
                            phase="APPLYING",
                        )
                        operation_started = True
                        result = apply_move(safe_root, row, index, logger=logger)
                    else:
                        self._emit(
                            callbacks,
                            current=current,
                            total=total,
                            row=row,
                            phase="APPLYING",
                        )
                        operation_started = True
                        result = apply_remove(safe_root, row, index, logger=logger)
                except DownloadCancelled:
                    cancelled = True
                    self._emit(
                        callbacks, current=current, total=total, row=row, phase="CANCELLED"
                    )
                    break
                except DownloadError as error:
                    failure_code = _download_failure_code(error)
                except OperationError as error:
                    failure_code = _operation_failure_code(error)
                except SyncIndexError:
                    failure_code = "INDEX_ERROR"
                except OSError:
                    failure_code = "FILESYSTEM_ERROR"
                except Exception:
                    failure_code = "UNEXPECTED_ERROR"

                if failure_code is not None:
                    if not operation_started:
                        self._safe_failure_log(logger, row, failure_code)
                    counts["failed"] += 1
                    receipts.append(self._failed_receipt(row, failure_code))
                    self._emit(
                        callbacks, current=current, total=total, row=row, phase="FAILED"
                    )
                    continue
                if cancelled:
                    break
                assert result is not None
                if result.status == "SKIPPED_ALREADY_COMPLETED":
                    counts["skipped"] += 1
                    terminal = "SKIPPED"
                else:
                    counts["succeeded"] += 1
                    terminal = "SUCCEEDED"
                receipts.append(self._receipt(row, result))
                self._emit(
                    callbacks, current=current, total=total, row=row, phase=terminal
                )
        finally:
            if owned_session:
                try:
                    session.close()
                except Exception:
                    pass

        if not cancelled:
            self._emit(
                callbacks,
                current=len(receipts),
                total=total,
                row=None,
                phase="COMPLETED",
            )
        return BatchResult(
            batch_id=str(manifest.batch_id),
            counts=counts,
            receipt_items=tuple(receipts),
            cancelled=cancelled,
            processed=len(receipts),
            total=total,
            operation_log_path=logger.path,
        )
