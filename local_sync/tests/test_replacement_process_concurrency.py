from __future__ import annotations

import csv
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
from uuid import UUID

import imagehash
from PIL import Image

from conftest import RECEIPT_TOKEN
from sukaseafood_sync.canonical import write_canonical_manifest
from sukaseafood_sync.engine import ReceiptItem
from sukaseafood_sync.index import SyncIndex, SyncResult
from sukaseafood_sync.manifest import ExportManifest, ManifestRow


CANDIDATE_ID = UUID("88888888-8888-4888-8888-888888888888")
OLD_BATCH_ID = UUID("50000000-0000-4000-8000-000000000005")
OLD_REVIEW_ID = UUID("60000000-0000-4000-8000-000000000005")


def _jpeg(size: tuple[int, int], color: tuple[int, int, int]) -> tuple[bytes, str]:
    stream = BytesIO()
    Image.new("RGB", size, color).save(stream, "JPEG")
    content = stream.getvalue()
    with Image.open(BytesIO(content)) as decoded:
        perceptual_hash = str(imagehash.phash(decoded.convert("RGB"))).lower()
    return content, perceptual_hash


def _row(
    *, batch_id: UUID, review_id: UUID, review_version: int
) -> ManifestRow:
    relative = PurePosixPath(f"images/SF006/{CANDIDATE_ID}.jpg")
    return ManifestRow(
        batch_id=batch_id,
        action="ADD",
        candidate_id=CANDIDATE_ID,
        review_id=review_id,
        review_version=review_version,
        species_code="SF006",
        target_relative_path=relative,
        previous_relative_path=relative if review_version > 5 else None,
        preview_url=f"https://images.example.test/{review_version}/preview.jpg",
        original_url=f"https://images.example.test/{review_version}/original.jpg",
        source_url="https://catalog.example.test/record/888",
        creator="Researcher",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Researcher / Catalog",
    )


