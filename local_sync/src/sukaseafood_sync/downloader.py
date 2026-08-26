from __future__ import annotations

from dataclasses import dataclass
from email.utils import parsedate_to_datetime
import hashlib
import os
from pathlib import Path
import stat
import time
from datetime import timezone
from typing import Callable
from urllib.parse import urljoin, urlsplit
import warnings

import imagehash
from PIL import Image, ImageOps
import requests

from .manifest import ManifestRow


MIB = 1024 * 1024
_REPARSE_POINT = 0x400
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
_FORMAT_SUFFIXES = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}


class DownloadError(RuntimeError):
    """An original image could not be downloaded and safely validated."""

    def __init__(self, message: str, *, code: str = "DOWNLOAD_ERROR") -> None:
        self.code = code
        super().__init__(message)


class DownloadCancelled(DownloadError):
    """The caller cancelled an in-progress download."""

    def __init__(self, message: str = "download cancelled") -> None:
        super().__init__(message, code="CANCELLED")


@dataclass(frozen=True, slots=True)
class DownloadPolicy:
    max_bytes: int = 100 * MIB
    chunk_size: int = 256 * 1024
    attempts: int = 4
    backoff_delays: tuple[float, ...] = (10.0, 20.0, 40.0)
    timeout: tuple[float, float] = (10.0, 60.0)
    max_redirects: int = 5
    max_retry_after: float = 300.0

    def __post_init__(self) -> None:
        valid = (
            self.max_bytes > 0
            and self.chunk_size > 0
            and self.attempts > 0
            and len(self.backoff_delays) >= self.attempts - 1
            and all(delay >= 0 for delay in self.backoff_delays)
            and len(self.timeout) == 2
            and all(value > 0 for value in self.timeout)
            and self.max_redirects >= 0
            and self.max_retry_after >= 0
        )
        if not valid:
            raise ValueError("download policy values must be safe and positive")


@dataclass(frozen=True, slots=True)
class DownloadResult:
    staging_path: Path
    sha256: str
    phash: str
    byte_count: int
    format: str
    suffix: str
    width: int
    height: int


Progress = Callable[[int, int | None], None]
Cancel = Callable[[], bool]


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT)


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        raise DownloadError(
            "download path cannot be inspected", code="FILESYSTEM_ERROR"
        ) from None


