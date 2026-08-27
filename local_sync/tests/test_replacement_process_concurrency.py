from __future__ import annotations

import csv
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import threading
from uuid import UUID

import imagehash
from PIL import Image
import pytest

from conftest import RECEIPT_TOKEN
from sukaseafood_sync.canonical import write_canonical_manifest
from sukaseafood_sync.downloader import DownloadResult
from sukaseafood_sync.engine import ReceiptItem, SyncCallbacks, SyncEngine
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


def _newer_row(*, action: str, batch_id: UUID, review_id: UUID) -> ManifestRow:
    previous = PurePosixPath(f"images/SF006/{CANDIDATE_ID}.jpg")
    if action == "ADD":
        return _row(batch_id=batch_id, review_id=review_id, review_version=10)
    if action == "MOVE":
        target = PurePosixPath(f"images/SF010/{CANDIDATE_ID}.jpg")
        species_code = "SF010"
    else:
        target = PurePosixPath(f"_removed/{batch_id}/{CANDIDATE_ID}.jpg")
        species_code = "SF006"
    return ManifestRow(
        batch_id=batch_id,
        action=action,  # type: ignore[arg-type]
        candidate_id=CANDIDATE_ID,
        review_id=review_id,
        review_version=10,
        species_code=species_code,
        target_relative_path=target,
        previous_relative_path=previous,
        preview_url="https://images.example.test/10/preview.jpg",
        original_url="https://images.example.test/10/original.jpg",
        source_url="https://catalog.example.test/record/888",
        creator="Researcher",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Researcher / Catalog",
    )


