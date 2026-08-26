from __future__ import annotations

import asyncio
import csv
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sys
import threading

from fastapi.testclient import TestClient
import imagehash
from PIL import Image

from app.main import create_app
from app.models import Decision, ExportBatch, ExportItem
from tests.export_support import (
    csv_rows,
    load_models,
    mao_headers,
    seed_export_database,
)


LOCAL_SRC = Path(__file__).resolve().parents[2] / "local_sync" / "src"
if str(LOCAL_SRC) not in sys.path:
    sys.path.insert(0, str(LOCAL_SRC))

from sukaseafood_sync.downloader import (  # noqa: E402
    DownloadCancelled,
    DownloadResult,
)
from sukaseafood_sync.engine import SyncCallbacks, SyncEngine  # noqa: E402
from sukaseafood_sync.index import SyncIndex  # noqa: E402
from sukaseafood_sync.manifest import load_manifest  # noqa: E402
from sukaseafood_sync.receipt import build_receipt, save_receipt_file  # noqa: E402


def jpeg_bytes() -> tuple[bytes, str, str]:
    stream = BytesIO()
    Image.new("RGB", (11, 7), color=(14, 90, 170)).save(stream, "JPEG")
    content = stream.getvalue()
    with Image.open(BytesIO(content)) as decoded:
        phash = str(imagehash.phash(decoded.convert("RGB"))).lower()
    return content, hashlib.sha256(content).hexdigest(), phash


def test_cancelled_partial_receipt_converges_server_index_files_and_canonical(
    settings, tmp_path
):
    seed = asyncio.run(
        seed_export_database(
            settings, decisions=(Decision.APPROVED, Decision.APPROVED)
        )
    )
    manifest_path = tmp_path / "batch.csv"
    dataset_root = tmp_path / "training"
    dataset_root.mkdir()

    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/v1/admin/exports", json={}, headers=mao_headers(seed, csrf=True)
        )
        assert created.status_code == 201
        batch_id = created.json()["id"]
        downloaded = client.get(
            f"/v1/admin/exports/{batch_id}.csv", headers=mao_headers(seed)
        )
        manifest_path.write_bytes(downloaded.content)
        exported_rows = csv_rows(downloaded)
        manifest = load_manifest(manifest_path)

        content, sha256, phash = jpeg_bytes()
        cancel = threading.Event()
        calls = 0

        def cancel_on_second(
            session, row, destination, policy, progress, cancelled
        ):
            nonlocal calls
            del session, policy, cancelled
            calls += 1
            if calls == 2:
                cancel.set()
                raise DownloadCancelled()
            staging = Path(destination).with_name(Path(destination).name + ".part")
            staging.write_bytes(content)
            progress(len(content), len(content))
            return DownloadResult(
                staging,
                sha256,
                phash,
                len(content),
                "JPEG",
                ".jpg",
                11,
                7,
            )

        result = SyncEngine(
            downloader=cancel_on_second,
            wait=lambda _delay, _event: False,
        ).run(manifest, dataset_root, SyncCallbacks(), cancel)
        partial = build_receipt(manifest, result)
        saved_path = save_receipt_file(partial, dataset_root)
        saved_payload = json.loads(saved_path.read_text("utf-8"))

        uploaded = client.post(
            f"/v1/sync/batches/{batch_id}/receipt",
            json=partial.to_dict(),
            headers={"Authorization": f"Batch {manifest.receipt_token}"},
        )
        next_export = client.post(
            "/v1/admin/exports", json={}, headers=mao_headers(seed, csrf=True)
        )
        counts = client.get(
            "/v1/admin/exports/pending-counts", headers=mao_headers(seed)
        )

    first, second = manifest.rows
    assert result.cancelled is True
    assert result.processed == 1
    assert result.counts == {"succeeded": 1, "failed": 0, "skipped": 0}
    assert set(saved_payload) == {"batch_id", "items"}
    assert len(saved_payload["items"]) == 1
    assert uploaded.status_code == 200
    assert uploaded.json()["status"] == "pending"
    assert uploaded.json()["accepted_candidate_ids"] == [str(first.candidate_id)]
    assert uploaded.json()["pending_candidate_ids"] == [str(second.candidate_id)]
    assert next_export.status_code == 200
    assert next_export.json()["id"] == batch_id
    assert next_export.json()["created"] is False
    assert counts.json()[first.species_code] == 1

    items = asyncio.run(load_models(settings, ExportItem))
    by_candidate = {item.candidate_id: item for item in items}
    assert by_candidate[first.candidate_id].status == "succeeded"
    assert by_candidate[second.candidate_id].status == "pending"
    assert asyncio.run(load_models(settings, ExportBatch))[0].status == "pending"

    index = SyncIndex(dataset_root)
    assert index.latest_for_candidate(first.candidate_id) is not None
    assert index.latest_for_candidate(second.candidate_id) is None
    first_path = dataset_root.joinpath(*first.target_relative_path.parts)
    second_path = dataset_root.joinpath(*second.target_relative_path.parts)
    assert first_path.read_bytes() == content
    assert not second_path.exists()
    with (dataset_root / "canonical_manifest.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as stream:
        canonical = list(csv.DictReader(stream))
    assert [row["candidate_id"] for row in canonical] == [str(first.candidate_id)]
    assert canonical[0]["relative_path"] == first.target_relative_path.as_posix()
    assert not list(dataset_root.rglob("*.part"))