def _regular_non_reparse(metadata: os.stat_result) -> bool:
    return stat.S_ISREG(metadata.st_mode) and not _is_reparse(metadata)


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _remove_owned_staging(path: Path, owned: os.stat_result | None) -> None:
    if owned is None:
        return
    current = _lstat(path)
    if (
        current is not None
        and _regular_non_reparse(current)
        and _same_file(current, owned)
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _prepare_paths(destination: Path) -> Path:
    if not destination.parent.is_dir():
        raise DownloadError(
            "destination parent must already exist", code="FILESYSTEM_ERROR"
        )
    if _lstat(destination) is not None:
        raise DownloadError(
            "destination already exists or is unsafe", code="FILESYSTEM_ERROR"
        )
    staging = destination.with_name(destination.name + ".part")
    existing = _lstat(staging)
    if existing is not None:
        if not _regular_non_reparse(existing):
            raise DownloadError(
                "staging path is not a safe regular file", code="FILESYSTEM_ERROR"
            )
        try:
            staging.unlink()
        except OSError:
            raise DownloadError(
                "stale staging file cannot be removed", code="FILESYSTEM_ERROR"
            ) from None
    return staging


def _validated_https_url(candidate: str) -> str | None:
    if any(character.isspace() for character in candidate):
        return None
    try:
        parsed = urlsplit(candidate)
        _ = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return candidate


def _request(
    session: requests.Session,
    original_url: str,
    policy: DownloadPolicy,
    cancel: Cancel,
) -> requests.Response:
    current = original_url
    visited: set[str] = set()
    redirects = 0
    while True:
        if cancel():
            raise DownloadCancelled("download cancelled")
        if current in visited:
            raise DownloadError("redirect loop rejected", code="NETWORK_ERROR")
        visited.add(current)
        response: requests.Response | None = None
        request_failed = False
        try:
            prepared = requests.Request(
                "GET",
                current,
                headers={"Accept-Encoding": "identity"},
            ).prepare()
            environment = session.merge_environment_settings(
                current,
                proxies={},
                stream=True,
                verify=None,
                cert=None,
            )
            response = session.send(
                prepared,
                allow_redirects=False,
                timeout=policy.timeout,
                **environment,
            )
        except requests.RequestException:
            request_failed = True
        if request_failed:
            raise _TransportFailure
        assert response is not None
        if response.status_code not in _REDIRECT_STATUSES:
            return response
        location = response.headers.get("Location")
        response.close()
        if redirects >= policy.max_redirects or location is None:
            raise DownloadError(
                "redirect limit or location rejected", code="NETWORK_ERROR"
            )
        try:
            location_parts = urlsplit(location)
            if location_parts.scheme and _validated_https_url(location) is None:
                raise ValueError
            following = urljoin(current, location)
        except (TypeError, ValueError):
            following = None
        current = _validated_https_url(following) if following is not None else None
        if current is None:
            raise DownloadError("redirect target rejected", code="NETWORK_ERROR")
        redirects += 1


class _TransportFailure(Exception):
    pass


def _content_length(response: requests.Response, maximum: int) -> int | None:
    raw = response.headers.get("Content-Length")
    if raw is None:
        return None
    if not raw.isdigit():
        raise DownloadError("Content-Length is malformed", code="INVALID_IMAGE")
    parsed = int(raw)
    if parsed > maximum:
        raise DownloadError("download size exceeds the configured limit")
    return parsed


def _retry_after(response: requests.Response, policy: DownloadPolicy) -> float | None:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    if raw.isdigit():
        return min(float(raw), policy.max_retry_after)
    try:
        parsed = parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        delay = max(0.0, parsed.timestamp() - time.time())
    except (TypeError, ValueError, OverflowError):
        return None
    return min(delay, policy.max_retry_after)


def _open_new_staging(path: Path):
    flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError:
        raise DownloadError(
            "staging file cannot be created safely", code="FILESYSTEM_ERROR"
        ) from None
    metadata = os.fstat(descriptor)
    if not _regular_non_reparse(metadata):
        os.close(descriptor)
        raise DownloadError(
            "staging path is not a safe regular file", code="FILESYSTEM_ERROR"
        )
    return os.fdopen(descriptor, "w+b"), metadata


def _validate_image(stream) -> tuple[str, str, int, int]:
    decode_failed = False
    unsupported = False
    zero_sized = False
    decoded_format: str | None = None
    width = height = 0
    perceptual_hash = ""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            stream.seek(0)
            with Image.open(stream) as image:
                image.verify()
            stream.seek(0)
            with Image.open(stream) as image:
                decoded_format = image.format
                if decoded_format not in _FORMAT_SUFFIXES:
                    unsupported = True
                else:
                    image.load()
                    transposed = ImageOps.exif_transpose(image)
                    width, height = transposed.size
                    zero_sized = width <= 0 or height <= 0
                    if not zero_sized:
                        rgb = transposed.convert("RGB")
                        rgb.load()
                        perceptual_hash = str(imagehash.phash(rgb)).lower()
    except Exception:
        decode_failed = True
    if decode_failed:
        raise DownloadError(
            "downloaded bytes are not a safe decodable image", code="INVALID_IMAGE"
        )
    if unsupported:
        raise DownloadError(
            "downloaded image is not a supported image format", code="INVALID_IMAGE"
        )
    if zero_sized or decoded_format is None:
        raise DownloadError(
            "downloaded bytes are not a safe decodable image", code="INVALID_IMAGE"
        )
    return decoded_format, perceptual_hash, width, height


def _stream_response(
    response: requests.Response,
    staging: Path,
    policy: DownloadPolicy,
    progress: Progress,
    cancel: Cancel,
) -> tuple[os.stat_result, str, int, str, str, int, int]:
    total = _content_length(response, policy.max_bytes)
    stream, owned = _open_new_staging(staging)
    digest = hashlib.sha256()
    downloaded = 0
    failed: DownloadError | None = None
    transport_failed = False
    try:
        with stream:
            try:
                for chunk in response.iter_content(chunk_size=policy.chunk_size):
                    if cancel():
                        failed = DownloadCancelled("download cancelled")
                        break
                    if not chunk:
                        continue
                    downloaded += len(chunk)
                    if downloaded > policy.max_bytes:
                        failed = DownloadError("download size exceeds the configured limit")
                        break
                    stream.write(chunk)
                    digest.update(chunk)
                    progress(downloaded, total)
            except requests.RequestException:
                transport_failed = True
            except Exception:
                failed = DownloadError(
                    "download stream could not be saved", code="FILESYSTEM_ERROR"
                )
            if failed is None and not transport_failed:
                try:
                    stream.flush()
                    os.fsync(stream.fileno())
                    decoded_format, phash, width, height = _validate_image(stream)
                except DownloadError as error:
                    failed = error
                except OSError:
                    failed = DownloadError(
                        "download stream could not be saved", code="FILESYSTEM_ERROR"
                    )
    finally:
        if failed is not None or transport_failed:
            _remove_owned_staging(staging, owned)
    if transport_failed:
        raise _TransportFailure
    if failed is not None:
        raise failed
    return (
        owned,
        digest.hexdigest(),
        downloaded,
        decoded_format,
        phash,
        width,
        height,
    )


def download_image(
    session: requests.Session,
    row: ManifestRow,
    destination: Path,
    policy: DownloadPolicy,
    progress: Progress,
    cancel: Cancel,
) -> DownloadResult:
    """Download and validate an ADD row, leaving verified bytes in ``.part``."""

    session.trust_env = True
    if not isinstance(row, ManifestRow) or row.action != "ADD":
        raise DownloadError("download requires an ADD manifest row")
    original_url = _validated_https_url(row.original_url)
    if original_url is None:
        raise DownloadError("original image URL is not safe HTTPS")
    target = Path(destination)
    staging = _prepare_paths(target)

    for attempt in range(policy.attempts):
        response: requests.Response | None = None
        transport_failed = False
        retry_delay: float | None = None
        try:
            response = _request(session, original_url, policy, cancel)
            status = response.status_code
            if status in _RETRY_STATUSES:
                retry_delay = _retry_after(response, policy)
            elif not 200 <= status < 300:
                raise DownloadError(
                    "image request failed with a non-retriable HTTP status",
                    code="NETWORK_ERROR",
                )
            else:
                (
                    owned,
                    sha256,
                    byte_count,
                    decoded_format,
                    phash,
                    width,
                    height,
                ) = _stream_response(response, staging, policy, progress, cancel)
                try:
                    if _lstat(target) is not None:
                        raise DownloadError(
                            "destination appeared during download",
                            code="FILESYSTEM_ERROR",
                        )
                    current = _lstat(staging)
                    if (
                        current is None
                        or not _regular_non_reparse(current)
                        or not _same_file(current, owned)
                    ):
                        raise DownloadError(
                            "verified staging file changed unexpectedly",
                            code="FILESYSTEM_ERROR",
                        )
                    return DownloadResult(
                        staging_path=staging,
                        sha256=sha256,
                        phash=phash,
                        byte_count=byte_count,
                        format=decoded_format,
                        suffix=_FORMAT_SUFFIXES[decoded_format],
                        width=width,
                        height=height,
                    )
                except BaseException:
                    _remove_owned_staging(staging, owned)
                    raise
        except _TransportFailure:
            transport_failed = True
        finally:
            if response is not None:
                response.close()

        if attempt + 1 >= policy.attempts:
            if transport_failed:
                raise DownloadError(
                    "image download failed after network retries", code="NETWORK_ERROR"
                )
            raise DownloadError(
                "image request failed after retriable HTTP statuses",
                code="NETWORK_ERROR",
            )
        if cancel():
            raise DownloadCancelled("download cancelled")
        delay = (
            retry_delay
            if retry_delay is not None
            else policy.backoff_delays[attempt]
        )
        time.sleep(delay)

    raise DownloadError("image download failed", code="NETWORK_ERROR")
