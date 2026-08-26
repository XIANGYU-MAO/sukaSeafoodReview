from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
from threading import Event
from uuid import UUID

import imagehash
from PIL import Image
import responses

from conftest import BATCH_ID, RECEIPT_TOKEN, valid_row, write_manifest
from sukaseafood_sync.index import SyncIndex, SyncResult
from sukaseafood_sync.service import SyncRequest, run_sync


API_BASE = "http://127.0.0.1:8765/sukaseafood/api/v1"
RECEIPT_URL = f"{API_BASE}/sync/batches/{BATCH_ID}/receipt"
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


def _uuid(number: int) -> str:
    return str(UUID(int=number))


def _image_bytes(format_name: str, color: tuple[int, int, int]) -> bytes:
    output = BytesIO()
    Image.new("RGB", (24, 18), color).save(output, format=format_name)
    return output.getvalue()


def _seed_present(
    root: Path,
    *,
    candidate_id: str,
    review_id: str,
    review_version: int,
    relative_path: str,
    body: bytes,
) -> None:
    target = root.joinpath(*relative_path.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    with Image.open(BytesIO(body)) as decoded:
        perceptual_hash = str(imagehash.phash(decoded))
    SyncIndex(root).record_success(
        SyncResult(
            candidate_id=candidate_id,
            review_id=review_id,
            review_version=review_version,
            action="ADD",
            batch_id=BATCH_ID,
            relative_path=relative_path,
            sha256=hashlib.sha256(body).hexdigest(),
            perceptual_hash=perceptual_hash,
            completed_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
    )


def test_real_incremental_sync_retries_only_failure_and_preserves_server_paths(
    tmp_path: Path,
) -> None:
    """A missing canonical update, unsafe relocation, or broad retry breaks this test."""

    root = tmp_path / "training"
    manifest_dir = tmp_path / "manifest"
    manifest_dir.mkdir()

    ordinary_id, retry_id, invalid_id = (_uuid(10), _uuid(11), _uuid(12))
    composite_id, move_id, remove_id = (_uuid(13), _uuid(14), _uuid(15))
    ordinary_url = "https://ordinary.e2e.test/original.jpg"
    retry_url = "https://retry.e2e.test/original.png"
    invalid_url = "https://invalid.e2e.test/original.jpg"
    composite_url = "https://composite.e2e.test/original.webp"
    ordinary_body = _image_bytes("JPEG", (220, 30, 40))
    retry_body = _image_bytes("PNG", (20, 180, 70))
    composite_body = _image_bytes("WEBP", (40, 70, 210))
    invalid_body = b"not an image: retry me"

    composite_previous = f"images/SF008/{composite_id}.jpg"
    move_previous = f"images/SF008/{move_id}.webp"
    remove_previous = f"images/SF008/{remove_id}.jpg"
    composite_target = f"images/SF008/{composite_id}.webp"
    move_target = f"images/FUTURE_42/{move_id}.webp"
    remove_target = f"_removed/{BATCH_ID}/{remove_id}.jpg"

    _seed_present(
        root,
        candidate_id=composite_id,
        review_id=_uuid(113),
        review_version=1,
        relative_path=composite_previous,
        body=_image_bytes("JPEG", (90, 90, 90)),
    )
    _seed_present(
        root,
        candidate_id=move_id,
        review_id=_uuid(114),
        review_version=1,
        relative_path=move_previous,
        body=_image_bytes("WEBP", (100, 120, 140)),
    )
    _seed_present(
        root,
        candidate_id=remove_id,
        review_id=_uuid(115),
        review_version=1,
        relative_path=remove_previous,
        body=_image_bytes("JPEG", (150, 130, 110)),
    )

    rows = [
        valid_row(
            candidate_id=ordinary_id,
            review_id=_uuid(210),
            review_version=1,
            species_code="FUTURE_42",
            target_relative_path=f"images/FUTURE_42/{ordinary_id}.jpg",
            original_url=ordinary_url,
            source_url="https://ordinary.e2e.test/record/10",
        ),
        valid_row(
            candidate_id=retry_id,
            review_id=_uuid(211),
            review_version=1,
            species_code="SF006",
            target_relative_path=f"images/SF006/{retry_id}.png",
            original_url=retry_url,
            source_url="https://retry.e2e.test/record/11",
        ),
        valid_row(
            candidate_id=invalid_id,
            review_id=_uuid(212),
            review_version=1,
            species_code="SF007",
            target_relative_path=f"images/SF007/{invalid_id}.jpg",
            original_url=invalid_url,
            source_url="https://invalid.e2e.test/record/12",
        ),
        valid_row(
            candidate_id=composite_id,
            review_id=_uuid(213),
            review_version=2,
            species_code="SF008",
            target_relative_path=composite_target,
            previous_relative_path=composite_previous,
            original_url=composite_url,
            source_url="https://composite.e2e.test/record/13",
        ),
        valid_row(
            action="MOVE",
            candidate_id=move_id,
            review_id=_uuid(214),
            review_version=2,
            species_code="FUTURE_42",
            target_relative_path=move_target,
            previous_relative_path=move_previous,
            original_url="https://move.e2e.test/original.webp",
            source_url="https://move.e2e.test/record/14",
        ),
        valid_row(
            action="REMOVE",
            candidate_id=remove_id,
            review_id=_uuid(215),
            review_version=2,
            species_code="SF008",
            target_relative_path=remove_target,
            previous_relative_path=remove_previous,
            original_url="https://remove.e2e.test/original.jpg",
            source_url="https://remove.e2e.test/record/15",
        ),
    ]
    manifest_path = write_manifest(manifest_dir, rows=rows)
    captured_receipts: list[dict[str, object]] = []
    successful_ids = [ordinary_id, retry_id, composite_id, move_id, remove_id]

    def receipt_response(request):
        payload = json.loads(request.body.decode("utf-8"))
        captured_receipts.append(payload)
        if len(captured_receipts) == 1:
            return 200, {"Content-Type": "application/json"}, "{}"
        response = {
            "batch_id": str(BATCH_ID),
            "status": "pending",
            "accepted_candidate_ids": successful_ids,
            "pending_candidate_ids": [invalid_id],
        }
        return 200, {"Content-Type": "application/json"}, json.dumps(response)

    with responses.RequestsMock(assert_all_requests_are_fired=False) as http:
        http.add(responses.GET, ordinary_url, body=ordinary_body, status=200)
        http.add(
            responses.GET,
            retry_url,
            status=429,
            headers={"Retry-After": "0"},
        )
        http.add(responses.GET, retry_url, body=retry_body, status=200)
        http.add(responses.GET, invalid_url, body=invalid_body, status=200)
        http.add(responses.GET, composite_url, body=composite_body, status=200)
        http.add_callback(responses.POST, RECEIPT_URL, callback=receipt_response)
        http.add(responses.GET, invalid_url, body=invalid_body, status=200)
        http.add_callback(responses.POST, RECEIPT_URL, callback=receipt_response)

        first = run_sync(
            SyncRequest(manifest_path, root, api_base=API_BASE),
            Event(),
        )
        canonical_after_first = (root / "canonical_manifest.csv").read_bytes()
        second = run_sync(
            SyncRequest(manifest_path, root, api_base=API_BASE),
            Event(),
        )

        requested_urls = [call.request.url for call in http.calls]

    assert first.exit_code == 4
    assert first.offline_receipt_path == root / f"download_receipt-{BATCH_ID}.json"
    assert second.exit_code == 3
    assert dict(first.counts) == {"succeeded": 5, "failed": 1, "skipped": 0}
    assert dict(second.counts) == {"succeeded": 0, "failed": 1, "skipped": 5}
    assert requested_urls.count(ordinary_url) == 1
    assert requested_urls.count(retry_url) == 2
    assert requested_urls.count(composite_url) == 1
    assert requested_urls.count(invalid_url) == 2

    exact_item_keys = {
        "candidate_id",
        "review_id",
        "review_version",
        "status",
        "sha256",
        "relative_path",
        "error",
    }
    expected_receipt_paths = {
        ordinary_id: f"images/FUTURE_42/{ordinary_id}.jpg",
        retry_id: f"images/SF006/{retry_id}.png",
        invalid_id: None,
        composite_id: composite_target,
        move_id: move_target,
        remove_id: remove_target,
    }
    assert len(captured_receipts) == 2
    for payload in captured_receipts:
        assert set(payload) == {"items"}
        assert len(payload["items"]) == len(rows)
        for item in payload["items"]:
            assert set(item) == exact_item_keys
            assert item["relative_path"] == expected_receipt_paths[item["candidate_id"]]
        serialized = json.dumps(payload, sort_keys=True)
        for forbidden in (
            "action",
            "perceptual_hash",
            "original_url",
            "source_url",
            "preview_url",
            "receipt_token",
            RECEIPT_TOKEN,
            "proxy",
            "attribution",
        ):
            assert forbidden not in serialized

    offline_payload = json.loads(first.offline_receipt_path.read_text("utf-8"))
    assert set(offline_payload) == {"batch_id", "items"}
    assert all(set(item) == exact_item_keys for item in offline_payload["items"])

    assert not root.joinpath(*composite_previous.split("/")).exists()
    assert root.joinpath(*composite_target.split("/")).read_bytes() == composite_body
    assert not root.joinpath(*move_previous.split("/")).exists()
    assert root.joinpath(*move_target.split("/")).is_file()
    assert not root.joinpath(*remove_previous.split("/")).exists()
    assert root.joinpath(*remove_target.split("/")).is_file()
    assert not list(root.rglob("*.part"))

    canonical_path = root / "canonical_manifest.csv"
    assert canonical_path.read_bytes() == canonical_after_first
    with canonical_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        canonical_rows = list(reader)
    assert tuple(reader.fieldnames or ()) == CANONICAL_COLUMNS
    canonical_by_id = {row["candidate_id"]: row for row in canonical_rows}
    assert set(canonical_by_id) == {
        ordinary_id,
        retry_id,
        composite_id,
        move_id,
    }
    for candidate_id, path in expected_receipt_paths.items():
        if candidate_id in canonical_by_id:
            assert canonical_by_id[candidate_id]["relative_path"] == path
    assert canonical_by_id[ordinary_id]["species_code"] == "FUTURE_42"

    reopened = SyncIndex(root)
    for row in rows:
        record = reopened.get_completed(
            row["candidate_id"],
            row["review_id"],
            int(row["review_version"]),
            row["action"],
        )
        if row["candidate_id"] == invalid_id:
            assert record is None
        else:
            assert record is not None
            assert record.relative_path.as_posix() == expected_receipt_paths[row["candidate_id"]]
            assert record.receipt_submitted_at is not None

    log_text = "".join(
        path.read_text("utf-8") for path in sorted((root / "logs").glob("*.jsonl"))
    )
    for secret in (
        RECEIPT_TOKEN,
        ordinary_url,
        retry_url,
        invalid_url,
        composite_url,
        API_BASE,
    ):
        assert secret not in log_text
