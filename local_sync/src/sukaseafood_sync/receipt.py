from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from typing import Callable, Literal
from urllib.parse import urlsplit
from uuid import UUID

import requests

from .engine import BatchResult, ReceiptItem
from .index import SyncIndex
from .manifest import (
    ExportManifest,
    MAX_MANIFEST_BYTES,
    ManifestError,
    SUPPORTED_SUFFIXES,
    _TOKEN_PATTERN,
    validate_relative_path,
)


_ONLINE_ITEM_KEYS = (
    "candidate_id",
    "review_id",
    "review_version",
    "status",
    "sha256",
    "relative_path",
    "error",
)
_COUNT_KEYS = frozenset({"succeeded", "failed", "skipped"})
_ERROR_CODE_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z", re.ASCII)
_LOWER_HEX_64 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_DNS_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z", re.ASCII)
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
_AUTHENTICATION_STATUSES = frozenset({401, 403})
_VALIDATION_STATUSES = frozenset({400, 404, 422})
_MAX_RESPONSE_BYTES = 1024 * 1024
MAX_RECEIPT_FILE_BYTES = MAX_MANIFEST_BYTES
MAX_RECEIPT_JSON_DEPTH = 8
_REPARSE_POINT = 0x400


class ReceiptError(ValueError):
    """A stable, secret-free receipt construction or file error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class Receipt:
    batch_id: UUID
    items: tuple[ReceiptItem, ...]
    _item_actions: tuple[tuple[str, str, int, str], ...] = field(repr=False)
    manifest_candidate_ids: tuple[str, ...] = field(repr=False)

    def __post_init__(self) -> None:
        _require_valid_receipt(self)

    @staticmethod
    def _serialize_item(item: ReceiptItem) -> dict[str, object]:
        return {key: getattr(item, key) for key in _ONLINE_ITEM_KEYS}

    def to_dict(self) -> dict[str, object]:
        """Return the exact online request body (without batch ID)."""

        _require_valid_receipt(self)
        return {"items": [self._serialize_item(item) for item in self.items]}

    def to_file_dict(self) -> dict[str, object]:
        """Return the exact offline/admin-upload receipt schema."""

        _require_valid_receipt(self)
        return {"batch_id": str(self.batch_id), **self.to_dict()}


@dataclass(frozen=True, slots=True)
class SubmitResult:
    submitted: bool
    code: str
    status: Literal["pending", "completed"] | None = None
    accepted_candidate_ids: tuple[str, ...] = ()
    pending_candidate_ids: tuple[str, ...] = ()
    attempts: int = 0
    retryable: bool = False
    manual_action: bool = False
    index_update_failed: bool = False


def _canonical_uuid(value: object) -> str | None:
    if type(value) is not str:
        return None
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        return None
    return value if str(parsed) == value else None


def _valid_item(item: object) -> bool:
    if type(item) is not ReceiptItem:
        return False
    if _canonical_uuid(item.candidate_id) is None:
        return False
    if _canonical_uuid(item.review_id) is None:
        return False
    if type(item.review_version) is not int:
        return False
    if item.review_version < 1 or item.review_version > 2**63 - 1:
        return False
    if type(item.status) is not str:
        return False
    if item.status == "SUCCEEDED":
        if type(item.sha256) is not str or _LOWER_HEX_64.fullmatch(item.sha256) is None:
            return False
        if item.error is not None or type(item.relative_path) is not str:
            return False
        try:
            relative = validate_relative_path(item.relative_path, "relative_path")
        except (ManifestError, TypeError, ValueError):
            return False
        if not item.relative_path.strip() or relative.as_posix() != item.relative_path:
            return False
        return True
    if item.status == "FAILED":
        return (
            item.sha256 is None
            and item.relative_path is None
            and type(item.error) is str
            and _ERROR_CODE_PATTERN.fullmatch(item.error) is not None
        )
    return False


def _valid_receipt(receipt: object) -> bool:
    if type(receipt) is not Receipt:
        return False
    if type(receipt.batch_id) is not UUID:
        return False
    if type(receipt.items) is not tuple or not 1 <= len(receipt.items) <= 10_000:
        return False
    if type(receipt._item_actions) is not tuple or len(receipt._item_actions) != len(receipt.items):
        return False
    if type(receipt.manifest_candidate_ids) is not tuple:
        return False
    if not len(receipt.items) <= len(receipt.manifest_candidate_ids) <= 10_000:
        return False

    item_triples: list[tuple[str, str, int]] = []
    seen_triples: set[tuple[str, str, int]] = set()
    for receipt_item in receipt.items:
        if not _valid_item(receipt_item):
            return False
        triple = (
            receipt_item.candidate_id,
            receipt_item.review_id,
            receipt_item.review_version,
        )
        if triple in seen_triples:
            return False
        seen_triples.add(triple)
        item_triples.append(triple)

    for expected, mapping in zip(item_triples, receipt._item_actions, strict=True):
        if type(mapping) is not tuple or len(mapping) != 4:
            return False
        candidate_id, review_id, review_version, action = mapping
        if (
            type(candidate_id) is not str
            or type(review_id) is not str
            or type(review_version) is not int
            or type(action) is not str
            or _canonical_uuid(candidate_id) is None
            or _canonical_uuid(review_id) is None
            or not 1 <= review_version <= 2**63 - 1
            or action not in {"ADD", "MOVE", "REMOVE"}
            or (candidate_id, review_id, review_version) != expected
        ):
            return False

    seen_candidates: set[str] = set()
    for candidate_id in receipt.manifest_candidate_ids:
        if (
            type(candidate_id) is not str
            or _canonical_uuid(candidate_id) is None
            or candidate_id in seen_candidates
        ):
            return False
        seen_candidates.add(candidate_id)
    return tuple(item[0] for item in item_triples) == receipt.manifest_candidate_ids[: len(item_triples)]


def _require_valid_receipt(receipt: object) -> Receipt:
    if not _valid_receipt(receipt):
        raise ReceiptError("INVALID_RECEIPT")
    return receipt


def build_receipt(manifest: ExportManifest, batch_result: BatchResult) -> Receipt:
    """Validate an engine result against its manifest and build a safe receipt."""

    if not isinstance(manifest, ExportManifest) or not isinstance(batch_result, BatchResult):
        raise ReceiptError("INVALID_INPUT")
    batch_id = _canonical_uuid(batch_result.batch_id)
    if type(manifest.batch_id) is not UUID or batch_id is None or batch_id != str(manifest.batch_id):
        raise ReceiptError("BATCH_MISMATCH")
    rows = manifest.rows
    items = batch_result.receipt_items
    if not items:
        raise ReceiptError("EMPTY_RECEIPT")
    counts = batch_result.counts
    valid_counts = (
        isinstance(counts, dict)
        and set(counts) == _COUNT_KEYS
        and all(
            isinstance(value, int) and not isinstance(value, bool) and value >= 0
            for value in counts.values()
        )
    )
    if (
        not valid_counts
        or type(batch_result.processed) is not int
        or type(batch_result.total) is not int
        or type(batch_result.cancelled) is not bool
        or batch_result.processed < 0
        or batch_result.total < 0
        or batch_result.total != len(rows)
        or batch_result.processed != len(items)
        or batch_result.processed > batch_result.total
        or sum(counts.values()) != batch_result.processed
        or (not batch_result.cancelled and batch_result.processed != batch_result.total)
    ):
        raise ReceiptError("COUNT_MISMATCH")
    seen: set[tuple[str, str, int]] = set()
    actions: list[tuple[str, str, int, str]] = []
    for position, item in enumerate(items):
        if not _valid_item(item):
            raise ReceiptError("INVALID_ITEM")
        triple = (item.candidate_id, item.review_id, item.review_version)
        if triple in seen:
            raise ReceiptError("DUPLICATE_ITEM")
        seen.add(triple)
        if position >= len(rows):
            raise ReceiptError("ITEM_MISMATCH")
        row = rows[position]
        expected = (str(row.candidate_id), str(row.review_id), row.review_version)
        if triple != expected or row.batch_id != manifest.batch_id:
            raise ReceiptError("ITEM_MISMATCH")
        actions.append((*triple, row.action))
    failed = sum(entry.status == "FAILED" for entry in items)
    succeeded = sum(entry.status == "SUCCEEDED" for entry in items)
    if counts["failed"] != failed or counts["succeeded"] + counts["skipped"] != succeeded:
        raise ReceiptError("COUNT_MISMATCH")
    manifest_candidates = tuple(str(row.candidate_id) for row in rows)
    if len(set(manifest_candidates)) != len(manifest_candidates):
        raise ReceiptError("DUPLICATE_ITEM")
    return Receipt(manifest.batch_id, tuple(items), tuple(actions), manifest_candidates)


def _valid_origin_host(host: str, netloc: str, port: int | None) -> bool:
    if (
        not host
        or "%" in netloc
        or any(ord(character) < 32 or ord(character) == 127 for character in netloc)
        or port == 0
    ):
        return False
    if ":" in host:
        closing = netloc.find("]")
        if not netloc.startswith("[") or closing < 0:
            return False
        suffix = netloc[closing + 1 :]
        if suffix and (not suffix.startswith(":") or not suffix[1:].isdigit()):
            return False
        try:
            ipaddress.IPv6Address(host)
        except ipaddress.AddressValueError:
            return False
        return True
    if "[" in netloc or "]" in netloc:
        return False
    if netloc.endswith(":"):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        return isinstance(address, ipaddress.IPv4Address) and str(address) == host
    if all(character in "0123456789." for character in host):
        return False
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        return False
    if not ascii_host or len(ascii_host) > 253:
        return False
    labels = ascii_host.split(".")
    return all(_DNS_LABEL.fullmatch(label) is not None for label in labels)


def _validated_api_base(api_base: object) -> str | None:
    if (
        not isinstance(api_base, str)
        or not api_base
        or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in api_base)
    ):
        return None
    try:
        parsed = urlsplit(api_base)
        port = parsed.port
        host = parsed.hostname
    except (TypeError, ValueError):
        return None
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.netloc
        or host is None
        or parsed.path not in {"/sukaseafood/api/v1", "/sukaseafood/api/v1/"}
        or not _valid_origin_host(host, parsed.netloc, port)
    ):
        return None
    scheme = parsed.scheme.lower()
    if scheme == "https":
        pass
    elif scheme == "http" and host.casefold() in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        pass
    else:
        return None
    return api_base.rstrip("/")


def validate_api_base(api_base: object) -> str:
    """Return the canonical receipt API base or raise a stable safe error."""

    validated = _validated_api_base(api_base)
    if validated is None:
        raise ReceiptError("INVALID_API_BASE")
    return validated


def _safe_result(
    code: str,
    attempts: int,
    *,
    submitted: bool = False,
    status: Literal["pending", "completed"] | None = None,
    accepted: tuple[str, ...] = (),
    pending: tuple[str, ...] = (),
    retryable: bool = False,
    manual_action: bool = False,
    index_update_failed: bool = False,
) -> SubmitResult:
    return SubmitResult(
        submitted=submitted,
        code=code,
        status=status,
        accepted_candidate_ids=accepted,
        pending_candidate_ids=pending,
        attempts=attempts,
        retryable=retryable,
        manual_action=manual_action,
        index_update_failed=index_update_failed,
    )


def _retry_delay(
    response: object | None,
    fallback: float,
    now: Callable[[], datetime],
) -> float:
    value = None
    headers = getattr(response, "headers", None)
    if isinstance(headers, dict) or hasattr(headers, "get"):
        value = headers.get("Retry-After")
    delay: float | None = None
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            delay = float(stripped)
        else:
            try:
                retry_at = parsedate_to_datetime(stripped)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                current = now()
                if current.tzinfo is None:
                    current = current.replace(tzinfo=timezone.utc)
                delay = (retry_at - current).total_seconds()
            except (TypeError, ValueError, OverflowError):
                delay = None
    if delay is None or not math.isfinite(delay) or delay < 0:
        delay = fallback
    return min(60.0, max(0.0, delay))


def _parse_success(receipt: Receipt, content: object) -> tuple[str, tuple[str, ...], tuple[str, ...]] | None:
    if not isinstance(content, (bytes, bytearray)) or len(content) > _MAX_RESPONSE_BYTES:
        return None
    try:
        payload = json.loads(bytes(content).decode("utf-8"))
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        RecursionError,
        OverflowError,
    ):
        return None
    expected_keys = {
        "batch_id",
        "status",
        "accepted_candidate_ids",
        "pending_candidate_ids",
    }
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        return None
    if payload["batch_id"] != str(receipt.batch_id):
        return None
    status = payload["status"]
    if status not in {"pending", "completed"}:
        return None
    accepted_raw = payload["accepted_candidate_ids"]
    pending_raw = payload["pending_candidate_ids"]
    if not isinstance(accepted_raw, list) or not isinstance(pending_raw, list):
        return None
    accepted: list[str] = []
    pending: list[str] = []
    for raw, destination in ((accepted_raw, accepted), (pending_raw, pending)):
        for candidate in raw:
            canonical = _canonical_uuid(candidate)
            if canonical is None or canonical in destination:
                return None
            destination.append(canonical)
    accepted_tuple = tuple(accepted)
    pending_tuple = tuple(pending)
    if set(accepted_tuple).intersection(pending_tuple):
        return None
    succeeded_candidates = {
        entry.candidate_id for entry in receipt.items if entry.status == "SUCCEEDED"
    }
    manifest_candidates = set(receipt.manifest_candidate_ids)
    if not set(accepted_tuple).issubset(succeeded_candidates):
        return None
    if not set(pending_tuple).issubset(manifest_candidates):
        return None
    return status, accepted_tuple, pending_tuple


def _read_bounded_response(response: object) -> bytes | None:
    content = bytearray()
    try:
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not isinstance(chunk, bytes):
                return None
            content.extend(chunk)
            if len(content) > _MAX_RESPONSE_BYTES:
                return None
    except (requests.ConnectionError, requests.Timeout):
        raise
    except Exception:
        return None
    return bytes(content)


def _mark_accepted(
    receipt: Receipt,
    accepted: tuple[str, ...],
    index: SyncIndex,
) -> bool:
    mapping = {entry[0]: entry for entry in receipt._item_actions}
    try:
        for candidate_id in accepted:
            candidate, review, version, action = mapping[candidate_id]
            index.mark_receipt_submitted(candidate, review, version, action)
    except Exception:
        return False
    return True


def submit_receipt(
    receipt: Receipt,
    api_base: str,
    token: str,
    timeout: float,
    *,
    session: requests.Session | None = None,
    index: SyncIndex | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    cancelled: Callable[[], bool] = lambda: False,
) -> SubmitResult:
    """Submit a receipt without merging origin session credentials or cookies."""

    if not _valid_receipt(receipt):
        return _safe_result("INVALID_RECEIPT", 0)
    base = _validated_api_base(api_base)
    if base is None:
        return _safe_result("INVALID_API_BASE", 0)
    if not isinstance(token, str) or _TOKEN_PATTERN.fullmatch(token) is None:
        return _safe_result("INVALID_TOKEN", 0)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0 or not math.isfinite(timeout):
        return _safe_result("INVALID_TIMEOUT", 0)
    try:
        if cancelled():
            return _safe_result("CANCELLED", 0)
    except Exception:
        return _safe_result("CANCELLED", 0)

    owned = session is None
    transport = requests.Session() if owned else session
    assert transport is not None
    transport.trust_env = True
    url = f"{base}/sync/batches/{receipt.batch_id}/receipt"
    body = json.dumps(receipt.to_dict(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        prepared_request = requests.Request(
            "POST",
            url,
            data=body,
            headers={
                "Authorization": f"Batch {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        ).prepare()
    except Exception:
        if owned:
            transport.close()
        return _safe_result("INVALID_API_BASE", 0)
    attempts = 0
    try:
        for attempts in range(1, 4):
            try:
                if cancelled():
                    return _safe_result("CANCELLED", attempts - 1)
            except Exception:
                return _safe_result("CANCELLED", attempts - 1)
            response = None
            try:
                settings = transport.merge_environment_settings(
                    prepared_request.url,
                    {},
                    False,
                    transport.verify,
                    transport.cert,
                )
                settings["stream"] = True
                response = transport.send(
                    prepared_request,
                    timeout=timeout,
                    allow_redirects=False,
                    **settings,
                )
            except (requests.ConnectionError, requests.Timeout):
                if attempts == 3:
                    return _safe_result("RETRY_EXHAUSTED", attempts, retryable=True)
                sleep(float(attempts))
                try:
                    if cancelled():
                        return _safe_result("CANCELLED", attempts)
                except Exception:
                    return _safe_result("CANCELLED", attempts)
                continue
            except requests.RequestException:
                return _safe_result("TRANSPORT_FAILED", attempts)
            except Exception:
                return _safe_result("TRANSPORT_FAILED", attempts)

            retry_delay: float | None = None
            try:
                try:
                    status_code = int(response.status_code)
                except (TypeError, ValueError, OverflowError):
                    return _safe_result("MALFORMED_RESPONSE", attempts)
                if status_code == 200:
                    try:
                        content = _read_bounded_response(response)
                    except (requests.ConnectionError, requests.Timeout):
                        if attempts == 3:
                            return _safe_result("RETRY_EXHAUSTED", attempts, retryable=True)
                        retry_delay = float(attempts)
                        content = None
                    if retry_delay is not None:
                        pass
                    else:
                        parsed = _parse_success(receipt, content)
                        if parsed is None:
                            return _safe_result("MALFORMED_RESPONSE", attempts)
                        status, accepted, pending = parsed
                        if index is not None and not _mark_accepted(receipt, accepted, index):
                            return _safe_result(
                                "INDEX_UPDATE_FAILED",
                                attempts,
                                submitted=True,
                                status=status,  # type: ignore[arg-type]
                                accepted=accepted,
                                pending=pending,
                                manual_action=True,
                                index_update_failed=True,
                            )
                        return _safe_result(
                            "SUBMITTED",
                            attempts,
                            submitted=True,
                            status=status,  # type: ignore[arg-type]
                            accepted=accepted,
                            pending=pending,
                        )
                if status_code in _RETRYABLE_STATUSES:
                    if attempts == 3:
                        return _safe_result("RETRY_EXHAUSTED", attempts, retryable=True)
                    retry_delay = _retry_delay(response, float(attempts), now)
                if 300 <= status_code < 400:
                    return _safe_result("REDIRECT_REJECTED", attempts)
                if status_code in _AUTHENTICATION_STATUSES:
                    return _safe_result("AUTHENTICATION_FAILED", attempts)
                if status_code in _VALIDATION_STATUSES:
                    return _safe_result("VALIDATION_FAILED", attempts)
                if status_code == 409:
                    return _safe_result("CONFLICT", attempts, manual_action=True)
                if retry_delay is None:
                    return _safe_result("HTTP_FAILED", attempts)
            finally:
                try:
                    response.close()
                except Exception:
                    pass
            assert retry_delay is not None
            sleep(retry_delay)
            try:
                if cancelled():
                    return _safe_result("CANCELLED", attempts)
            except Exception:
                return _safe_result("CANCELLED", attempts)
            continue
        return _safe_result("RETRY_EXHAUSTED", attempts, retryable=True)
    finally:
        if owned:
            transport.close()


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT)


def _validate_parent(parent: Path) -> bool:
    try:
        absolute = Path(os.path.abspath(parent))
        resolved = parent.resolve(strict=True)
        metadata = parent.lstat()
    except (OSError, RuntimeError, ValueError):
        return False
    if absolute != resolved or not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata):
        return False
    return True


def _safe_target(target: Path) -> bool:
    if target.suffix != ".json" or not target.name or any(ord(character) < 32 for character in target.name):
        return False
    try:
        metadata = target.lstat()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode) and not _is_reparse(metadata)


def prepare_receipt_directory(
    path: str | os.PathLike[str],
    *,
    create: bool,
) -> Path:
    """Validate a safe receipt directory and optionally create missing components."""

    try:
        requested = Path(path)
    except (TypeError, ValueError):
        raise ReceiptError("UNSAFE_RECEIPT_DIRECTORY") from None
    try:
        requested.lstat()
    except FileNotFoundError:
        cursor = requested.parent
        while True:
            try:
                cursor.lstat()
                break
            except FileNotFoundError:
                parent = cursor.parent
                if parent == cursor:
                    raise ReceiptError("UNSAFE_RECEIPT_DIRECTORY") from None
                cursor = parent
            except (OSError, RuntimeError, ValueError):
                raise ReceiptError("UNSAFE_RECEIPT_DIRECTORY") from None
        if not _validate_parent(cursor):
            raise ReceiptError("UNSAFE_RECEIPT_DIRECTORY")
        if not create:
            return requested
        try:
            requested.mkdir(parents=True, exist_ok=True)
        except (OSError, RuntimeError, ValueError):
            raise ReceiptError("UNSAFE_RECEIPT_DIRECTORY") from None
    except (OSError, RuntimeError, ValueError):
        raise ReceiptError("UNSAFE_RECEIPT_DIRECTORY") from None
    if not _validate_parent(requested):
        raise ReceiptError("UNSAFE_RECEIPT_DIRECTORY")
    return requested


def save_receipt_file(receipt: Receipt, path: str | os.PathLike[str]) -> Path:
    """Atomically save the exact offline receipt schema to a safe JSON path."""

    _require_valid_receipt(receipt)
    invalid_path = False
    try:
        requested = Path(path)
    except (TypeError, ValueError):
        invalid_path = True
        requested = Path()
    if invalid_path:
        raise ReceiptError("UNSAFE_FILE_PATH")
    metadata_error = False
    try:
        requested_metadata = requested.lstat()
    except FileNotFoundError:
        requested_metadata = None
    except OSError:
        metadata_error = True
        requested_metadata = None
    if metadata_error:
        raise ReceiptError("UNSAFE_FILE_PATH")
    if requested_metadata is not None and stat.S_ISDIR(requested_metadata.st_mode):
        if stat.S_ISLNK(requested_metadata.st_mode) or _is_reparse(requested_metadata):
            raise ReceiptError("UNSAFE_FILE_PATH")
        target = requested / f"download_receipt-{receipt.batch_id}.json"
    else:
        target = requested
    parent = target.parent
    if not _validate_parent(parent) or not _safe_target(target):
        raise ReceiptError("UNSAFE_FILE_PATH")

    payload = json.dumps(
        receipt.to_file_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=False,
    )
    descriptor = -1
    temporary: Path | None = None
    write_failed = False
    try:
        descriptor, raw_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=parent
        )
        temporary = Path(raw_name)
        metadata = temporary.lstat()
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata):
            raise OSError("unsafe temporary file")
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if not _validate_parent(parent) or not _safe_target(target):
            raise OSError("unsafe target")
        os.replace(temporary, target)
        temporary = None
    except (OSError, RuntimeError, ValueError):
        write_failed = True
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
    if write_failed:
        raise ReceiptError("FILE_WRITE_FAILED")
    return target


def load_receipt_file(
    path: str | os.PathLike[str],
    manifest: ExportManifest,
) -> Receipt:
    """Load the exact offline schema and bind it to its original manifest."""

    if not isinstance(manifest, ExportManifest):
        raise ReceiptError("INVALID_MANIFEST")
    try:
        requested = Path(path)
        selected_metadata = requested.lstat()
        if (
            not stat.S_ISREG(selected_metadata.st_mode)
            or stat.S_ISLNK(selected_metadata.st_mode)
            or _is_reparse(selected_metadata)
        ):
            raise OSError("unsafe receipt file")
        with requested.open("rb") as stream:
            opened_metadata = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(opened_metadata.st_mode)
                or _is_reparse(opened_metadata)
                or not os.path.samestat(selected_metadata, opened_metadata)
            ):
                raise OSError("receipt file changed")
            encoded = stream.read(MAX_RECEIPT_FILE_BYTES + 1)
    except (OSError, RuntimeError, TypeError, ValueError):
        raise ReceiptError("INVALID_RECEIPT_FILE") from None
    if len(encoded) > MAX_RECEIPT_FILE_BYTES:
        raise ReceiptError("RECEIPT_FILE_TOO_LARGE")
    try:
        payload = json.loads(encoded.decode("utf-8"))
    except Exception:
        raise ReceiptError("INVALID_RECEIPT_FILE") from None
    stack: list[tuple[object, int]] = [(payload, 1)]
    while stack:
        value, depth = stack.pop()
        if depth > MAX_RECEIPT_JSON_DEPTH:
            raise ReceiptError("RECEIPT_FILE_TOO_DEEP")
        if isinstance(value, dict):
            stack.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            stack.extend((child, depth + 1) for child in value)
    if not isinstance(payload, dict) or set(payload) != {"batch_id", "items"}:
        raise ReceiptError("INVALID_RECEIPT_FILE")
    if payload["batch_id"] != str(manifest.batch_id):
        raise ReceiptError("BATCH_MISMATCH")
    raw_items = payload["items"]
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= len(manifest.rows):
        raise ReceiptError("INVALID_RECEIPT_FILE")
    items: list[ReceiptItem] = []
    actions: list[tuple[str, str, int, str]] = []
    for position, raw in enumerate(raw_items):
        if not isinstance(raw, dict) or set(raw) != set(_ONLINE_ITEM_KEYS):
            raise ReceiptError("INVALID_RECEIPT_FILE")
        try:
            item = ReceiptItem(**raw)
        except (TypeError, ValueError):
            raise ReceiptError("INVALID_RECEIPT_FILE") from None
        row = manifest.rows[position]
        if (
            item.candidate_id != str(row.candidate_id)
            or item.review_id != str(row.review_id)
            or item.review_version != row.review_version
        ):
            raise ReceiptError("ITEM_MISMATCH")
        if item.status == "SUCCEEDED":
            try:
                received_path = validate_relative_path(
                    item.relative_path,
                    "relative_path",
                )
            except (ManifestError, TypeError, ValueError):
                raise ReceiptError("ITEM_MISMATCH") from None
            target = row.target_relative_path
            suffix_adjustment = (
                row.action == "ADD"
                and received_path.parent == target.parent
                and received_path.stem == target.stem
                and received_path.suffix.lower() in SUPPORTED_SUFFIXES
            )
            if received_path != target and not suffix_adjustment:
                raise ReceiptError("ITEM_MISMATCH")
        items.append(item)
        actions.append(
            (item.candidate_id, item.review_id, item.review_version, row.action)
        )
    try:
        return Receipt(
            manifest.batch_id,
            tuple(items),
            tuple(actions),
            tuple(str(row.candidate_id) for row in manifest.rows),
        )
    except ReceiptError:
        raise ReceiptError("INVALID_RECEIPT_FILE") from None