@pytest.mark.parametrize("newer_action", ["ADD", "MOVE", "REMOVE"])
def test_newer_action_reconciles_older_intent_after_real_process_death(
    tmp_path: Path,
    newer_action: str,
) -> None:
    root = tmp_path / "training"
    root.mkdir()
    relative = PurePosixPath(f"images/SF006/{CANDIDATE_ID}.jpg")
    target = root.joinpath(*relative.parts)
    target.parent.mkdir(parents=True)
    old_jpg, old_phash = _jpeg((13, 9), (25, 65, 105))
    generation_9_jpg, _generation_9_phash = _jpeg((19, 11), (150, 60, 20))
    generation_10_jpg, generation_10_phash = _jpeg((23, 17), (20, 155, 65))
    target.write_bytes(old_jpg)
    SyncIndex(root).record_success(
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
    generation_5 = _row(
        batch_id=OLD_BATCH_ID, review_id=OLD_REVIEW_ID, review_version=5
    )
    write_canonical_manifest(
        root,
        ExportManifest((generation_5,), OLD_BATCH_ID, RECEIPT_TOKEN),
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
    source_9.write_bytes(generation_9_jpg)
    batch_9 = UUID("50000000-0000-4000-8000-000000000009")
    review_9 = UUID("60000000-0000-4000-8000-000000000009")
    worker = r'''
import hashlib
from io import BytesIO
import os
from pathlib import Path, PurePosixPath
import sys
import threading
from uuid import UUID

import imagehash
from PIL import Image

from sukaseafood_sync.downloader import DownloadResult
import sukaseafood_sync.engine as engine_module
from sukaseafood_sync.engine import SyncCallbacks, SyncEngine
from sukaseafood_sync.manifest import ExportManifest, ManifestRow

root = Path(sys.argv[1])
candidate = UUID(sys.argv[2])
batch = UUID(sys.argv[3])
review = UUID(sys.argv[4])
source = Path(sys.argv[5])
relative = PurePosixPath(f"images/SF006/{candidate}.jpg")
row = ManifestRow(
    batch_id=batch,
    action="ADD",
    candidate_id=candidate,
    review_id=review,
    review_version=9,
    species_code="SF006",
    target_relative_path=relative,
    previous_relative_path=relative,
    preview_url="https://images.example.test/9/preview.jpg",
    original_url="https://images.example.test/9/original.jpg",
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
    return DownloadResult(
        staging, hashlib.sha256(content).hexdigest(), phash, len(content),
        "JPEG", ".jpg", width, height,
    )

original_promote = engine_module._promote_isolated_staging
def die_after_promotion(*args, **kwargs):
    original_promote(*args, **kwargs)
    os._exit(91)
engine_module._promote_isolated_staging = die_after_promotion
SyncEngine(downloader=download).run(
    ExportManifest((row,), batch, "A" * 20),
    root,
    SyncCallbacks(),
    threading.Event(),
)
'''
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            worker,
            str(root),
            str(CANDIDATE_ID),
            str(batch_9),
            str(review_9),
            str(source_9),
        ],
        cwd=Path(__file__).parents[1],
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert process.returncode == 91, (process.stdout, process.stderr)
    assert SyncIndex(root).get_add_intent(CANDIDATE_ID, review_9, 9, "ADD") is not None

    batch_10 = UUID("50000000-0000-4000-8000-000000000010")
    review_10 = UUID("60000000-0000-4000-8000-000000000010")
    generation_10 = _newer_row(
        action=newer_action, batch_id=batch_10, review_id=review_10
    )

    def download_10(session, manifest_row, destination, policy, progress, cancelled):
        del session, manifest_row, policy, progress, cancelled
        staging = Path(destination).with_name(Path(destination).name + ".part")
        staging.write_bytes(generation_10_jpg)
        with Image.open(BytesIO(generation_10_jpg)) as decoded:
            width, height = decoded.size
        return DownloadResult(
            staging,
            hashlib.sha256(generation_10_jpg).hexdigest(),
            generation_10_phash,
            len(generation_10_jpg),
            "JPEG",
            ".jpg",
            width,
            height,
        )

    outcome = SyncEngine(downloader=download_10).run(
        ExportManifest((generation_10,), batch_10, RECEIPT_TOKEN),
        root,
        SyncCallbacks(),
        threading.Event(),
    )

    index = SyncIndex(root)
    latest = index.latest_for_candidate(CANDIDATE_ID)
    assert outcome.counts == {"succeeded": 1, "failed": 0, "skipped": 0}, (
        outcome.receipt_items
    )
    assert outcome.receipt_items[0].status == "SUCCEEDED"
    assert outcome.receipt_items[0].error is None
    assert latest is not None and latest.review_version == 10
    assert latest.action == newer_action
    expected_content = generation_10_jpg if newer_action == "ADD" else old_jpg
    expected_sha = hashlib.sha256(expected_content).hexdigest()
    final_target = root.joinpath(*generation_10.target_relative_path.parts)
    assert latest.relative_path == generation_10.target_relative_path
    assert latest.sha256 == expected_sha
    assert final_target.read_bytes() == expected_content
    if final_target != target:
        assert not target.exists()
    assert index.get_add_intent(CANDIDATE_ID, review_9, 9, "ADD") is None
    assert not list(root.rglob("*.part"))
    assert not list(root.rglob("*.sync-download"))
    with (root / "canonical_manifest.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as stream:
        canonical = list(csv.DictReader(stream))
    if newer_action == "REMOVE":
        assert canonical == []
    else:
        assert len(canonical) == 1
        assert canonical[0]["review_version"] == "10"
        assert (
            canonical[0]["relative_path"]
            == generation_10.target_relative_path.as_posix()
        )
        assert canonical[0]["sha256"] == expected_sha


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


def test_two_real_processes_same_replacement_are_idempotent(tmp_path: Path) -> None:
    """An exact peer completion after download must become a canonical skip."""

    root = tmp_path / "training"
    root.mkdir()
    relative = PurePosixPath(f"images/SF006/{CANDIDATE_ID}.jpg")
    target = root.joinpath(*relative.parts)
    target.parent.mkdir(parents=True)
    old_jpg, old_phash = _jpeg((13, 9), (25, 65, 105))
    new_jpg, new_phash = _jpeg((23, 17), (20, 155, 65))
    target.write_bytes(old_jpg)
    SyncIndex(root).record_success(
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

    source = tmp_path / "generation-9.jpg"
    source.write_bytes(new_jpg)
    ready_dir = tmp_path / "ready"
    ready_dir.mkdir()
    batch_id = UUID("50000000-0000-4000-8000-000000000009")
    review_id = UUID("60000000-0000-4000-8000-000000000009")
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
batch = UUID(sys.argv[3])
review = UUID(sys.argv[4])
source = Path(sys.argv[5])
ready_dir = Path(sys.argv[6])
worker_id = sys.argv[7]
relative = PurePosixPath(f"images/SF006/{candidate}.jpg")
row = ManifestRow(
    batch_id=batch,
    action="ADD",
    candidate_id=candidate,
    review_id=review,
    review_version=9,
    species_code="SF006",
    target_relative_path=relative,
    previous_relative_path=relative,
    preview_url="https://images.example.test/9/preview.jpg",
    original_url="https://images.example.test/9/original.jpg",
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
    (ready_dir / f"{worker_id}.ready").write_text("ready", encoding="ascii")
    deadline = time.monotonic() + 10
    while len(list(ready_dir.glob("*.ready"))) < 2:
        if time.monotonic() >= deadline:
            raise RuntimeError("peer process did not reach download barrier")
        time.sleep(0.01)
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
print(json.dumps({"counts": result.counts, "items": [
    {"status": item.status, "error": item.error} for item in result.receipt_items
]}, sort_keys=True))
'''
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                worker,
                str(root),
                str(CANDIDATE_ID),
                str(batch_id),
                str(review_id),
                str(source),
                str(ready_dir),
                str(worker_id),
            ],
            cwd=Path(__file__).parents[1],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for worker_id in (1, 2)
    ]
    outputs = [process.communicate(timeout=30) for process in processes]

    assert [process.returncode for process in processes] == [0, 0], outputs
    payloads = [json.loads(stdout) for stdout, _stderr in outputs]
    assert all(payload["items"] == [{"status": "SUCCEEDED", "error": None}] for payload in payloads)
    assert sorted(payload["counts"]["succeeded"] for payload in payloads) == [0, 1]
    assert sorted(payload["counts"]["skipped"] for payload in payloads) == [0, 1]
    assert all(payload["counts"]["failed"] == 0 for payload in payloads)

    latest = SyncIndex(root).latest_for_candidate(CANDIDATE_ID)
    assert latest is not None
    assert latest.review_version == 9
    assert latest.sha256 == hashlib.sha256(new_jpg).hexdigest()
    assert latest.perceptual_hash == new_phash
    assert target.read_bytes() == new_jpg
    with (root / "canonical_manifest.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as stream:
        canonical = list(csv.DictReader(stream))
    assert len(canonical) == 1
    assert canonical[0]["review_version"] == "9"
    assert canonical[0]["sha256"] == latest.sha256
    assert not list(root.rglob("*.part"))
    assert not list(root.rglob("*.sync-download"))
