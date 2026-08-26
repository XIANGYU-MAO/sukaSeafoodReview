from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from io import BytesIO
import os
from pathlib import Path, PurePosixPath
import re
import traceback
from unittest.mock import Mock
from uuid import UUID

import imagehash
from PIL import Image, features
import pytest
import requests
import responses

from sukaseafood_sync.downloader import (
    DownloadCancelled,
    DownloadError,
    DownloadPolicy,
    download_image,
)
from sukaseafood_sync.manifest import ManifestRow


IMAGE_URL = "https://images.example.test/original.jpg?token=super-secret-query-token"
REDIRECT_URL = "https://cdn.example.test/assets/fish"
TOKEN = "super-secret-query-token"


def add_row(**overrides: object) -> ManifestRow:
    values: dict[str, object] = {
        "batch_id": UUID("11111111-1111-4111-8111-111111111111"),
        "action": "ADD",
        "candidate_id": UUID("22222222-2222-4222-8222-222222222222"),
        "review_id": UUID("33333333-3333-4333-8333-333333333333"),
        "review_version": 1,
        "species_code": "SF006",
        "target_relative_path": PurePosixPath(
            "images/SF006/22222222-2222-4222-8222-222222222222.jpg"
        ),
        "previous_relative_path": None,
        "preview_url": "https://images.example.test/preview.jpg",
        "original_url": IMAGE_URL,
        "source_url": "https://catalog.example.test/records/1",
        "creator": "A. Researcher",
        "license": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "attribution": "A. Researcher / Example Catalog",
    }
    values.update(overrides)
    return ManifestRow(**values)  # type: ignore[arg-type]


def session() -> requests.Session:
    client = requests.Session()
    client.trust_env = False
    return client


def policy(**overrides: object) -> DownloadPolicy:
    return DownloadPolicy(**overrides)


def noop(_downloaded: int, _total: int | None) -> None:
    pass


def never_cancel() -> bool:
    return False


@pytest.fixture
def valid_jpeg() -> bytes:
    output = BytesIO()
    image = Image.new("RGB", (11, 7))
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            pixels[x, y] = ((x * 23) % 256, (y * 37) % 256, ((x + y) * 19) % 256)
    image.save(output, format="JPEG", quality=91, optimize=False, progressive=False)
    return output.getvalue()


def encoded_image(format_name: str, size: tuple[int, int] = (9, 5)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, color=(17, 91, 203)).save(output, format=format_name)
    return output.getvalue()


@pytest.fixture
def http():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        yield mock


@pytest.fixture
def fake_sleep(monkeypatch: pytest.MonkeyPatch) -> Mock:
    sleeper = Mock()
    monkeypatch.setattr("sukaseafood_sync.downloader.time.sleep", sleeper)
    return sleeper


def part_for(destination: Path) -> Path:
    return destination.with_name(destination.name + ".part")


def assert_no_output(destination: Path) -> None:
    assert not destination.exists()
    assert not part_for(destination).exists()


def assert_secret_free(error: BaseException) -> None:
    pending = [error]
    seen: set[int] = set()
    surfaces: list[str] = []
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        surfaces.extend((str(current), repr(current)))
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    surfaces.append(
        "".join(traceback.format_exception(type(error), error, error.__traceback__))
    )
    assert all(TOKEN not in surface for surface in surfaces)
    assert all(IMAGE_URL not in surface for surface in surfaces)


def test_policy_defaults_are_enforced_by_download_behavior(http, valid_jpeg, tmp_path):
    http.add(responses.GET, IMAGE_URL, body=valid_jpeg, status=200)
    destination = tmp_path / "fish.image"

    result = download_image(
        session(), add_row(), destination, policy(), noop, never_cancel
    )

    assert result.staging_path == part_for(destination)
    assert result.staging_path.read_bytes() == valid_jpeg
    assert not destination.exists()


