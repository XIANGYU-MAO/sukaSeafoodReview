from __future__ import annotations

from ipaddress import ip_address
from urllib.parse import urlsplit


# Suffix entries begin with a dot and match both the registrable host and its
# subdomains.  Reserved test domains keep deterministic offline tests possible;
# they cannot resolve on the public Internet.
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


class ImageOriginError(ValueError):
    pass


def normalize_image_origin_pattern(value: str) -> str:
    pattern = value.strip().lower()
    host = pattern[1:] if pattern.startswith(".") else pattern
    if (
        not host
        or "*" in pattern
        or ":" in host
        or "/" in host
        or host == "localhost"
        or host.endswith(".localhost")
        or host.startswith(".")
        or host.endswith(".")
        or ".." in host
    ):
        raise ImageOriginError("invalid image-origin allowlist entry")
    try:
        ip_address(host)
    except ValueError:
        pass
    else:
        raise ImageOriginError("image-origin allowlist entries must be hostnames")
    labels = host.split(".")
    if any(
        not label
        or label.startswith("-")
        or label.endswith("-")
        or not all(character.isascii() and (character.isalnum() or character == "-") for character in label)
        for label in labels
    ):
        raise ImageOriginError("invalid image-origin allowlist entry")
    return f".{host}" if pattern.startswith(".") else host


def normalize_image_origin_allowlist(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(normalize_image_origin_pattern(value) for value in values)
    if not normalized:
        raise ImageOriginError("image-origin allowlist must not be empty")
    return tuple(dict.fromkeys(normalized))


def image_host_allowed(hostname: str, allowlist: tuple[str, ...]) -> bool:
    host = hostname.rstrip(".").lower()
    for pattern in allowlist:
        if pattern.startswith("."):
            root = pattern[1:]
            if host == root or host.endswith(pattern):
                return True
        elif host == pattern:
            return True
    return False


def require_approved_image_url(
    value: str, allowlist: tuple[str, ...] | None
) -> str:
    if value != value.strip() or any(character.isspace() for character in value):
        raise ImageOriginError("image URL must not contain whitespace")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ImageOriginError("image URL is malformed") from exc
    hostname = parsed.hostname
    if (
        parsed.scheme.lower() != "https"
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        raise ImageOriginError("image URL must use an approved HTTPS origin")
    host = hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        raise ImageOriginError("local image origins are forbidden")
    try:
        ip_address(host)
    except ValueError:
        pass
    else:
        raise ImageOriginError("literal image addresses are forbidden")
    if allowlist is not None and not image_host_allowed(host, allowlist):
        raise ImageOriginError("image origin is not approved")
    return value


def require_public_image_url(value: str) -> str:
    return require_approved_image_url(value, None)
