from __future__ import annotations

import csv
from contextlib import contextmanager
from dataclasses import fields, replace
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import queue
import sys
import threading
import time
from uuid import UUID

import imagehash
from PIL import Image
import pytest

from conftest import BATCH_ID, RECEIPT_TOKEN
from sukaseafood_sync import operations
from sukaseafood_sync.downloader import DownloadCancelled, DownloadError, DownloadResult
from sukaseafood_sync.engine import (
    BatchResult,
    ProgressEvent,
    ReceiptItem,
    SyncCallbacks,
    SyncEngine,
    SyncEngineError,
)
from sukaseafood_sync.index import SyncIndex, SyncIndexError, SyncResult
from sukaseafood_sync.manifest import ExportManifest, ManifestRow
from sukaseafood_sync.operations import OperationError


def candidate(number: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{number:012d}")


def row(
    number: int,
    action: str = "ADD",
    *,
    species_code: str = "FUTURE_SPECIES_42",
    source_url: str = "https://www.inaturalist.org/observations/1",
    original_url: str | None = None,
) -> ManifestRow:
    candidate_id = candidate(number)
    previous = PurePosixPath(f"images/OLD/{candidate_id}.jpg")
    if action == "REMOVE":
        target = PurePosixPath(f"_removed/{BATCH_ID}/{candidate_id}.jpg")
    else:
        target = PurePosixPath(f"images/{species_code}/{candidate_id}.jpg")
    return ManifestRow(
        batch_id=BATCH_ID,
        action=action,  # type: ignore[arg-type]
        candidate_id=candidate_id,
        review_id=UUID(f"10000000-0000-4000-8000-{number:012d}"),
        review_version=2,
        species_code=species_code,
        target_relative_path=target,
        previous_relative_path=previous if action in {"MOVE", "REMOVE"} else None,
        preview_url=f"https://images.example.test/{number}/preview.jpg",
        original_url=original_url or f"https://images.example.test/{number}/original.jpg",
        source_url=source_url,
        creator="Researcher",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Researcher / Catalog",
    )


def manifest(*rows: ManifestRow) -> ExportManifest:
    return ExportManifest(tuple(rows), BATCH_ID, RECEIPT_TOKEN)


def jpeg() -> tuple[bytes, str]:
    stream = BytesIO()
    image = Image.new("RGB", (11, 7), color=(14, 90, 170))
    image.save(stream, "JPEG")
    content = stream.getvalue()
    with Image.open(BytesIO(content)) as decoded:
        phash = str(imagehash.phash(decoded.convert("RGB"))).lower()
    return content, phash


JPEG, PHASH = jpeg()
SHA256 = hashlib.sha256(JPEG).hexdigest()


def jpeg_variant(
    size: tuple[int, int], color: tuple[int, int, int]
) -> tuple[bytes, str, str]:
    stream = BytesIO()
    Image.new("RGB", size, color=color).save(stream, "JPEG")
    content = stream.getvalue()
    with Image.open(BytesIO(content)) as decoded:
        phash = str(imagehash.phash(decoded.convert("RGB"))).lower()
    return content, phash, hashlib.sha256(content).hexdigest()


def local(root: Path, relative: PurePosixPath) -> Path:
    return root.joinpath(*relative.parts)


def fake_download(root: Path, calls: list[UUID], *, fail: set[UUID] | None = None):
    failures = fail or set()

    def download(session, manifest_row, destination, policy, progress, cancel):
        calls.append(manifest_row.candidate_id)
        if manifest_row.candidate_id in failures:
            raise DownloadError(
                "image download failed after network retries", code="NETWORK_ERROR"
            )
        staging = Path(destination).with_name(Path(destination).name + ".part")
        staging.write_bytes(JPEG)
        progress(len(JPEG), len(JPEG))
        return DownloadResult(staging, SHA256, PHASH, len(JPEG), "JPEG", ".jpg", 11, 7)

    return download


def seed_prior(root: Path, index: SyncIndex, item: ManifestRow) -> None:
    assert item.previous_relative_path is not None
    path = local(root, item.previous_relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(JPEG)
    index.record_success(
        SyncResult(
            candidate_id=item.candidate_id,
            review_id=UUID(f"20000000-0000-4000-8000-{item.candidate_id.int % 10**12:012d}"),
            review_version=1,
            action="ADD",
            batch_id=BATCH_ID,
            relative_path=item.previous_relative_path,
            sha256=SHA256,
            perceptual_hash=PHASH,
        )
    )


def seed_exact(root: Path, index: SyncIndex, item: ManifestRow) -> None:
    path = local(root, item.target_relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(JPEG)
    index.record_success(
        SyncResult(
            candidate_id=item.candidate_id,
            review_id=item.review_id,
            review_version=item.review_version,
            action=item.action,
            batch_id=item.batch_id,
            relative_path=item.target_relative_path,
            sha256=SHA256,
            perceptual_hash=PHASH,
        )
    )


class Session:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_same_path_identical_bytes_advance_generation_without_replacing_file(
    sync_root: Path,
) -> None:
    sync_root.mkdir()
    generation_9 = replace(
        row(69),
        review_id=candidate(6909),
        review_version=9,
    )
    generation_9 = replace(
        generation_9,
        previous_relative_path=generation_9.target_relative_path,
        original_url="https://images.example.test/69/changed-url.jpg",
    )
    seed_prior(sync_root, SyncIndex(sync_root), generation_9)
    target = local(sync_root, generation_9.target_relative_path)
    before = target.stat()
    calls: list[UUID] = []

    outcome = SyncEngine(
        session=Session(), downloader=fake_download(sync_root, calls)
    ).run(
        ExportManifest((generation_9,), generation_9.batch_id, RECEIPT_TOKEN),
        sync_root,
        SyncCallbacks(),
        threading.Event(),
    )

    after = target.stat()
    latest = SyncIndex(sync_root).latest_for_candidate(generation_9.candidate_id)
    assert calls == [generation_9.candidate_id]
    assert outcome.counts == {"succeeded": 1, "failed": 0, "skipped": 0}
    assert latest is not None and latest.review_version == 9
    assert latest.sha256 == SHA256 and latest.perceptual_hash == PHASH
    assert (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
    assert target.read_bytes() == JPEG
    assert not (sync_root / "_removed" / str(generation_9.batch_id)).exists()
    assert not list(sync_root.rglob("*.part"))
    assert not list(sync_root.rglob("*.sync-download"))


def test_completed_operation_reissued_in_new_batch_returns_new_truthful_receipt(
    sync_root: Path,
) -> None:
    sync_root.mkdir()
    original = row(70)
    first_calls: list[UUID] = []
    first = SyncEngine(
        session=Session(), downloader=fake_download(sync_root, first_calls)
    ).run(
        ExportManifest((original,), original.batch_id, RECEIPT_TOKEN),
        sync_root,
        SyncCallbacks(),
        threading.Event(),
    )
    new_batch = candidate(7099)
    reissued = replace(original, batch_id=new_batch)

    def no_network(*_args, **_kwargs):
        raise AssertionError("exact completed operation must not download again")

    second = SyncEngine(session=Session(), downloader=no_network).run(
        ExportManifest((reissued,), new_batch, RECEIPT_TOKEN),
        sync_root,
        SyncCallbacks(),
        threading.Event(),
    )

    assert first.counts == {"succeeded": 1, "failed": 0, "skipped": 0}
    assert second.batch_id == str(new_batch)
    assert second.counts == {"succeeded": 0, "failed": 0, "skipped": 1}
    assert second.receipt_items == first.receipt_items
    assert second.receipt_items[0].status == "SUCCEEDED"
    assert second.receipt_items[0].sha256 == SHA256
    assert second.receipt_items[0].relative_path == original.target_relative_path.as_posix()


def test_replacement_cancellation_before_swap_recovers_without_network(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Removing replacement preparation makes a downloaded cancellation unrecoverable."""

    sync_root.mkdir()
    generation_5 = replace(row(64), review_version=5)
    generation_9 = replace(
        generation_5,
        review_id=candidate(6409),
        review_version=9,
        previous_relative_path=generation_5.target_relative_path,
        original_url="https://images.example.test/64/replacement.jpg",
    )
    old_jpg, old_phash, old_sha = jpeg_variant((13, 9), (30, 70, 110))
    new_jpg, new_phash, new_sha = jpeg_variant((19, 11), (180, 40, 25))

    def download_bytes(content: bytes, phash: str, sha256: str, *, cancel=None):
        def download(session, item, destination, policy, progress, cancelled):
            del session, item, policy, progress, cancelled
            staging = Path(destination).with_name(Path(destination).name + ".part")
            staging.write_bytes(content)
            if cancel is not None:
                cancel.set()
            with Image.open(BytesIO(content)) as decoded:
                width, height = decoded.size
            return DownloadResult(
                staging, sha256, phash, len(content), "JPEG", ".jpg", width, height
            )

        return download

    monkeypatch.setattr(
        "sukaseafood_sync.engine.download_image",
        download_bytes(old_jpg, old_phash, old_sha),
    )
    seeded = SyncEngine(session=Session()).run(
        manifest(generation_5), sync_root, SyncCallbacks(), threading.Event()
    )
    assert seeded.counts == {"succeeded": 1, "failed": 0, "skipped": 0}

    cancel_event = threading.Event()
    monkeypatch.setattr(
        "sukaseafood_sync.engine.download_image",
        download_bytes(new_jpg, new_phash, new_sha, cancel=cancel_event),
    )
    cancelled = SyncEngine(session=Session()).run(
        manifest(generation_9), sync_root, SyncCallbacks(), cancel_event
    )

    target = local(sync_root, generation_9.target_relative_path)
    pending = SyncIndex(sync_root).get_add_intent(
        generation_9.candidate_id,
        generation_9.review_id,
        generation_9.review_version,
        "ADD",
    )
    assert cancelled.cancelled
    assert cancelled.receipt_items == ()
    assert pending is not None and pending.prior_sha256 == old_sha
    assert target.read_bytes() == old_jpg

    def no_network(*_args, **_kwargs):
        raise AssertionError("replacement recovery must precede network access")

    monkeypatch.setattr("sukaseafood_sync.engine.download_image", no_network)
    resumed = SyncEngine(session=Session()).run(
        manifest(generation_9), sync_root, SyncCallbacks(), threading.Event()
    )

    assert resumed.counts == {"succeeded": 1, "failed": 0, "skipped": 0}
    assert resumed.receipt_items[0].sha256 == new_sha
    assert target.read_bytes() == new_jpg
    with (sync_root / "canonical_manifest.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as stream:
        canonical = list(csv.DictReader(stream))
    assert canonical[0]["review_version"] == "9"
    assert canonical[0]["sha256"] == new_sha
    assert SyncIndex(sync_root).max_generation(generation_9.candidate_id) == 9


def test_replacement_death_after_staging_promotion_recovers_without_network(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The promoted replacement stage must already have durable recovery intent."""

    import sukaseafood_sync.engine as engine_module

    sync_root.mkdir()
    generation_5 = replace(row(66), review_version=5)
    generation_9 = replace(
        generation_5,
        review_id=candidate(6609),
        review_version=9,
        previous_relative_path=generation_5.target_relative_path,
        original_url="https://images.example.test/66/replacement.jpg",
    )
    old_jpg, old_phash, old_sha = jpeg_variant((13, 9), (35, 75, 115))
    new_jpg, new_phash, new_sha = jpeg_variant((19, 13), (175, 45, 25))

    def download_bytes(content: bytes, phash: str, sha256: str):
        def download(session, item, destination, policy, progress, cancelled):
            del session, item, policy, progress, cancelled
            staging = Path(destination).with_name(Path(destination).name + ".part")
            staging.write_bytes(content)
            with Image.open(BytesIO(content)) as decoded:
                width, height = decoded.size
            return DownloadResult(
                staging, sha256, phash, len(content), "JPEG", ".jpg", width, height
            )

        return download

    monkeypatch.setattr(
        "sukaseafood_sync.engine.download_image",
        download_bytes(old_jpg, old_phash, old_sha),
    )
    SyncEngine(session=Session()).run(
        manifest(generation_5), sync_root, SyncCallbacks(), threading.Event()
    )

    original_promote = engine_module._promote_isolated_staging
    died = False

    def die_after_promotion(*args, **kwargs):
        nonlocal died
        promoted = original_promote(*args, **kwargs)
        if not died:
            died = True
            raise KeyboardInterrupt("simulated death after stage promotion")
        return promoted

    monkeypatch.setattr(engine_module, "_promote_isolated_staging", die_after_promotion)
    monkeypatch.setattr(
        "sukaseafood_sync.engine.download_image",
        download_bytes(new_jpg, new_phash, new_sha),
    )
    with pytest.raises(KeyboardInterrupt, match="after stage promotion"):
        SyncEngine(session=Session()).run(
            manifest(generation_9), sync_root, SyncCallbacks(), threading.Event()
        )

    pending = SyncIndex(sync_root).get_add_intent(
        generation_9.candidate_id,
        generation_9.review_id,
        generation_9.review_version,
        "ADD",
    )
    assert died
    assert pending is not None

    def no_network(*_args, **_kwargs):
        raise AssertionError("durable replacement recovery must precede network access")

    monkeypatch.setattr("sukaseafood_sync.engine.download_image", no_network)
    resumed = SyncEngine(session=Session()).run(
        manifest(generation_9), sync_root, SyncCallbacks(), threading.Event()
    )

    target = local(sync_root, generation_9.target_relative_path)
    assert resumed.counts == {"succeeded": 1, "failed": 0, "skipped": 0}
    assert resumed.receipt_items[0].sha256 == new_sha
    assert target.read_bytes() == new_jpg
    assert SyncIndex(sync_root).max_generation(generation_9.candidate_id) == 9
    assert not list(sync_root.rglob("*.part"))
    assert not list(sync_root.rglob("*.sync-download"))


@pytest.mark.parametrize(
    "newer_corruption",
    (None, "target", "canonical"),
    ids=("valid", "tampered-target", "missing-canonical"),
)
def test_terminal_barrier_preserves_superseded_receipt_for_consistent_newer_state(
    sync_root: Path, newer_corruption: str | None
) -> None:
    """A newer canonical success must not invalidate an older truthful receipt."""

    sync_root.mkdir()
    batch_5 = candidate(9505)
    batch_9 = candidate(9509)
    batch_10 = candidate(9510)
    generation_5 = replace(row(67), batch_id=batch_5, review_version=5)
    generation_9 = replace(
        generation_5,
        batch_id=batch_9,
        review_id=candidate(6709),
        review_version=9,
        previous_relative_path=generation_5.target_relative_path,
        original_url="https://images.example.test/67/generation-9.jpg",
    )
    generation_10 = replace(
        generation_9,
        batch_id=batch_10,
        review_id=candidate(6710),
        review_version=10,
        original_url="https://images.example.test/67/generation-10.jpg",
    )
    old_jpg, old_phash, old_sha = jpeg_variant((13, 9), (30, 70, 110))
    generation_9_jpg, generation_9_phash, generation_9_sha = jpeg_variant(
        (19, 13), (175, 45, 25)
    )
    generation_10_jpg, generation_10_phash, generation_10_sha = jpeg_variant(
        (23, 17), (20, 155, 65)
    )
    images = {
        5: (old_jpg, old_phash, old_sha),
        9: (generation_9_jpg, generation_9_phash, generation_9_sha),
        10: (generation_10_jpg, generation_10_phash, generation_10_sha),
    }

    def download(session, item, destination, policy, progress, cancelled):
        del session, policy, progress, cancelled
        content, phash, sha256 = images[item.review_version]
        staging = Path(destination).with_name(Path(destination).name + ".part")
        staging.write_bytes(content)
        with Image.open(BytesIO(content)) as decoded:
            width, height = decoded.size
        return DownloadResult(
            staging, sha256, phash, len(content), "JPEG", ".jpg", width, height
        )

    def export(item: ManifestRow) -> ExportManifest:
        return ExportManifest((item,), item.batch_id, RECEIPT_TOKEN)

    SyncEngine(session=Session(), downloader=download).run(
        export(generation_5), sync_root, SyncCallbacks(), threading.Event()
    )

    generation_10_result: BatchResult | None = None
    superseded = False

    def supersede_before_barrier(event: ProgressEvent) -> None:
        nonlocal generation_10_result, superseded
        if event.phase != "SUCCEEDED" or superseded:
            return
        superseded = True
        generation_10_result = SyncEngine(
            session=Session(), downloader=download
        ).run(
            export(generation_10),
            sync_root,
            SyncCallbacks(),
            threading.Event(),
        )
        if newer_corruption == "target":
            local(sync_root, generation_10.target_relative_path).write_bytes(old_jpg)
        elif newer_corruption == "canonical":
            (sync_root / "canonical_manifest.csv").unlink()

    generation_9_engine = SyncEngine(session=Session(), downloader=download)
    generation_9_callbacks = SyncCallbacks(progress=supersede_before_barrier)
    if newer_corruption is None:
        generation_9_result = generation_9_engine.run(
            export(generation_9),
            sync_root,
            generation_9_callbacks,
            threading.Event(),
        )
    else:
        with pytest.raises(SyncEngineError, match="RECOVERY_BARRIER_FAILED"):
            generation_9_engine.run(
                export(generation_9),
                sync_root,
                generation_9_callbacks,
                threading.Event(),
            )
        generation_9_result = None

    assert superseded
    assert generation_10_result is not None
    assert generation_10_result.counts == {
        "succeeded": 1,
        "failed": 0,
        "skipped": 0,
    }
    assert generation_10_result.receipt_items[0].status == "SUCCEEDED"
    assert generation_10_result.receipt_items[0].sha256 == generation_10_sha

    index = SyncIndex(sync_root)
    exact_9 = index.get_completed(
        generation_9.candidate_id,
        generation_9.review_id,
        generation_9.review_version,
        "ADD",
    )
    latest = index.latest_for_candidate(generation_10.candidate_id)
    assert exact_9 is not None and exact_9.sha256 == generation_9_sha
    assert latest is not None and latest.review_version == 10
    assert latest.sha256 == generation_10_sha
    target = local(sync_root, generation_10.target_relative_path)
    if newer_corruption == "target":
        assert target.read_bytes() == old_jpg
        return
    assert target.read_bytes() == generation_10_jpg
    if newer_corruption == "canonical":
        assert not (sync_root / "canonical_manifest.csv").exists()
        return
    assert generation_9_result is not None
    assert generation_9_result.counts == {
        "succeeded": 1,
        "failed": 0,
        "skipped": 0,
    }
    assert generation_9_result.receipt_items[0].status == "SUCCEEDED"
    assert generation_9_result.receipt_items[0].sha256 == generation_9_sha
    with (sync_root / "canonical_manifest.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as stream:
        canonical = list(csv.DictReader(stream))
    assert len(canonical) == 1
    assert canonical[0]["review_version"] == "10"
    assert canonical[0]["sha256"] == generation_10_sha
    assert not list(sync_root.rglob("*.part"))
    assert not list(sync_root.rglob("*.sync-download"))


def test_replacement_cancellation_after_backup_keeps_success_canonical(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancellation observed after the swap boundary must not hide durable success."""

    sync_root.mkdir()
    generation_5 = replace(row(65), review_version=5)
    generation_9 = replace(
        generation_5,
        review_id=candidate(6509),
        review_version=9,
        previous_relative_path=generation_5.target_relative_path,
        original_url="https://images.example.test/65/replacement.jpg",
    )
    old_jpg, old_phash, old_sha = jpeg_variant((13, 9), (20, 60, 100))
    new_jpg, new_phash, new_sha = jpeg_variant((21, 15), (170, 45, 30))

    def download_bytes(content: bytes, phash: str, sha256: str):
        def download(session, item, destination, policy, progress, cancelled):
            del session, item, policy, progress, cancelled
            staging = Path(destination).with_name(Path(destination).name + ".part")
            staging.write_bytes(content)
            with Image.open(BytesIO(content)) as decoded:
                width, height = decoded.size
            return DownloadResult(
                staging, sha256, phash, len(content), "JPEG", ".jpg", width, height
            )

        return download

    monkeypatch.setattr(
        "sukaseafood_sync.engine.download_image",
        download_bytes(old_jpg, old_phash, old_sha),
    )
    SyncEngine(session=Session()).run(
        manifest(generation_5), sync_root, SyncCallbacks(), threading.Event()
    )

    cancel_event = threading.Event()
    original_link = operations._link_no_clobber

    def cancel_after_backup(source, source_owned, target, expected_sha256):
        linked = original_link(source, source_owned, target, expected_sha256)
        if "_removed" in Path(target).parts:
            cancel_event.set()
        return linked

    monkeypatch.setattr(operations, "_link_no_clobber", cancel_after_backup)
    monkeypatch.setattr(
        "sukaseafood_sync.engine.download_image",
        download_bytes(new_jpg, new_phash, new_sha),
    )
    result = SyncEngine(session=Session()).run(
        manifest(generation_9), sync_root, SyncCallbacks(), cancel_event
    )

    target = local(sync_root, generation_9.target_relative_path)
    assert cancel_event.is_set()
    assert result.cancelled
    assert result.receipt_items[0].status == "SUCCEEDED"
    assert result.receipt_items[0].sha256 == new_sha
    assert target.read_bytes() == new_jpg
    with (sync_root / "canonical_manifest.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as stream:
        canonical = list(csv.DictReader(stream))
    assert canonical[0]["review_version"] == "9"
    assert canonical[0]["sha256"] == new_sha
    assert SyncIndex(sync_root).max_generation(generation_9.candidate_id) == 9


def test_engine_processes_mixed_batch_and_keeps_failure_pending(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_root.mkdir()
    add = row(1)
    move = row(2, "MOVE")
    remove = row(3, "REMOVE")
    failed = row(4)
    skipped = row(5, "MOVE")
    index = SyncIndex(sync_root)
    seed_prior(sync_root, index, move)
    seed_prior(sync_root, index, remove)
    seed_exact(sync_root, index, skipped)
    calls: list[UUID] = []
    monkeypatch.setattr(
        "sukaseafood_sync.engine.download_image",
        fake_download(sync_root, calls, fail={failed.candidate_id}),
    )

    result = SyncEngine(session=Session()).run(
        manifest(add, move, remove, failed, skipped),
        sync_root,
        SyncCallbacks(),
        threading.Event(),
    )

    assert result.counts == {"succeeded": 3, "failed": 1, "skipped": 1}
    assert result.processed == 5
    assert result.total == 5
    assert not result.cancelled
    assert calls == [add.candidate_id, failed.candidate_id]
    assert [item.candidate_id for item in result.receipt_items] == [
        str(item.candidate_id) for item in (add, move, remove, failed, skipped)
    ]
    assert [item.status for item in result.receipt_items] == [
        "SUCCEEDED", "SUCCEEDED", "SUCCEEDED", "FAILED", "SUCCEEDED"
    ]
    assert result.receipt_items[3].error == "NETWORK_ERROR"
    assert result.receipt_items[3].sha256 is None
    assert result.receipt_items[3].relative_path is None
    assert result.receipt_items[4].sha256 == SHA256
    assert result.receipt_items[4].relative_path == skipped.target_relative_path.as_posix()
    assert result.operation_log_path.parent == sync_root.resolve() / "logs"
    assert len(list((sync_root / "logs").glob("sync-*.jsonl"))) == 1
    log_entries = [
        json.loads(line)
        for line in result.operation_log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert {entry["candidate_id"] for entry in log_entries} == {
        str(item.candidate_id) for item in (add, move, remove, failed, skipped)
    }


def test_candidate_generation_is_monotonic_across_add_remove_add_and_old_replay(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_root.mkdir()
    generation_1 = replace(row(6), review_version=1)
    generation_2 = replace(
        row(6, "REMOVE"),
        review_version=2,
        previous_relative_path=generation_1.target_relative_path,
    )
    generation_3 = replace(row(6), review_version=3)
    old_move = replace(
        row(6, "MOVE", species_code="OLDER_SPECIES"),
        review_version=2,
        previous_relative_path=generation_1.target_relative_path,
    )
    calls: list[UUID] = []
    monkeypatch.setattr(
        "sukaseafood_sync.engine.download_image", fake_download(sync_root, calls)
    )
    engine = SyncEngine(session=Session())

    first = engine.run(
        manifest(generation_1), sync_root, SyncCallbacks(), threading.Event()
    )
    second = engine.run(
        manifest(generation_2), sync_root, SyncCallbacks(), threading.Event()
    )
    third = engine.run(
        manifest(generation_3), sync_root, SyncCallbacks(), threading.Event()
    )
    canonical_before_replay = (sync_root / "canonical_manifest.csv").read_bytes()
    replay_add = engine.run(
        manifest(generation_1), sync_root, SyncCallbacks(), threading.Event()
    )
    replay_remove = engine.run(
        manifest(generation_2), sync_root, SyncCallbacks(), threading.Event()
    )
    replay_move = engine.run(
        manifest(old_move), sync_root, SyncCallbacks(), threading.Event()
    )
    exact = engine.run(
        manifest(generation_3), sync_root, SyncCallbacks(), threading.Event()
    )

    assert first.counts["succeeded"] == second.counts["succeeded"] == third.counts["succeeded"] == 1
    assert replay_add.receipt_items[0].status == "FAILED"
    assert replay_add.receipt_items[0].error == "STALE_GENERATION"
    assert replay_remove.receipt_items[0].status == "FAILED"
    assert replay_remove.receipt_items[0].error == "STALE_GENERATION"
    assert replay_move.receipt_items[0].status == "FAILED"
    assert replay_move.receipt_items[0].error == "STALE_GENERATION"
    assert exact.counts == {"succeeded": 0, "failed": 0, "skipped": 1}
    assert calls == [generation_1.candidate_id, generation_3.candidate_id]
    assert (sync_root / "canonical_manifest.csv").read_bytes() == canonical_before_replay
    assert SyncIndex(sync_root).latest_for_candidate(generation_3.candidate_id).review_version == 3


@pytest.mark.parametrize("newer_action", ["ADD", "MOVE", "REMOVE"])
def test_newer_action_clears_ordinary_intent_left_before_shared_promotion(
    sync_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    newer_action: str,
) -> None:
    sync_root.mkdir()
    initial = replace(
        row(79, species_code="OLD"),
        review_id=candidate(7905),
        review_version=5,
    )
    initial_outcome = SyncEngine(
        session=Session(), downloader=fake_download(sync_root, [])
    ).run(
        ExportManifest((initial,), initial.batch_id, RECEIPT_TOKEN),
        sync_root,
        SyncCallbacks(),
        threading.Event(),
    )
    assert initial_outcome.counts == {"succeeded": 1, "failed": 0, "skipped": 0}

    generation_9_jpg, generation_9_phash, generation_9_sha = jpeg_variant(
        (19, 13), (130, 40, 190)
    )
    generation_9 = replace(
        row(79),
        batch_id=candidate(7909),
        review_id=candidate(8909),
        review_version=9,
        previous_relative_path=initial.target_relative_path,
    )

    def download_generation_9(
        session, manifest_row, destination, policy, progress, cancel
    ):
        del session, manifest_row, policy, progress, cancel
        staging = Path(destination).with_name(Path(destination).name + ".part")
        staging.write_bytes(generation_9_jpg)
        return DownloadResult(
            staging,
            generation_9_sha,
            generation_9_phash,
            len(generation_9_jpg),
            "JPEG",
            ".jpg",
            19,
            13,
        )

    class SimulatedProcessDeath(BaseException):
        pass

    def die_before_shared_promotion(*args, **kwargs):
        del args, kwargs
        raise SimulatedProcessDeath

    with monkeypatch.context() as isolated:
        isolated.setattr(
            "sukaseafood_sync.engine._promote_isolated_staging",
            die_before_shared_promotion,
        )
        with pytest.raises(SimulatedProcessDeath):
            SyncEngine(session=Session(), downloader=download_generation_9).run(
                ExportManifest(
                    (generation_9,), generation_9.batch_id, RECEIPT_TOKEN
                ),
                sync_root,
                SyncCallbacks(),
                threading.Event(),
            )

    index = SyncIndex(sync_root)
    intent = index.get_add_intent(
        generation_9.candidate_id,
        generation_9.review_id,
        generation_9.review_version,
        "ADD",
    )
    generation_9_target = local(sync_root, generation_9.target_relative_path)
    shared_stage = generation_9_target.with_name(generation_9_target.name + ".part")
    private_stages = list(
        generation_9_target.parent.glob(
            f".{generation_9_target.name}.*.sync-download.part"
        )
    )
    assert intent is not None and intent.prior_relative_path is None
    assert not generation_9_target.exists()
    assert not shared_stage.exists()
    assert len(private_stages) == 1
    assert private_stages[0].read_bytes() == generation_9_jpg
    assert local(sync_root, initial.target_relative_path).read_bytes() == JPEG

    batch_10 = candidate(7910)
    generation_10 = replace(
        row(79, newer_action, species_code="NEW"),
        batch_id=batch_10,
        review_id=candidate(8910),
        review_version=10,
        previous_relative_path=initial.target_relative_path,
    )
    if newer_action == "REMOVE":
        generation_10 = replace(
            generation_10,
            target_relative_path=PurePosixPath(
                f"_removed/{batch_10}/{generation_10.candidate_id}.jpg"
            ),
        )
    download_calls: list[UUID] = []
    outcome = SyncEngine(
        session=Session(), downloader=fake_download(sync_root, download_calls)
    ).run(
        ExportManifest((generation_10,), batch_10, RECEIPT_TOKEN),
        sync_root,
        SyncCallbacks(),
        threading.Event(),
    )

    latest = index.latest_for_candidate(generation_10.candidate_id)
    assert outcome.counts == {"succeeded": 1, "failed": 0, "skipped": 0}
    assert outcome.receipt_items[0].status == "SUCCEEDED"
    assert outcome.receipt_items[0].error is None
    assert latest is not None
    assert latest.review_version == 10
    assert latest.action == newer_action
    assert latest.relative_path == generation_10.target_relative_path
    assert index.get_add_intent(
        generation_9.candidate_id,
        generation_9.review_id,
        generation_9.review_version,
        "ADD",
    ) is None
    assert download_calls == (
        [generation_10.candidate_id] if newer_action == "ADD" else []
    )
    assert local(sync_root, generation_10.target_relative_path).read_bytes() == JPEG
    assert not local(sync_root, initial.target_relative_path).exists()
    assert not generation_9_target.exists()
    assert private_stages[0].read_bytes() == generation_9_jpg
    with (sync_root / "canonical_manifest.csv").open(
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
        assert canonical[0]["sha256"] == SHA256


def test_failed_newer_action_does_not_publish_recovered_ordinary_generation(
    sync_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sukaseafood_sync.engine as engine_module

    sync_root.mkdir()
    initial = replace(
        row(80, species_code="OLD"),
        review_id=candidate(8005),
        review_version=5,
    )
    first = SyncEngine(
        session=Session(), downloader=fake_download(sync_root, [])
    ).run(
        ExportManifest((initial,), initial.batch_id, RECEIPT_TOKEN),
        sync_root,
        SyncCallbacks(),
        threading.Event(),
    )
    assert first.counts == {"succeeded": 1, "failed": 0, "skipped": 0}
    canonical_before = (sync_root / "canonical_manifest.csv").read_bytes()

    generation_9_jpg, generation_9_phash, generation_9_sha = jpeg_variant(
        (19, 13), (130, 40, 190)
    )
    generation_9 = replace(
        row(80, species_code="MIDDLE"),
        batch_id=candidate(8009),
        review_id=candidate(9009),
        review_version=9,
        previous_relative_path=initial.target_relative_path,
    )

    def download_generation_9(
        session, manifest_row, destination, policy, progress, cancel
    ):
        del session, manifest_row, policy, progress, cancel
        staging = Path(destination).with_name(Path(destination).name + ".part")
        staging.write_bytes(generation_9_jpg)
        return DownloadResult(
            staging,
            generation_9_sha,
            generation_9_phash,
            len(generation_9_jpg),
            "JPEG",
            ".jpg",
            19,
            13,
        )

    class SimulatedProcessDeath(BaseException):
        pass

    original_promote = engine_module._promote_isolated_staging

    def die_after_shared_promotion(*args, **kwargs):
        original_promote(*args, **kwargs)
        raise SimulatedProcessDeath

    with monkeypatch.context() as isolated:
        isolated.setattr(
            engine_module, "_promote_isolated_staging", die_after_shared_promotion
        )
        with pytest.raises(SimulatedProcessDeath):
            SyncEngine(session=Session(), downloader=download_generation_9).run(
                ExportManifest(
                    (generation_9,), generation_9.batch_id, RECEIPT_TOKEN
                ),
                sync_root,
                SyncCallbacks(),
                threading.Event(),
            )

    generation_9_target = local(sync_root, generation_9.target_relative_path)
    shared_stage = generation_9_target.with_name(generation_9_target.name + ".part")
    assert not generation_9_target.exists()
    assert shared_stage.read_bytes() == generation_9_jpg
    assert local(sync_root, initial.target_relative_path).read_bytes() == JPEG

    generation_10 = replace(
        row(80, "MOVE", species_code="NEW"),
        batch_id=candidate(8010),
        review_id=candidate(9010),
        review_version=10,
        previous_relative_path=generation_9.target_relative_path,
    )
    generation_10_target = local(sync_root, generation_10.target_relative_path)
    generation_10_target.parent.mkdir(parents=True, exist_ok=True)
    unowned = b"unowned newer target conflict"
    generation_10_target.write_bytes(unowned)

    outcome = SyncEngine(session=Session()).run(
        ExportManifest((generation_10,), generation_10.batch_id, RECEIPT_TOKEN),
        sync_root,
        SyncCallbacks(),
        threading.Event(),
    )

    index = SyncIndex(sync_root)
    latest = index.latest_for_candidate(generation_9.candidate_id)
    assert outcome.counts == {"succeeded": 0, "failed": 1, "skipped": 0}
    assert outcome.receipt_items == (
        ReceiptItem(
            candidate_id=str(generation_10.candidate_id),
            review_id=str(generation_10.review_id),
            review_version=10,
            status="FAILED",
            sha256=None,
            relative_path=None,
            error="FILESYSTEM_ERROR",
        ),
    )
    assert latest is not None
    assert latest.review_version == 9
    assert latest.relative_path == generation_9.target_relative_path
    assert latest.sha256 == generation_9_sha
    assert index.get_completed(
        generation_10.candidate_id,
        generation_10.review_id,
        generation_10.review_version,
        generation_10.action,
    ) is None
    assert index.get_add_intent(
        generation_9.candidate_id,
        generation_9.review_id,
        generation_9.review_version,
        "ADD",
    ) is None
    assert not local(sync_root, initial.target_relative_path).exists()
    assert generation_9_target.read_bytes() == generation_9_jpg
    assert generation_10_target.read_bytes() == unowned
    assert not shared_stage.exists()
    assert (sync_root / "canonical_manifest.csv").read_bytes() == canonical_before


@pytest.mark.skipif(os.name != "nt", reason="Windows engine promotion leaf race")
def test_engine_promotion_source_replacement_preserves_durable_state(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_root.mkdir()
    manifest_row = replace(
        row(81, species_code="RACE"),
        review_id=candidate(8102),
        review_version=2,
    )
    target = local(sync_root, manifest_row.target_relative_path)
    shared_stage = target.with_name(target.name + ".part")
    captured_private: list[Path] = []

    def download(session, item, destination, policy, progress, cancel):
        del session, item, policy, progress, cancel
        private = Path(destination).with_name(Path(destination).name + ".part")
        private.write_bytes(JPEG)
        captured_private.append(private)
        return DownloadResult(
            private, SHA256, PHASH, len(JPEG), "JPEG", ".jpg", 11, 7
        )

    outside = sync_root.parent / "engine-promotion-outside"
    outside.mkdir()
    sentinel = outside / "sentinel.bin"
    sentinel.write_bytes(b"outside remains unchanged")
    original_pin = operations._pin_link_parents
    parked: list[Path] = []
    unowned = b"unowned private replacement"

    @contextmanager
    def replace_private_at_link(source: Path, destination: Path):
        with original_pin(source, destination):
            if (
                captured_private
                and source == captured_private[0]
                and destination == shared_stage
                and not parked
            ):
                parked_path = source.with_name(source.name + ".parked")
                source.rename(parked_path)
                source.write_bytes(unowned)
                parked.append(parked_path)
            yield

    monkeypatch.setattr(operations, "_pin_link_parents", replace_private_at_link)

    outcome = SyncEngine(session=Session(), downloader=download).run(
        ExportManifest((manifest_row,), manifest_row.batch_id, RECEIPT_TOKEN),
        sync_root,
        SyncCallbacks(),
        threading.Event(),
    )

    index = SyncIndex(sync_root)
    assert outcome.counts == {"succeeded": 0, "failed": 1, "skipped": 0}
    assert outcome.receipt_items == (
        ReceiptItem(
            candidate_id=str(manifest_row.candidate_id),
            review_id=str(manifest_row.review_id),
            review_version=manifest_row.review_version,
            status="FAILED",
            sha256=None,
            relative_path=None,
            error="FILESYSTEM_ERROR",
        ),
    )
    assert len(captured_private) == len(parked) == 1
    assert captured_private[0].read_bytes() == unowned
    assert parked[0].read_bytes() == JPEG
    assert shared_stage.exists()
    assert shared_stage.samefile(parked[0])
    assert not shared_stage.samefile(captured_private[0])
    assert not target.exists()
    assert index.get_completed(
        manifest_row.candidate_id,
        manifest_row.review_id,
        manifest_row.review_version,
        manifest_row.action,
    ) is None
    assert index.get_add_intent(
        manifest_row.candidate_id,
        manifest_row.review_id,
        manifest_row.review_version,
        manifest_row.action,
    ) is not None
    assert not (sync_root / "canonical_manifest.csv").exists()
    assert sentinel.read_bytes() == b"outside remains unchanged"
    assert list(outside.iterdir()) == [sentinel]


@pytest.mark.skipif(os.name != "nt", reason="Windows engine discard leaf race")
def test_engine_discard_refuses_cross_root_relocation_without_losing_success(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_root.mkdir()
    manifest_row = replace(
        row(82, species_code="RACE"),
        review_id=candidate(8202),
        review_version=2,
    )
    target = local(sync_root, manifest_row.target_relative_path)
    captured_private: list[Path] = []
    index = SyncIndex(sync_root)

    def download(session, item, destination, policy, progress, cancel):
        del session, policy, progress, cancel
        private = Path(destination).with_name(Path(destination).name + ".part")
        private.write_bytes(JPEG)
        captured_private.append(private)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(JPEG)
        index.record_success(
            SyncResult(
                candidate_id=item.candidate_id,
                review_id=item.review_id,
                review_version=item.review_version,
                action=item.action,
                batch_id=item.batch_id,
                relative_path=item.target_relative_path,
                sha256=SHA256,
                perceptual_hash=PHASH,
            )
        )
        return DownloadResult(
            private, SHA256, PHASH, len(JPEG), "JPEG", ".jpg", 11, 7
        )

    outside = sync_root.parent / "engine-discard-outside"
    outside.mkdir()
    relocated = outside / "relocated-owned-stage.jpg"
    original_pin = operations._pin_link_parents
    unowned = b"unowned private replacement"
    injected = False

    @contextmanager
    def relocate_private_at_disposition(source: Path, destination: Path):
        nonlocal injected
        with original_pin(source, destination):
            if (
                captured_private
                and source == captured_private[0]
                and destination == source
                and not injected
            ):
                source.rename(relocated)
                source.write_bytes(unowned)
                injected = True
            yield

    monkeypatch.setattr(
        operations, "_pin_link_parents", relocate_private_at_disposition
    )

    outcome = SyncEngine(session=Session(), downloader=download).run(
        ExportManifest((manifest_row,), manifest_row.batch_id, RECEIPT_TOKEN),
        sync_root,
        SyncCallbacks(),
        threading.Event(),
    )

    assert injected
    assert outcome.counts == {"succeeded": 0, "failed": 0, "skipped": 1}
    assert outcome.receipt_items == (
        ReceiptItem(
            candidate_id=str(manifest_row.candidate_id),
            review_id=str(manifest_row.review_id),
            review_version=manifest_row.review_version,
            status="SUCCEEDED",
            sha256=SHA256,
            relative_path=manifest_row.target_relative_path.as_posix(),
            error=None,
        ),
    )
    assert captured_private[0].read_bytes() == unowned
    assert relocated.read_bytes() == JPEG
    assert target.read_bytes() == JPEG
    completed = index.get_completed(
        manifest_row.candidate_id,
        manifest_row.review_id,
        manifest_row.review_version,
        manifest_row.action,
    )
    assert completed is not None
    assert completed.sha256 == SHA256
    assert index.get_add_intent(
        manifest_row.candidate_id,
        manifest_row.review_id,
        manifest_row.review_version,
        manifest_row.action,
    ) is None
    with (sync_root / "canonical_manifest.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as stream:
        canonical = list(csv.DictReader(stream))
    assert len(canonical) == 1
    assert canonical[0]["review_version"] == "2"
    assert canonical[0]["relative_path"] == manifest_row.target_relative_path.as_posix()
    assert canonical[0]["sha256"] == SHA256


def test_concurrent_old_and_new_replay_cannot_invert_candidate_state(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_root.mkdir()
    older = replace(row(7, species_code="OLDER_SPECIES"), review_version=1)
    newest = replace(
        row(7, species_code="NEWER_SPECIES"),
        review_version=3,
        previous_relative_path=older.target_relative_path,
    )
    both_downloaded = threading.Barrier(2)

    def concurrent_download(session, manifest_row, destination, policy, progress, cancel):
        del session, policy, cancel
        staging = Path(destination).with_name(Path(destination).name + ".part")
        staging.write_bytes(JPEG)
        progress(len(JPEG), len(JPEG))
        both_downloaded.wait(timeout=5)
        if manifest_row.review_version == older.review_version:
            # Force the newer generation to durably apply before the older
            # downloader returns and enters the root-state critical section.
            time.sleep(0.1)
        return DownloadResult(
            staging, SHA256, PHASH, len(JPEG), "JPEG", ".jpg", 11, 7
        )

    monkeypatch.setattr(
        "sukaseafood_sync.engine.download_image", concurrent_download
    )
    outcomes: list[BatchResult] = []
    failures: list[BaseException] = []

    def run(item: ManifestRow) -> None:
        try:
            outcomes.append(
                SyncEngine(session=Session()).run(
                    manifest(item),
                    sync_root,
                    SyncCallbacks(),
                    threading.Event(),
                )
            )
        except BaseException as exc:  # surfaced below with both worker states
            failures.append(exc)

    workers = [
        threading.Thread(target=run, args=(item,)) for item in (older, newest)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    assert all(not worker.is_alive() for worker in workers)
    assert failures == []
    assert len(outcomes) == 2
    latest = SyncIndex(sync_root).latest_for_candidate(newest.candidate_id)
    assert latest is not None and latest.review_version == 3
    with (sync_root / "canonical_manifest.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as stream:
        canonical = list(csv.DictReader(stream))
    assert len(canonical) == 1
    assert canonical[0]["candidate_id"] == str(newest.candidate_id)
    assert canonical[0]["review_version"] == "3"
    assert local(sync_root, newest.target_relative_path).read_bytes() == JPEG
    assert not local(sync_root, older.target_relative_path).exists()
    assert not list(sync_root.rglob("*.part"))


def test_concurrent_same_target_generations_download_to_isolated_staging_paths(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_root.mkdir()
    older = replace(row(8), review_version=1)
    newest = replace(row(8), review_version=3)
    both_downloaded = threading.Barrier(2)
    destinations: list[Path] = []

    def concurrent_download(session, manifest_row, destination, policy, progress, cancel):
        del session, policy, cancel
        destination = Path(destination)
        destinations.append(destination)
        staging = destination.with_name(destination.name + ".part")
        staging.write_bytes(JPEG)
        progress(len(JPEG), len(JPEG))
        both_downloaded.wait(timeout=5)
        if manifest_row.review_version == older.review_version:
            time.sleep(0.1)
        return DownloadResult(
            staging, SHA256, PHASH, len(JPEG), "JPEG", ".jpg", 11, 7
        )

    monkeypatch.setattr(
        "sukaseafood_sync.engine.download_image", concurrent_download
    )
    outcomes: list[BatchResult] = []

    def run(item: ManifestRow) -> None:
        outcomes.append(
            SyncEngine(session=Session()).run(
                manifest(item), sync_root, SyncCallbacks(), threading.Event()
            )
        )

    workers = [
        threading.Thread(target=run, args=(item,)) for item in (older, newest)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    assert all(not worker.is_alive() for worker in workers)
    assert len(outcomes) == 2
    assert len(destinations) == len(set(destinations)) == 2
    latest = SyncIndex(sync_root).latest_for_candidate(newest.candidate_id)
    assert latest is not None and latest.review_version == 3
    assert local(sync_root, newest.target_relative_path).read_bytes() == JPEG
    assert not list(sync_root.rglob("*.part"))
    assert not list(sync_root.rglob("*.sync-download"))


def test_receipt_contract_and_progress_are_stable_and_secret_free(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_root.mkdir()
    item = row(9)
    events: list[ProgressEvent] = []
    monkeypatch.setattr(
        "sukaseafood_sync.engine.download_image", fake_download(sync_root, [])
    )

    result = SyncEngine(session=Session()).run(
        manifest(item), sync_root, SyncCallbacks(events.append), threading.Event()
    )

    assert [field.name for field in fields(ReceiptItem)] == [
        "candidate_id", "review_id", "review_version", "status", "sha256",
        "relative_path", "error",
    ]
    assert isinstance(result, BatchResult)
    assert [event.phase for event in events] == [
        "RECOVERING", "DOWNLOADING", "DOWNLOADING", "APPLYING", "SUCCEEDED", "COMPLETED"
    ]
    byte_event = events[2]
    assert (byte_event.downloaded_bytes, byte_event.total_bytes) == (len(JPEG), len(JPEG))
    exposed = repr(result) + repr(events) + result.operation_log_path.read_text(encoding="utf-8")
    assert RECEIPT_TOKEN not in exposed
    assert item.original_url not in exposed
    assert item.source_url not in exposed
    assert str(sync_root.resolve()) not in repr(result) + repr(events)
    assert "tkinter" not in sys.modules


@pytest.mark.parametrize(
    ("lower_code", "expected"),
    [
        ("NETWORK_ERROR", "NETWORK_ERROR"),
        ("INVALID_IMAGE", "INVALID_IMAGE"),
        ("DOWNLOAD_ERROR", "DOWNLOAD_ERROR"),
        ("FILESYSTEM_ERROR", "FILESYSTEM_ERROR"),
        (f"UNTRUSTED_{RECEIPT_TOKEN}", "DOWNLOAD_ERROR"),
    ],
)
def test_download_failures_use_stable_categories(
    sync_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    lower_code: str,
    expected: str,
) -> None:
    sync_root.mkdir()

    def fail(*args, **kwargs):
        raise DownloadError(
            f"same untrusted text {RECEIPT_TOKEN}", code=lower_code
        )

    monkeypatch.setattr("sukaseafood_sync.engine.download_image", fail)
    events: list[ProgressEvent] = []
    result = SyncEngine(session=Session()).run(
        manifest(row(11)), sync_root, SyncCallbacks(events.append), threading.Event()
    )
    assert result.receipt_items[0].error == expected
    assert events[-2].message == f"sync.failed.{expected.casefold()}"
    assert RECEIPT_TOKEN not in repr(result) + repr(events)


@pytest.mark.parametrize(
    ("lower_code", "expected"),
    [
        ("TARGET_CONFLICT", "FILESYSTEM_ERROR"),
        ("SOURCE_STATE_MISSING", "OPERATION_ERROR"),
        ("INDEX_READ_FAILED", "INDEX_ERROR"),
        ("ADD_RECOVERY_INVALID", "INVALID_IMAGE"),
        ("LOG_SETUP_FAILED", "SETUP_ERROR"),
        ("UNEXPECTED_OPERATION_FAILURE", "UNEXPECTED_ERROR"),
        (f"UNTRUSTED_{RECEIPT_TOKEN}", "OPERATION_ERROR"),
    ],
)
def test_operation_failures_use_explicit_stable_categories(
    sync_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    lower_code: str,
    expected: str,
) -> None:
    sync_root.mkdir()

    def fail_recovery(*args, **kwargs):
        raise OperationError(lower_code)

    monkeypatch.setattr("sukaseafood_sync.engine.recover_add", fail_recovery)
    events: list[ProgressEvent] = []
    result = SyncEngine(session=Session()).run(
        manifest(row(14)), sync_root, SyncCallbacks(events.append), threading.Event()
    )

    assert result.receipt_items[0].error == expected
    assert events[-2].message == f"sync.failed.{expected.casefold()}"
    assert RECEIPT_TOKEN not in repr(result) + repr(events)


def test_direct_index_failure_has_index_category(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_root.mkdir()

    def fail_recovery(*args, **kwargs):
        raise SyncIndexError(f"untrusted {RECEIPT_TOKEN}")

    monkeypatch.setattr("sukaseafood_sync.engine.recover_add", fail_recovery)
    result = SyncEngine(session=Session()).run(
        manifest(row(15)), sync_root, SyncCallbacks(), threading.Event()
    )

    assert result.receipt_items[0].error == "INDEX_ERROR"


def test_unexpected_failure_does_not_expose_exception_graph_or_stop_batch(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_root.mkdir()
    secret_url = f"https://secret.example/{RECEIPT_TOKEN}"
    first, second = row(12), row(13)

    def download(session, item, destination, policy, progress, cancel):
        if item == first:
            raise RuntimeError(f"{secret_url} {sync_root.resolve()}")
        return fake_download(sync_root, [])(session, item, destination, policy, progress, cancel)

    monkeypatch.setattr("sukaseafood_sync.engine.download_image", download)
    result = SyncEngine(session=Session()).run(
        manifest(first, second), sync_root, SyncCallbacks(), threading.Event()
    )

    assert result.counts == {"succeeded": 1, "failed": 1, "skipped": 0}
    assert result.receipt_items[0].error == "UNEXPECTED_ERROR"
    surfaces = repr(result) + result.operation_log_path.read_text(encoding="utf-8")
    assert RECEIPT_TOKEN not in surfaces
    assert secret_url not in surfaces
    assert str(sync_root.resolve()) not in repr(result)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.waits: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def wait(self, delay: float, cancel_event: threading.Event) -> bool:
        self.waits.append(delay)
        self.now += delay
        return cancel_event.is_set()


def test_source_intervals_are_per_class_and_requests_are_serial(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_root.mkdir()
    items = (
        row(21, source_url="https://INATURALIST.org/observations/21"),
        row(22, source_url="https://api.inaturalist.org/v1/observations/22"),
        row(23, source_url="https://commons.WIKIMEDIA.org/wiki/File:Fish"),
        row(24, source_url="https://en.wikipedia.org/wiki/Fish"),
        row(25, source_url="https://future-source.example/items/25"),
        row(26, source_url="https://future-source.example/items/26"),
    )
    clock = Clock()
    requests_at: list[tuple[UUID, float]] = []

    def download(session, item, destination, policy, progress, cancel):
        requests_at.append((item.candidate_id, clock.now))
        return fake_download(sync_root, [])(session, item, destination, policy, progress, cancel)

    monkeypatch.setattr("sukaseafood_sync.engine.download_image", download)
    result = SyncEngine(
        session=Session(), monotonic=clock.monotonic, wait=clock.wait
    ).run(manifest(*items), sync_root, SyncCallbacks(), threading.Event())

    assert result.counts == {"succeeded": 6, "failed": 0, "skipped": 0}
    assert [when for _candidate, when in requests_at] == [0.0, 1.0, 1.0, 7.5, 7.5, 8.5]
    assert clock.waits == [1.0, 6.5, 1.0]


def test_engine_interval_starts_after_downloader_retry_after_finishes(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_root.mkdir()
    clock = Clock()
    requested: list[float] = []

    def download(session, item, destination, policy, progress, cancel):
        requested.append(clock.now)
        if len(requested) == 1:
            clock.now += 12.0  # downloader-owned Retry-After/backoff time
        return fake_download(sync_root, [])(session, item, destination, policy, progress, cancel)

    monkeypatch.setattr("sukaseafood_sync.engine.download_image", download)
    SyncEngine(session=Session(), monotonic=clock.monotonic, wait=clock.wait).run(
        manifest(row(31), row(32)), sync_root, SyncCallbacks(), threading.Event()
    )

    assert requested == [0.0, 13.0]
    assert clock.waits == [1.0]


def test_source_catalog_host_wins_over_cross_host_media_and_aliases_share_limits(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_root.mkdir()
    items = (
        row(
            33,
            source_url="https://www.gbif.org/occurrence/33",
            original_url="https://inaturalist-open-data.s3.amazonaws.com/photos/33/original.jpg",
        ),
        row(
            34,
            source_url="https://api.gbif.org/v1/occurrence/34",
            original_url="https://images.gbif.org/34.jpg",
        ),
        row(35, source_url="https://fishair.org/media/35.jpg"),
        row(36, source_url="https://data.fish-vista.org/media/36.jpg"),
        row(
            37,
            source_url="https://catalog.example.test/37",
            original_url="https://upload.wikimedia.org/wikipedia/commons/f/f0/Fish.jpg",
        ),
        row(38, source_url="https://en.wikipedia.org/wiki/Fish"),
    )
    clock = Clock()
    requested: list[float] = []

    def download(session, item, destination, policy, progress, cancel):
        requested.append(clock.now)
        return fake_download(sync_root, [])(session, item, destination, policy, progress, cancel)

    monkeypatch.setattr("sukaseafood_sync.engine.download_image", download)
    SyncEngine(session=Session(), monotonic=clock.monotonic, wait=clock.wait).run(
        manifest(*items), sync_root, SyncCallbacks(), threading.Event()
    )

    assert requested == [0.0, 1.0, 1.0, 2.0, 2.0, 8.5]
    assert clock.waits == [1.0, 1.0, 6.5]


def test_callback_exceptions_are_isolated_and_queue_delivery_works_from_thread(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_root.mkdir()
    delivered: queue.Queue[ProgressEvent] = queue.Queue()
    monkeypatch.setattr(
        "sukaseafood_sync.engine.download_image", fake_download(sync_root, [])
    )

    def callback(event: ProgressEvent) -> None:
        delivered.put(event)
        if event.phase == "DOWNLOADING":
            raise RuntimeError(f"callback secret {RECEIPT_TOKEN}")

    outcome: list[BatchResult] = []
    worker = threading.Thread(
        target=lambda: outcome.append(
            SyncEngine(session=Session()).run(
                manifest(row(41)), sync_root, SyncCallbacks(callback), threading.Event()
            )
        )
    )
    worker.start()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert outcome[0].counts == {"succeeded": 1, "failed": 0, "skipped": 0}
    phases: list[str] = []
    while not delivered.empty():
        phases.append(delivered.get_nowait().phase)
    assert phases == ["RECOVERING", "DOWNLOADING", "DOWNLOADING", "APPLYING", "SUCCEEDED", "COMPLETED"]


def test_session_ownership_and_setup_validation(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_root.mkdir()
    monkeypatch.setattr(
        "sukaseafood_sync.engine.download_image", fake_download(sync_root, [])
    )
    caller_owned = Session()
    SyncEngine(session=caller_owned).run(
        manifest(row(51)), sync_root, SyncCallbacks(), threading.Event()
    )
    assert not caller_owned.closed

    created: list[Session] = []

    def factory() -> Session:
        created.append(Session())
        return created[-1]

    SyncEngine(session_factory=factory).run(
        manifest(row(52)), sync_root, SyncCallbacks(), threading.Event()
    )
    assert len(created) == 1
    assert created[0].closed

    mismatched = ExportManifest((replace(row(53), batch_id=candidate(999)),), BATCH_ID, RECEIPT_TOKEN)
    with pytest.raises(SyncEngineError, match="MANIFEST_BATCH_MISMATCH") as caught:
        SyncEngine(session=Session()).run(
            mismatched, sync_root, SyncCallbacks(), threading.Event()
        )
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None

    unsafe_root = sync_root / "not-a-directory"
    unsafe_root.write_text("file", encoding="utf-8")
    with pytest.raises(SyncEngineError, match="ROOT_UNSAFE") as setup_caught:
        SyncEngine(session=Session()).run(
            manifest(row(54)), unsafe_root, SyncCallbacks(), threading.Event()
        )
    assert setup_caught.value.code == "ROOT_UNSAFE"


def test_engine_creates_a_new_selected_training_root(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert not sync_root.exists()
    monkeypatch.setattr(
        "sukaseafood_sync.engine.download_image", fake_download(sync_root, [])
    )

    result = SyncEngine(session=Session()).run(
        manifest(row(61)), sync_root, SyncCallbacks(), threading.Event()
    )

    assert result.counts == {"succeeded": 1, "failed": 0, "skipped": 0}
    assert sync_root.is_dir()


def test_root_setup_exception_graph_is_secret_free(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = f"{RECEIPT_TOKEN} {sync_root.resolve()}"

    def fail_lstat(_path: Path):
        raise OSError(secret)

    monkeypatch.setattr("sukaseafood_sync.engine.os.lstat", fail_lstat)
    with pytest.raises(SyncEngineError, match="ROOT_UNSAFE") as caught:
        SyncEngine(session=Session()).run(
            manifest(row(62)), sync_root, SyncCallbacks(), threading.Event()
        )

    pending: list[BaseException] = [caught.value]
    surfaces: list[str] = []
    while pending:
        error = pending.pop()
        surfaces.extend((str(error), repr(error)))
        if error.__cause__ is not None:
            pending.append(error.__cause__)
        if error.__context__ is not None:
            pending.append(error.__context__)
    assert all(RECEIPT_TOKEN not in surface for surface in surfaces)
    assert all(str(sync_root.resolve()) not in surface for surface in surfaces)


def test_invalid_manifest_action_is_rejected_before_root_mutation(sync_root: Path) -> None:
    invalid = replace(row(63), action="BOGUS")

    with pytest.raises(SyncEngineError, match="INVALID_MANIFEST"):
        SyncEngine(session=Session()).run(
            manifest(invalid), sync_root, SyncCallbacks(), threading.Event()
        )

    assert not sync_root.exists()