def test_429_honors_retry_after_seconds(http, fake_sleep, valid_jpeg, tmp_path):
    http.add(responses.GET, IMAGE_URL, status=429, headers={"Retry-After": "3"})
    http.add(responses.GET, IMAGE_URL, body=valid_jpeg, status=200)

    result = download_image(
        session(), add_row(), tmp_path / "fish.jpg", policy(), noop, never_cancel
    )

    assert result.sha256
    assert fake_sleep.call_args_list == [((3.0,), {})]


def test_retry_after_http_date_uses_bounded_delay(
    http, fake_sleep, valid_jpeg, tmp_path, monkeypatch
):
    now = 1_800_000_000.0
    retry_at = datetime.fromtimestamp(now + 450, tz=timezone.utc).strftime(
        "%a, %d %b %Y %H:%M:%S GMT"
    )
    monkeypatch.setattr("sukaseafood_sync.downloader.time.time", lambda: now)
    http.add(responses.GET, IMAGE_URL, status=503, headers={"Retry-After": retry_at})
    http.add(responses.GET, IMAGE_URL, body=valid_jpeg, status=200)

    download_image(session(), add_row(), tmp_path / "fish.jpg", policy(), noop, never_cancel)

    assert fake_sleep.call_args_list == [((300.0,), {})]


def test_invalid_retry_after_falls_back_10_20_40_and_stops_after_four_attempts(
    http, fake_sleep, tmp_path
):
    for _ in range(4):
        http.add(
            responses.GET,
            IMAGE_URL,
            status=503,
            headers={"Retry-After": "not-a-delay"},
        )

    with pytest.raises(DownloadError, match="HTTP"):
        download_image(session(), add_row(), tmp_path / "fish.jpg", policy(), noop, never_cancel)

    assert len(http.calls) == 4
    assert [call.args[0] for call in fake_sleep.call_args_list] == [10.0, 20.0, 40.0]
    assert_no_output(tmp_path / "fish.jpg")


@pytest.mark.parametrize("failure", [requests.ConnectionError("offline"), requests.Timeout("slow")])
def test_retries_transport_failures(http, fake_sleep, valid_jpeg, tmp_path, failure):
    http.add(responses.GET, IMAGE_URL, body=failure)
    http.add(responses.GET, IMAGE_URL, body=valid_jpeg, status=200)

    result = download_image(
        session(), add_row(), tmp_path / "fish.jpg", policy(), noop, never_cancel
    )

    assert result.byte_count == len(valid_jpeg)
    assert len(http.calls) == 2
    fake_sleep.assert_called_once_with(10.0)


def test_404_fails_immediately_without_retry(http, fake_sleep, tmp_path):
    http.add(responses.GET, IMAGE_URL, status=404)

    with pytest.raises(DownloadError, match="HTTP"):
        download_image(session(), add_row(), tmp_path / "fish.jpg", policy(), noop, never_cancel)

    assert len(http.calls) == 1
    fake_sleep.assert_not_called()
    assert_no_output(tmp_path / "fish.jpg")


def test_cancel_before_request_makes_no_request(http, tmp_path):
    http.add(responses.GET, IMAGE_URL, status=200, body=b"unused")

    with pytest.raises(DownloadCancelled, match="cancelled"):
        download_image(session(), add_row(), tmp_path / "fish.jpg", policy(), noop, lambda: True)

    assert len(http.calls) == 0
    assert_no_output(tmp_path / "fish.jpg")


def test_cancel_during_stream_removes_partial_file(http, valid_jpeg, tmp_path):
    http.add(responses.GET, IMAGE_URL, status=200, body=valid_jpeg)
    checks = 0

    def cancel_after_first_chunk() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 3

    destination = tmp_path / "fish.jpg"
    with pytest.raises(DownloadCancelled, match="cancelled"):
        download_image(
            session(),
            add_row(),
            destination,
            policy(chunk_size=32),
            noop,
            cancel_after_first_chunk,
        )

    assert len(http.calls) == 1
    assert_no_output(destination)


