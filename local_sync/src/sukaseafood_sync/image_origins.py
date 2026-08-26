from __future__ import annotations

from ipaddress import ip_address
import os
from urllib.parse import urlsplit


DEFAULT_IMAGE_ORIGIN_ALLOWLIST = (
    ".inaturalist.org",
    "inaturalist-open-data.s3.amazonaws.com",
    "caos.boldsystems.org",
    "cdn.floridamuseum.ufl.edu",
    "collections.nmnh.si.edu",
    "huggingface.co",
    "pictures.snsb.info",
    "specify.saiab.ac.za",
    "www.morphosource.org",
    ".wikimedia.org",
    ".wikimediausercontent.com",
    ".gbif.org",
    ".fishair.org",
    ".fish-vista.org",
    ".fishvista.org",
    ".example.test",
    ".e2e.test",
)
IMAGE_ORIGIN_ENV = "SUKASEAFOOD_IMAGE_ORIGIN_ALLOWLIST"


class ImageOriginPolicyError(ValueError):
    pass


def _pattern(value: str) -> str:
    pattern = value.strip().lower()
    host = pattern[1:] if pattern.startswith(".") else pattern
    if (
        not host
        or "*" in pattern
        or any(character in host for character in ":/")
        or host == "localhost"
        or host.endswith(".localhost")
        or host.startswith(".")
        or host.endswith(".")
        or ".." in host
    ):
        raise ImageOriginPolicyError("invalid image-origin allowlist")
    try:
        ip_address(host)
    except ValueError:
        pass
    else:
        raise ImageOriginPolicyError("literal image origins are forbidden")
    labels = host.split(".")
    if any(
        not label
        or label.startswith("-")
        or label.endswith("-")
        or not all(character.isascii() and (character.isalnum() or character == "-") for character in label)
        for label in labels
    ):
        raise ImageOriginPolicyError("invalid image-origin allowlist")
    return f".{host}" if pattern.startswith(".") else host


def configured_image_origin_allowlist() -> tuple[str, ...]:
    raw = os.getenv(IMAGE_ORIGIN_ENV)
    values = DEFAULT_IMAGE_ORIGIN_ALLOWLIST if raw is None else tuple(raw.split(","))
    normalized = tuple(dict.fromkeys(_pattern(value) for value in values if value.strip()))
    if not normalized:
        raise ImageOriginPolicyError("image-origin allowlist must not be empty")
    return normalized


def require_approved_image_url(
    value: str, allowlist: tuple[str, ...] | None = None
) -> str:
    if value != value.strip() or any(character.isspace() for character in value):
        raise ImageOriginPolicyError("image URL must not contain whitespace")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ImageOriginPolicyError("image URL is malformed") from exc
    hostname = parsed.hostname
    if (
        parsed.scheme.lower() != "https"
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        raise ImageOriginPolicyError("image URL must use approved HTTPS")
    host = hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        raise ImageOriginPolicyError("local image origins are forbidden")
    try:
        ip_address(host)
    except ValueError:
        pass
    else:
        raise ImageOriginPolicyError("literal image origins are forbidden")
    patterns = allowlist or configured_image_origin_allowlist()
    for pattern in patterns:
        if pattern.startswith("."):
            root = pattern[1:]
            if host == root or host.endswith(pattern):
                return value
        elif host == pattern:
            return value
    raise ImageOriginPolicyError("image origin is not approved")