def test_two_real_process_replacements_converge_to_highest_generation(
    tmp_path: Path,
) -> None:
    """Removing the root lock or generation recheck lets generation 9 win last."""

    root = tmp_path / "training"
    root.mkdir()
    relative = PurePosixPath(f"images/SF006/{CANDIDATE_ID}.jpg")
    target = root.joinpath(*relative.parts)
    target.parent.mkdir(parents=True)
    old_jpg, old_phash = _jpeg((13, 9), (25, 65, 105))
    generation_9_jpg, _generation_9_phash = _jpeg((19, 11), (150, 60, 20))
    generation_10_jpg, generation_10_phash = _jpeg((23, 17), (20, 155, 65))
    target.write_bytes(old_jpg)
    index = SyncIndex(root)
    index.record_success(
        SyncResult(
            candidate_id=CANDIDATE_ID,
            review_id=OLD_REVIEW_ID,
            review_version=5,
            action="ADD",
            batch_id=OLD_BATCH_ID,
            relative_path=relative,
            sha256=hashlib.sha256(old_jpg).hexdigest(),
            perceptual_hash=old_phash,
        )
    )
    old_row = _row(
        batch_id=OLD_BATCH_ID, review_id=OLD_REVIEW_ID, review_version=5
    )
    write_canonical_manifest(
        root,
        ExportManifest((old_row,), OLD_BATCH_ID, RECEIPT_TOKEN),
        (
            ReceiptItem(
                str(CANDIDATE_ID),
                str(OLD_REVIEW_ID),
                5,
                "SUCCEEDED",
                hashlib.sha256(old_jpg).hexdigest(),
                relative.as_posix(),
                None,
            ),
        ),
    )

    source_9 = tmp_path / "generation-9.jpg"
    source_10 = tmp_path / "generation-10.jpg"
    source_9.write_bytes(generation_9_jpg)
    source_10.write_bytes(generation_10_jpg)
    ready_dir = tmp_path / "ready"
    ready_dir.mkdir()
    worker = r'''
import hashlib
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import sys
import threading
import time
from uuid import UUID

import imagehash
from PIL import Image

from sukaseafood_sync.downloader import DownloadResult
from sukaseafood_sync.engine import SyncCallbacks, SyncEngine
from sukaseafood_sync.manifest import ExportManifest, ManifestRow

root = Path(sys.argv[1])
candidate = UUID(sys.argv[2])
generation = int(sys.argv[3])
batch = UUID(sys.argv[4])
review = UUID(sys.argv[5])
source = Path(sys.argv[6])
ready_dir = Path(sys.argv[7])
relative = PurePosixPath(f"images/SF006/{candidate}.jpg")
row = ManifestRow(
    batch_id=batch,
    action="ADD",
    candidate_id=candidate,
    review_id=review,
    review_version=generation,
    species_code="SF006",
    target_relative_path=relative,
    previous_relative_path=relative,
    preview_url=f"https://images.example.test/{generation}/preview.jpg",
    original_url=f"https://images.example.test/{generation}/original.jpg",
    source_url="https://catalog.example.test/record/888",
    creator="Researcher",
    license="CC BY 4.0",
    license_url="https://creativecommons.org/licenses/by/4.0/",
    attribution="Researcher / Catalog",
)

def download(session, manifest_row, destination, policy, progress, cancelled):
    del session, manifest_row, policy, progress, cancelled
    content = source.read_bytes()
    staging = Path(destination).with_name(Path(destination).name + ".part")
    staging.write_bytes(content)
    with Image.open(BytesIO(content)) as decoded:
        width, height = decoded.size
        phash = str(imagehash.phash(decoded.convert("RGB"))).lower()
    (ready_dir / f"{generation}.ready").write_text("ready", encoding="ascii")
    deadline = time.monotonic() + 10
    while len(list(ready_dir.glob("*.ready"))) < 2:
        if time.monotonic() >= deadline:
            raise RuntimeError("peer process did not reach download barrier")
        time.sleep(0.01)
    if generation == 9:
        time.sleep(0.2)
    return DownloadResult(
        staging,
        hashlib.sha256(content).hexdigest(),
        phash,
        len(content),
        "JPEG",
        ".jpg",
        width,
        height,
    )

result = SyncEngine(downloader=download).run(
    ExportManifest((row,), batch, "A" * 20),
    root,
    SyncCallbacks(),
    threading.Event(),
)
print(json.dumps({
    "counts": result.counts,
    "items": [
        {"version": item.review_version, "status": item.status, "error": item.error}
        for item in result.receipt_items
    ],
}, sort_keys=True))
'''
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    inputs = (
        (
            9,
            UUID("50000000-0000-4000-8000-000000000009"),
            UUID("60000000-0000-4000-8000-000000000009"),
            source_9,
        ),
        (
            10,
            UUID("50000000-0000-4000-8000-000000000010"),
            UUID("60000000-0000-4000-8000-000000000010"),
            source_10,
        ),
    )
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                worker,
                str(root),
                str(CANDIDATE_ID),
                str(generation),
                str(batch_id),
                str(review_id),
                str(source),
                str(ready_dir),
            ],
            cwd=Path(__file__).parents[1],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for generation, batch_id, review_id, source in inputs
    ]
    outputs = [process.communicate(timeout=30) for process in processes]

    assert [process.returncode for process in processes] == [0, 0], outputs
    payloads = [json.loads(stdout) for stdout, _stderr in outputs]
    by_generation = {
        item["version"]: item
        for payload in payloads
        for item in payload["items"]
    }
    assert by_generation[10] == {"version": 10, "status": "SUCCEEDED", "error": None}
    assert by_generation[9] in (
        {"version": 9, "status": "SUCCEEDED", "error": None},
        {"version": 9, "status": "FAILED", "error": "STALE_GENERATION"},
    )

    latest = SyncIndex(root).latest_for_candidate(CANDIDATE_ID)
    assert latest is not None
    assert latest.review_version == 10
    assert latest.sha256 == hashlib.sha256(generation_10_jpg).hexdigest()
    assert latest.perceptual_hash == generation_10_phash
    assert target.read_bytes() == generation_10_jpg
    with (root / "canonical_manifest.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as stream:
        canonical = list(csv.DictReader(stream))
    assert len(canonical) == 1
    assert canonical[0]["review_version"] == "10"
    assert canonical[0]["sha256"] == latest.sha256
    assert not list(root.rglob("*.part"))
    assert not list(root.rglob("*.sync-download"))