def test_cancel_before_retry_does_not_sleep_or_request_again(http, fake_sleep, tmp_path):
    http.add(responses.GET, IMAGE_URL, status=503)
    checks = iter((False, True))

    with pytest.raises(DownloadCancelled, match="cancelled"):
        download_image(
            session(), add_row(), tmp_path / "fish.jpg", policy(), noop, lambda: next(checks)
        )

    assert len(http.calls) == 1
    fake_sleep.assert_not_called()


def test_redirect_success_is_manual_and_preserves_safe_request_headers(
    http, valid_jpeg, tmp_path
):
    http.add(responses.GET, IMAGE_URL, status=302, headers={"Location": REDIRECT_URL})
    http.add(responses.GET, REDIRECT_URL, status=200, body=valid_jpeg)
    client = session()

    result = download_image(
        client, add_row(), tmp_path / "fish.jpg", policy(), noop, never_cancel
    )

    assert result.byte_count == len(valid_jpeg)
    assert client.trust_env is True
    assert [call.request.url for call in http.calls] == [IMAGE_URL, REDIRECT_URL]
    assert all(call.request.headers["Accept-Encoding"] == "identity" for call in http.calls)
    assert all(TOKEN not in repr(dict(call.request.headers)) for call in http.calls)


@pytest.mark.parametrize(
    "location",
    [
        "http://cdn.example.test/fish.jpg",
        "https://user:password@cdn.example.test/fish.jpg",
        "https:///missing-host.jpg",
    ],
    ids=["downgrade", "credentials", "missing-host"],
)
def test_rejects_unsafe_redirects_without_following(http, tmp_path, location):
    http.add(responses.GET, IMAGE_URL, status=302, headers={"Location": location})

    with pytest.raises(DownloadError, match="redirect") as caught:
        download_image(session(), add_row(), tmp_path / "fish.jpg", policy(), noop, never_cancel)

    assert len(http.calls) == 1
    assert_secret_free(caught.value)


def test_rejects_redirect_location_with_whitespace(http, tmp_path):
    http.add(
        responses.GET,
        IMAGE_URL,
        status=302,
        headers={"Location": "https://cdn.example.test/bad path.jpg"},
    )

    with pytest.raises(DownloadError, match="redirect"):
        download_image(
            session(),
            add_row(),
            tmp_path / "fish.jpg",
            policy(attempts=1, backoff_delays=()),
            noop,
            never_cancel,
        )

    assert len(http.calls) == 1


def test_rejects_redirect_loop_and_limit(http, tmp_path):
    loop_url = "https://cdn.example.test/loop"
    http.add(responses.GET, IMAGE_URL, status=302, headers={"Location": loop_url})
    http.add(responses.GET, loop_url, status=302, headers={"Location": IMAGE_URL})
    with pytest.raises(DownloadError, match="redirect"):
        download_image(session(), add_row(), tmp_path / "loop.jpg", policy(), noop, never_cancel)
    assert len(http.calls) == 2

    http.reset()
    urls = [IMAGE_URL, *(f"https://cdn.example.test/{index}" for index in range(3))]
    for current, following in zip(urls, urls[1:]):
        http.add(responses.GET, current, status=302, headers={"Location": following})
    with pytest.raises(DownloadError, match="redirect"):
        download_image(
            session(),
            add_row(),
            tmp_path / "limit.jpg",
            policy(max_redirects=2),
            noop,
            never_cancel,
        )
    assert len(http.calls) == 3


@pytest.mark.parametrize("length", ["101", "-1", "not-a-number"])
def test_rejects_oversized_or_malformed_content_length_before_staging(
    http, tmp_path, length
):
    http.add(
        responses.GET,
        IMAGE_URL,
        status=200,
        body=b"x" * 20,
        headers={"Content-Length": length},
    )
    destination = tmp_path / "fish.jpg"

    with pytest.raises(DownloadError, match="Content-Length|size"):
        download_image(
            session(), add_row(), destination, policy(max_bytes=100), noop, never_cancel
        )

    assert_no_output(destination)


