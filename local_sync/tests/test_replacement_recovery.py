from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path, PurePosixPath
from uuid import UUID

import imagehash
from PIL import Image
import pytest

from conftest import BATCH_ID, CANDIDATE_ID, REVIEW_ID
from sukaseafood_sync.downloader import DownloadResult
from sukaseafood_sync.index import SyncIndex, SyncResult
from sukaseafood_sync.manifest import ManifestRow
import sukaseafood_sync.operations as operations
from sukaseafood_sync.operations import OperationError, apply_add, recover_add


OLD_REVIEW_ID = UUID("44444444-4444-4444-8444-444444444444")


def encoded_jpeg(size: tuple[int, int]) -> tuple[bytes, str]:
    output = BytesIO()
    image = Image.new("RGB", size)
    pixels = image.load()
    for y in range(size[1]):
        for x in range(size[0]):
            pixels[x, y] = ((x * 29) % 256, (y * 37) % 256, ((x + y) * 17) % 256)
    image.save(output, format="JPEG")
    content = output.getvalue()
    with Image.open(BytesIO(content)) as decoded:
        perceptual_hash = str(imagehash.phash(decoded.convert("RGB"))).lower()
    return content, perceptual_hash


def replacement_fixture(
    root: Path,
) -> tuple[
    SyncIndex,
    ManifestRow,
    DownloadResult,
    Path,
    Path,
    bytes,
    bytes,
]:
    index = SyncIndex(root)
    managed = PurePosixPath(f"images/SF006/{CANDIDATE_ID}.jpg")
    row = ManifestRow(
        batch_id=BATCH_ID,
        action="ADD",
        candidate_id=CANDIDATE_ID,
        review_id=REVIEW_ID,
        review_version=9,
        species_code="SF006",
        target_relative_path=managed,
        previous_relative_path=managed,
        preview_url="https://images.example.test/preview.jpg",
        original_url="https://images.example.test/original.jpg",
        source_url="https://catalog.example.test/record/1",
        creator="Researcher",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Researcher / Catalog",
    )
    old_jpg, old_phash = encoded_jpeg((13, 9))
    new_jpg, new_phash = encoded_jpeg((19, 11))
    target = root.joinpath(*managed.parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(old_jpg)
    index.record_success(
        SyncResult(
            candidate_id=CANDIDATE_ID,
            review_id=OLD_REVIEW_ID,
            review_version=5,
            action="ADD",
            batch_id=BATCH_ID,
            relative_path=managed,
            sha256=hashlib.sha256(old_jpg).hexdigest(),
            perceptual_hash=old_phash,
        )
    )
    staging = target.with_name(target.name + ".part")
    staging.write_bytes(new_jpg)
    download = DownloadResult(
        staging_path=staging,
        sha256=hashlib.sha256(new_jpg).hexdigest(),
        phash=new_phash,
        byte_count=len(new_jpg),
        format="JPEG",
        suffix=".jpg",
        width=19,
        height=11,
    )
    backup = root / "_removed" / str(BATCH_ID) / target.name
    return index, row, download, target, backup, old_jpg, new_jpg


def test_replacement_intent_recovers_from_crash_before_backup_link(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index, row, download, target, backup, old_jpg, new_jpg = replacement_fixture(
        sync_root
    )
    original_link = operations._link_no_clobber
    injected = False

    def crash_before_first_link(*args: object, **kwargs: object):
        nonlocal injected
        if not injected:
            injected = True
            raise OperationError("FILESYSTEM_OPERATION_FAILED")
        return original_link(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(operations, "_link_no_clobber", crash_before_first_link)
    with pytest.raises(OperationError, match="FILESYSTEM_OPERATION_FAILED"):
        apply_add(sync_root, row, download, index)

    intent = index.get_add_intent(CANDIDATE_ID, REVIEW_ID, 9, "ADD")
    assert intent is not None
    assert intent.prior_relative_path == row.previous_relative_path
    assert intent.prior_sha256 == hashlib.sha256(old_jpg).hexdigest()
    assert intent.backup_relative_path == PurePosixPath(
        "_removed", str(BATCH_ID), target.name
    )
    assert target.read_bytes() == old_jpg
    assert not backup.exists()
    assert download.staging_path.read_bytes() == new_jpg

    monkeypatch.setattr(operations, "_link_no_clobber", original_link)
    result = recover_add(sync_root, row, index)

    assert result is not None
    assert result.status == "SUCCEEDED"
    assert result.sha256 == hashlib.sha256(new_jpg).hexdigest()
    assert target.read_bytes() == new_jpg
    assert backup.read_bytes() == old_jpg
    assert not download.staging_path.exists()
    assert index.get_add_intent(CANDIDATE_ID, REVIEW_ID, 9, "ADD") is None


def test_replacement_intent_recovers_from_crash_after_old_target_unlinked(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index, row, download, target, backup, old_jpg, new_jpg = replacement_fixture(
        sync_root
    )
    original_link = operations._link_no_clobber
    links = 0

    def crash_before_new_target(*args: object, **kwargs: object):
        nonlocal links
        links += 1
        if links == 2:
            raise OperationError("FILESYSTEM_OPERATION_FAILED")
        return original_link(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(operations, "_link_no_clobber", crash_before_new_target)
    with pytest.raises(OperationError, match="FILESYSTEM_OPERATION_FAILED"):
        apply_add(sync_root, row, download, index)

    assert not target.exists()
    assert backup.read_bytes() == old_jpg
    assert download.staging_path.read_bytes() == new_jpg
    assert index.get_add_intent(CANDIDATE_ID, REVIEW_ID, 9, "ADD") is not None

    monkeypatch.setattr(operations, "_link_no_clobber", original_link)
    result = recover_add(sync_root, row, index)

    assert result is not None
    assert result.status == "SUCCEEDED"
    assert target.read_bytes() == new_jpg
    assert backup.read_bytes() == old_jpg
    assert not download.staging_path.exists()
    latest = index.latest_for_candidate(CANDIDATE_ID)
    assert latest is not None
    assert latest.review_version == 9


def test_replacement_intent_restores_old_target_when_staging_is_unavailable(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index, row, download, target, backup, old_jpg, _new_jpg = replacement_fixture(
        sync_root
    )
    original_link = operations._link_no_clobber
    links = 0

    def crash_before_new_target(*args: object, **kwargs: object):
        nonlocal links
        links += 1
        if links == 2:
            raise OperationError("FILESYSTEM_OPERATION_FAILED")
        return original_link(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(operations, "_link_no_clobber", crash_before_new_target)
    with pytest.raises(OperationError, match="FILESYSTEM_OPERATION_FAILED"):
        apply_add(sync_root, row, download, index)
    download.staging_path.unlink()
    assert not target.exists()
    assert backup.read_bytes() == old_jpg

    monkeypatch.setattr(operations, "_link_no_clobber", original_link)
    result = recover_add(sync_root, row, index)

    assert result is None
    assert target.read_bytes() == old_jpg
    assert backup.read_bytes() == old_jpg
    assert index.get_add_intent(CANDIDATE_ID, REVIEW_ID, 9, "ADD") is None
    latest = index.latest_for_candidate(CANDIDATE_ID)
    assert latest is not None
    assert latest.review_version == 5


def test_replacement_intent_records_success_after_crash_with_new_target_present(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index, row, download, target, backup, old_jpg, new_jpg = replacement_fixture(
        sync_root
    )
    original_record = SyncIndex.record_success
    injected = False

    def crash_before_new_completion(self: SyncIndex, result: SyncResult):
        nonlocal injected
        if result.review_version == 9 and not injected:
            injected = True
            raise RuntimeError("simulated index crash")
        return original_record(self, result)

    monkeypatch.setattr(SyncIndex, "record_success", crash_before_new_completion)
    with pytest.raises(OperationError, match="INDEX_WRITE_FAILED"):
        apply_add(sync_root, row, download, index)

    assert target.read_bytes() == new_jpg
    assert backup.read_bytes() == old_jpg
    assert download.staging_path.read_bytes() == new_jpg
    assert index.get_add_intent(CANDIDATE_ID, REVIEW_ID, 9, "ADD") is not None
    latest = index.latest_for_candidate(CANDIDATE_ID)
    assert latest is not None
    assert latest.review_version == 5

    result = recover_add(sync_root, row, index)

    assert result is not None
    assert result.status == "SUCCEEDED"
    assert target.read_bytes() == new_jpg
    assert backup.read_bytes() == old_jpg
    assert not download.staging_path.exists()
    latest = index.latest_for_candidate(CANDIDATE_ID)
    assert latest is not None
    assert latest.review_version == 9
    assert index.get_add_intent(CANDIDATE_ID, REVIEW_ID, 9, "ADD") is None


def test_replacement_intent_completed_replay_returns_canonical_skip(
    sync_root: Path,
) -> None:
    index, row, download, target, backup, old_jpg, new_jpg = replacement_fixture(
        sync_root
    )
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_bytes(old_jpg)
    target.write_bytes(new_jpg)
    stored = index.record_success(
        SyncResult(
            candidate_id=CANDIDATE_ID,
            review_id=REVIEW_ID,
            review_version=9,
            action="ADD",
            batch_id=BATCH_ID,
            relative_path=row.target_relative_path,
            sha256=hashlib.sha256(new_jpg).hexdigest(),
            perceptual_hash=download.phash,
        )
    )

    result = recover_add(sync_root, row, index)

    assert result is not None
    assert result.status == "SKIPPED_ALREADY_COMPLETED"
    assert result.sha256 == stored.sha256
    assert result.completed_at == stored.completed_at
    assert target.read_bytes() == new_jpg
    assert backup.read_bytes() == old_jpg
    assert not download.staging_path.exists()
