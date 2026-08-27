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
from sukaseafood_sync.index import SyncIndex, SyncRecord, SyncResult
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


def replacement_clear_fixture(
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
    index, row, download, target, backup, old_jpg, new_jpg = replacement_fixture(
        root
    )
    prior = index.latest_for_candidate(CANDIDATE_ID)
    assert prior is not None
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.hardlink_to(target)
    download.staging_path.unlink()
    index.record_add_intent(
        SyncResult(
            candidate_id=row.candidate_id,
            review_id=row.review_id,
            review_version=row.review_version,
            action=row.action,
            batch_id=row.batch_id,
            relative_path=row.target_relative_path,
            sha256=download.sha256,
            perceptual_hash=download.phash,
        ),
        row.target_relative_path,
        prior_relative_path=prior.relative_path,
        prior_sha256=prior.sha256,
        backup_relative_path=PurePosixPath(
            "_removed", str(row.batch_id), target.name
        ),
    )
    return index, row, download, target, backup, old_jpg, new_jpg


def alternate_suffix_completed_fixture(
    root: Path,
) -> tuple[SyncIndex, ManifestRow, Path, Path, bytes, SyncRecord]:
    index = SyncIndex(root)
    declared = PurePosixPath(f"images/SF006/{CANDIDATE_ID}.image")
    actual = declared.with_suffix(".jpg")
    row = ManifestRow(
        batch_id=BATCH_ID,
        action="ADD",
        candidate_id=CANDIDATE_ID,
        review_id=REVIEW_ID,
        review_version=9,
        species_code="SF006",
        target_relative_path=declared,
        previous_relative_path=declared,
        preview_url="https://images.example.test/preview.jpg",
        original_url="https://images.example.test/original.jpg",
        source_url="https://catalog.example.test/record/1",
        creator="Researcher",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Researcher / Catalog",
    )
    content, phash = encoded_jpeg((19, 11))
    target = root.joinpath(*actual.parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    staging = root.joinpath(*declared.parts).with_name(declared.name + ".part")
    staging.hardlink_to(target)
    stored = index.record_success(
        SyncResult(
            candidate_id=row.candidate_id,
            review_id=row.review_id,
            review_version=row.review_version,
            action=row.action,
            batch_id=row.batch_id,
            relative_path=actual,
            sha256=hashlib.sha256(content).hexdigest(),
            perceptual_hash=phash,
        )
    )
    return index, row, target, staging, content, stored


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


def test_completed_alternate_suffix_replay_cleans_verified_staging_hardlink(
    sync_root: Path,
) -> None:
    index, row, target, staging, content, stored = alternate_suffix_completed_fixture(
        sync_root
    )

    result = recover_add(sync_root, row, index)

    assert result is not None
    assert result.status == "SKIPPED_ALREADY_COMPLETED"
    assert result.relative_path == stored.relative_path
    assert result.sha256 == stored.sha256
    assert target.read_bytes() == content
    assert not staging.exists()


@pytest.mark.parametrize("mismatch", ["sha256", "phash", "decoded_path", "ownership"])
def test_completed_alternate_suffix_replay_preserves_unverified_staging(
    sync_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
) -> None:
    index, row, target, staging, content, stored = alternate_suffix_completed_fixture(
        sync_root
    )
    original_read = operations._read_recovery_image
    original_unlink = operations._unlink_owned
    inspected_staging = False
    attempted_cleanup = False

    def inspect_with_mismatch(path: Path, relative: PurePosixPath | None):
        nonlocal inspected_staging
        image = original_read(path, relative)
        if path != staging:
            return image
        inspected_staging = True
        changed = list(image)
        if mismatch == "sha256":
            changed[0] = "f" * 64
        elif mismatch == "phash":
            changed[1] = "f" * 16
        elif mismatch == "decoded_path":
            changed[5] = ".png"
        return tuple(changed)

    def replace_before_unlink(path: Path, owned: object, code: str) -> None:
        nonlocal attempted_cleanup
        if mismatch == "ownership" and path == staging:
            attempted_cleanup = True
            staging.unlink()
            staging.write_bytes(content)
        original_unlink(path, owned, code)  # type: ignore[arg-type]

    monkeypatch.setattr(operations, "_read_recovery_image", inspect_with_mismatch)
    monkeypatch.setattr(operations, "_unlink_owned", replace_before_unlink)

    result = recover_add(sync_root, row, index)

    assert inspected_staging
    assert result is not None
    assert result.status == "SKIPPED_ALREADY_COMPLETED"
    assert result.relative_path == stored.relative_path
    assert target.read_bytes() == content
    assert staging.read_bytes() == content
    if mismatch == "ownership":
        assert attempted_cleanup


@pytest.mark.parametrize("cas_result", ["deleted", "already_missing"])
@pytest.mark.parametrize("changed_path", ["target", "backup", "staging"])
def test_replacement_intent_clear_reclassifies_every_path_after_cas(
    sync_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    cas_result: str,
    changed_path: str,
) -> None:
    index, row, download, target, backup, old_jpg, new_jpg = (
        replacement_clear_fixture(sync_root)
    )
    original_clear = SyncIndex.clear_add_intent_if_matches

    def clear_then_change(self: SyncIndex, intent):
        first_clear = original_clear(self, intent)
        assert first_clear
        if changed_path == "target":
            target.unlink()
            target.write_bytes(new_jpg)
        elif changed_path == "backup":
            backup.unlink()
            backup.write_bytes(new_jpg)
        else:
            download.staging_path.write_bytes(new_jpg)
        if cas_result == "deleted":
            return True
        assert not original_clear(self, intent)
        return False

    monkeypatch.setattr(SyncIndex, "clear_add_intent_if_matches", clear_then_change)

    with pytest.raises(OperationError, match="ADD_RECOVERY_STATE_CHANGED"):
        recover_add(sync_root, row, index)

    assert index.get_add_intent(CANDIDATE_ID, REVIEW_ID, 9, "ADD") is None
    assert index.get_completed(CANDIDATE_ID, REVIEW_ID, 9, "ADD") is None
    if changed_path == "target":
        assert target.read_bytes() == new_jpg
        assert backup.read_bytes() == old_jpg
        assert not download.staging_path.exists()
    elif changed_path == "backup":
        assert target.read_bytes() == old_jpg
        assert backup.read_bytes() == new_jpg
        assert not download.staging_path.exists()
    else:
        assert target.read_bytes() == old_jpg
        assert backup.read_bytes() == old_jpg
        assert download.staging_path.read_bytes() == new_jpg


def test_replacement_intent_clear_accepts_exact_completion_created_during_cas(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index, row, download, target, backup, old_jpg, new_jpg = (
        replacement_clear_fixture(sync_root)
    )
    original_clear = SyncIndex.clear_add_intent_if_matches
    completed: SyncRecord | None = None

    def complete_then_compare(self: SyncIndex, intent):
        nonlocal completed
        target.unlink()
        target.write_bytes(new_jpg)
        completed = self.record_success(
            SyncResult(
                candidate_id=row.candidate_id,
                review_id=row.review_id,
                review_version=row.review_version,
                action=row.action,
                batch_id=row.batch_id,
                relative_path=row.target_relative_path,
                sha256=download.sha256,
                perceptual_hash=download.phash,
            )
        )
        assert not original_clear(self, intent)
        return False

    monkeypatch.setattr(SyncIndex, "clear_add_intent_if_matches", complete_then_compare)

    result = recover_add(sync_root, row, index)

    assert completed is not None
    assert result is not None
    assert result.status == "SKIPPED_ALREADY_COMPLETED"
    assert result.completed_at == completed.completed_at
    assert result.sha256 == completed.sha256
    assert target.read_bytes() == new_jpg
    assert backup.read_bytes() == old_jpg
    assert index.get_add_intent(CANDIDATE_ID, REVIEW_ID, 9, "ADD") is None