def test_streamed_size_limit_removes_partial_file(http, tmp_path):
    http.add(responses.GET, IMAGE_URL, status=200, body=b"x" * 101)
    destination = tmp_path / "fish.jpg"

    with pytest.raises(DownloadError, match="size"):
        download_image(
            session(),
            add_row(),
            destination,
            policy(max_bytes=100, chunk_size=16),
            noop,
            never_cancel,
        )

    assert_no_output(destination)


def test_invalid_image_is_not_promoted(http, tmp_path):
    invalid = Path(__file__).parent / "fixtures" / "not_an_image.txt"
    http.add(responses.GET, IMAGE_URL, body=invalid.read_bytes(), status=200)
    target = tmp_path / "fish.jpg"

    with pytest.raises(DownloadError, match="decodable image"):
        download_image(session(), add_row(), target, policy(), noop, never_cancel)

    assert_no_output(target)


def test_truncated_image_is_rejected_and_cleaned(http, valid_jpeg, tmp_path):
    http.add(responses.GET, IMAGE_URL, body=valid_jpeg[:-10], status=200)
    destination = tmp_path / "fish.jpg"

    with pytest.raises(DownloadError, match="decodable image"):
        download_image(session(), add_row(), destination, policy(), noop, never_cancel)

    assert_no_output(destination)


def test_decompression_bomb_is_rejected_and_cleaned(http, tmp_path, monkeypatch):
    body = encoded_image("PNG", (20, 20))
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
    http.add(responses.GET, IMAGE_URL, body=body, status=200)
    destination = tmp_path / "fish.jpg"

    with pytest.raises(DownloadError, match="decodable image"):
        download_image(session(), add_row(), destination, policy(), noop, never_cancel)

    assert_no_output(destination)


def test_unsupported_decodable_image_is_rejected(http, tmp_path):
    http.add(responses.GET, IMAGE_URL, body=encoded_image("GIF"), status=200)
    destination = tmp_path / "fish.jpg"

    with pytest.raises(DownloadError, match="supported image"):
        download_image(session(), add_row(), destination, policy(), noop, never_cancel)

    assert_no_output(destination)


@pytest.mark.parametrize(
    ("format_name", "suffix"),
    [("JPEG", ".jpg"), ("PNG", ".png"), ("WEBP", ".webp")],
)
def test_preserves_raw_bytes_and_returns_verified_metadata(
    http, tmp_path, format_name, suffix
):
    if format_name == "WEBP" and not features.check("webp"):
        pytest.skip("Pillow was built without WebP")
    body = encoded_image(format_name)
    http.add(responses.GET, IMAGE_URL, body=body, status=200)
    destination = tmp_path / "fish.untrusted"

    result = download_image(
        session(), add_row(), destination, policy(), noop, never_cancel
    )

    with Image.open(BytesIO(body)) as decoded:
        expected_phash = str(imagehash.phash(decoded.convert("RGB")))
    assert result.staging_path == part_for(destination)
    assert result.staging_path.read_bytes() == body
    assert result.sha256 == hashlib.sha256(body).hexdigest()
    assert result.phash == expected_phash
    assert re.fullmatch(r"[0-9a-f]{64}", result.sha256)
    assert re.fullmatch(r"[0-9a-f]{16}", result.phash)
    assert result.byte_count == len(body)
    assert result.format == format_name
    assert result.suffix == suffix
    assert (result.width, result.height) == (9, 5)
    assert not destination.exists()


def test_applies_exif_orientation_before_hash_and_dimensions(http, tmp_path):
    output = BytesIO()
    image = Image.new("RGB", (5, 3), color=(200, 40, 10))
    exif = Image.Exif()
    exif[274] = 6
    image.save(output, format="JPEG", exif=exif)
    body = output.getvalue()
    http.add(responses.GET, IMAGE_URL, body=body, status=200)

    result = download_image(
        session(), add_row(), tmp_path / "fish.jpg", policy(), noop, never_cancel
    )

    assert (result.width, result.height) == (3, 5)


def test_stale_regular_part_is_replaced_and_progress_is_monotonic(
    http, valid_jpeg, tmp_path
):
    destination = tmp_path / "fish.jpg"
    staging = part_for(destination)
    staging.write_bytes(b"stale")
    progress: list[tuple[int, int | None]] = []
    http.add(
        responses.GET,
        IMAGE_URL,
        body=valid_jpeg,
        status=200,
        headers={"Content-Length": str(len(valid_jpeg))},
    )

    result = download_image(
        session(),
        add_row(),
        destination,
        policy(chunk_size=31),
        lambda downloaded, total: progress.append((downloaded, total)),
        never_cancel,
    )

    assert result.staging_path.read_bytes() == valid_jpeg
    assert progress
    assert [downloaded for downloaded, _ in progress] == sorted(
        downloaded for downloaded, _ in progress
    )
    assert progress[-1] == (len(valid_jpeg), len(valid_jpeg))


def test_existing_destination_is_untouched_and_blocks_download(http, tmp_path):
    destination = tmp_path / "fish.jpg"
    destination.write_bytes(b"existing")
    http.add(responses.GET, IMAGE_URL, body=b"unused", status=200)

    with pytest.raises(DownloadError, match="destination"):
        download_image(session(), add_row(), destination, policy(), noop, never_cancel)

    assert destination.read_bytes() == b"existing"
    assert not part_for(destination).exists()
    assert len(http.calls) == 0


def test_missing_destination_parent_is_rejected_without_request(http, tmp_path):
    destination = tmp_path / "missing" / "fish.jpg"
    http.add(responses.GET, IMAGE_URL, body=b"unused", status=200)

    with pytest.raises(DownloadError, match="parent"):
        download_image(session(), add_row(), destination, policy(), noop, never_cancel)

    assert len(http.calls) == 0


def test_non_add_row_is_rejected_without_request(http, tmp_path):
    http.add(responses.GET, IMAGE_URL, body=b"unused", status=200)

    with pytest.raises(DownloadError, match="ADD"):
        download_image(
            session(), add_row(action="MOVE"), tmp_path / "fish.jpg", policy(), noop, never_cancel
        )

    assert len(http.calls) == 0


def test_symlink_destination_and_staging_are_refused_without_following(http, tmp_path):
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    http.add(responses.GET, IMAGE_URL, body=b"unused", status=200)

    destination = tmp_path / "fish.jpg"
    try:
        destination.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    with pytest.raises(DownloadError, match="destination"):
        download_image(session(), add_row(), destination, policy(), noop, never_cancel)
    assert outside.read_bytes() == b"outside"

    destination.unlink()
    staging = part_for(destination)
    staging.symlink_to(outside)
    with pytest.raises(DownloadError, match="staging"):
        download_image(session(), add_row(), destination, policy(), noop, never_cancel)
    assert outside.read_bytes() == b"outside"
    assert staging.is_symlink()
    assert len(http.calls) == 0


def test_network_failure_error_chain_does_not_expose_tokenized_url(
    http, fake_sleep, tmp_path
):
    for _ in range(4):
        http.add(responses.GET, IMAGE_URL, body=requests.ConnectionError(IMAGE_URL))

    with pytest.raises(DownloadError, match="network") as caught:
        download_image(session(), add_row(), tmp_path / "fish.jpg", policy(), noop, never_cancel)

    assert_secret_free(caught.value)
    assert [call.args[0] for call in fake_sleep.call_args_list] == [10.0, 20.0, 40.0]


def test_http_failure_error_chain_does_not_expose_tokenized_url(http, tmp_path):
    http.add(responses.GET, IMAGE_URL, status=404)

    with pytest.raises(DownloadError, match="HTTP") as caught:
        download_image(session(), add_row(), tmp_path / "fish.jpg", policy(), noop, never_cancel)

    assert_secret_free(caught.value)
