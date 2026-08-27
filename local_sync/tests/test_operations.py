from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import stat
import traceback
from uuid import UUID

import pytest
import imagehash
from PIL import Image

from conftest import BATCH_ID, CANDIDATE_ID, RECEIPT_TOKEN, REVIEW_ID
import sukaseafood_sync.operations as operations
from sukaseafood_sync.downloader import DownloadResult
from sukaseafood_sync.index import SyncIndex, SyncResult
from sukaseafood_sync.manifest import ManifestRow
from sukaseafood_sync.operations import (
    OperationError,
    OperationLogger,
    apply_add,
    apply_move,
    apply_remove,
    recover_add,
)


OLD_REVIEW_ID = UUID("44444444-4444-4444-8444-444444444444")
OTHER_CANDIDATE_ID = UUID("55555555-5555-4555-8555-555555555555")
JPEG_BYTES = b"verified-jpeg-payload"
JPEG_SHA = hashlib.sha256(JPEG_BYTES).hexdigest()
PHASH = "0123456789abcdef"


def encoded_image(
    format_name: str = "JPEG", size: tuple[int, int] = (13, 9)
) -> tuple[bytes, str]:
    output = BytesIO()
    image = Image.new("RGB", size)
    pixels = image.load()
    for y in range(size[1]):
        for x in range(size[0]):
            pixels[x, y] = ((x * 19) % 256, (y * 31) % 256, ((x + y) * 23) % 256)
    image.save(output, format=format_name)
    content = output.getvalue()
    with Image.open(BytesIO(content)) as decoded:
        phash = str(imagehash.phash(decoded.convert("RGB"))).lower()
    return content, phash


def row(action: str = "ADD", **overrides: object) -> ManifestRow:
    if action == "REMOVE":
        target = PurePosixPath(f"_removed/{BATCH_ID}/{CANDIDATE_ID}.jpg")
        previous: PurePosixPath | None = PurePosixPath(
            f"images/SF006/{CANDIDATE_ID}.jpg"
        )
    elif action == "MOVE":
        target = PurePosixPath(f"images/SHELLFISH_A/{CANDIDATE_ID}.jpg")
        previous = PurePosixPath(f"images/SF006/{CANDIDATE_ID}.jpg")
    else:
        target = PurePosixPath(f"images/SF006/{CANDIDATE_ID}.jpg")
        previous = None
    values: dict[str, object] = {
        "batch_id": BATCH_ID,
        "action": action,
        "candidate_id": CANDIDATE_ID,
        "review_id": REVIEW_ID,
        "review_version": 2,
        "species_code": "SF006" if action != "MOVE" else "SHELLFISH_A",
        "target_relative_path": target,
        "previous_relative_path": previous,
        "preview_url": "https://images.example.test/preview.jpg",
        "original_url": "https://images.example.test/original.jpg",
        "source_url": "https://catalog.example.test/record/1",
        "creator": "Researcher",
        "license": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "attribution": "Researcher / Catalog",
    }
    values.update(overrides)
    return ManifestRow(**values)  # type: ignore[arg-type]


def local_path(root: Path, relative: PurePosixPath) -> Path:
    return root.joinpath(*relative.parts)


def write_file(root: Path, relative: PurePosixPath, content: bytes = JPEG_BYTES) -> Path:
    path = local_path(root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def download(
    root: Path,
    manifest_row: ManifestRow,
    *,
    content: bytes = JPEG_BYTES,
    format: str = "JPEG",
    suffix: str = ".jpg",
    phash: str = PHASH,
    width: int = 11,
    height: int = 7,
    staging_path: Path | None = None,
    sha256: str | None = None,
    byte_count: int | None = None,
) -> DownloadResult:
    staging = staging_path or local_path(root, manifest_row.target_relative_path).with_name(
        manifest_row.target_relative_path.name + ".part"
    )
    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.write_bytes(content)
    return DownloadResult(
        staging_path=staging,
        sha256=sha256 or hashlib.sha256(content).hexdigest(),
        phash=phash,
        byte_count=len(content) if byte_count is None else byte_count,
        format=format,
        suffix=suffix,
        width=width,
        height=height,
    )


def prior_result(manifest_row: ManifestRow, *, sha256: str = JPEG_SHA) -> SyncResult:
    assert manifest_row.previous_relative_path is not None
    return SyncResult(
        candidate_id=manifest_row.candidate_id,
        review_id=OLD_REVIEW_ID,
        review_version=1,
        action="ADD",
        batch_id=manifest_row.batch_id,
        relative_path=manifest_row.previous_relative_path,
        sha256=sha256,
        perceptual_hash=PHASH,
    )


def seed_prior(root: Path, index: SyncIndex, manifest_row: ManifestRow) -> Path:
    assert manifest_row.previous_relative_path is not None
    source = write_file(root, manifest_row.previous_relative_path)
    index.record_success(prior_result(manifest_row))
    return source


def completed_result(manifest_row: ManifestRow, relative: PurePosixPath) -> SyncResult:
    return SyncResult(
        candidate_id=manifest_row.candidate_id,
        review_id=manifest_row.review_id,
        review_version=manifest_row.review_version,
        action=manifest_row.action,
        batch_id=manifest_row.batch_id,
        relative_path=relative,
        sha256=JPEG_SHA,
        perceptual_hash=PHASH,
    )


def only_log(root: Path) -> Path:
    logs = list((root / "logs").glob("sync-*.jsonl"))
    assert len(logs) == 1
    return logs[0]


def assert_secret_free(error: BaseException, root: Path) -> None:
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
    surfaces.append("".join(traceback.format_exception(type(error), error, error.__traceback__)))
    assert all(RECEIPT_TOKEN not in surface for surface in surfaces)
    assert all(str(root.resolve()) not in surface for surface in surfaces)


def test_add_exact_completed_key_skips_without_touching_download_value(
    sync_root: Path,
) -> None:
    index = SyncIndex(sync_root)
    manifest_row = row()
    target = write_file(sync_root, manifest_row.target_relative_path)
    stored = index.record_success(completed_result(manifest_row, manifest_row.target_relative_path))

    result = apply_add(sync_root, manifest_row, object(), index)  # type: ignore[arg-type]

    assert result.status == "SKIPPED_ALREADY_COMPLETED"
    assert result.relative_path == stored.relative_path
    assert result.sha256 == stored.sha256
    assert target.read_bytes() == JPEG_BYTES


def test_completed_key_with_missing_or_mutated_file_fails_closed(sync_root: Path) -> None:
    index = SyncIndex(sync_root)
    manifest_row = row()
    index.record_success(completed_result(manifest_row, manifest_row.target_relative_path))

    with pytest.raises(OperationError, match="COMPLETED_STATE_STALE"):
        apply_add(sync_root, manifest_row, object(), index)  # type: ignore[arg-type]

    target = write_file(sync_root, manifest_row.target_relative_path, b"mutated")
    with pytest.raises(OperationError, match="COMPLETED_STATE_STALE"):
        apply_add(sync_root, manifest_row, object(), index)  # type: ignore[arg-type]
    assert target.read_bytes() == b"mutated"


def test_add_uses_decoder_suffix_and_server_parent_and_stem(sync_root: Path) -> None:
    index = SyncIndex(sync_root)
    manifest_row = row(target_relative_path=PurePosixPath(f"images/SF006/{CANDIDATE_ID}.image"))
    verified = download(sync_root, manifest_row, format="PNG", suffix=".png")

    result = apply_add(sync_root, manifest_row, verified, index)

    expected = PurePosixPath(f"images/SF006/{CANDIDATE_ID}.png")
    assert result.relative_path == expected
    assert local_path(sync_root, expected).read_bytes() == JPEG_BYTES
    assert not verified.staging_path.exists()
    assert index.get_completed(CANDIDATE_ID, REVIEW_ID, 2, "ADD").relative_path == expected  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"sha256": "A" * 64}, "DOWNLOAD_RESULT_INVALID"),
        ({"phash": "A" * 16}, "DOWNLOAD_RESULT_INVALID"),
        ({"byte_count": len(JPEG_BYTES) + 1}, "STAGING_CONTENT_MISMATCH"),
        ({"sha256": "0" * 64}, "STAGING_CONTENT_MISMATCH"),
        ({"format": "GIF", "suffix": ".gif"}, "DOWNLOAD_RESULT_INVALID"),
        ({"format": "JPEG", "suffix": ".jpeg"}, "DOWNLOAD_RESULT_INVALID"),
        ({"width": 0}, "DOWNLOAD_RESULT_INVALID"),
    ],
)
def test_add_rejects_invalid_result_or_staging_metadata(
    sync_root: Path, change: dict[str, object], code: str
) -> None:
    index = SyncIndex(sync_root)
    manifest_row = row()
    verified = download(sync_root, manifest_row)
    altered = replace(verified, **change)

    with pytest.raises(OperationError, match=code):
        apply_add(sync_root, manifest_row, altered, index)

    assert verified.staging_path.read_bytes() == JPEG_BYTES
    assert not local_path(sync_root, manifest_row.target_relative_path).exists()


def test_add_rejects_staging_path_not_owned_by_server_target(sync_root: Path) -> None:
    index = SyncIndex(sync_root)
    manifest_row = row()
    wrong = sync_root / "other.part"
    verified = download(sync_root, manifest_row, staging_path=wrong)

    with pytest.raises(OperationError, match="STAGING_PATH_INVALID") as caught:
        apply_add(sync_root, manifest_row, verified, index)

    assert wrong.read_bytes() == JPEG_BYTES
    assert_secret_free(caught.value, sync_root)


def test_add_existing_different_target_is_not_clobbered(sync_root: Path) -> None:
    index = SyncIndex(sync_root)
    manifest_row = row()
    verified = download(sync_root, manifest_row)
    target = write_file(sync_root, manifest_row.target_relative_path, b"do-not-clobber")

    with pytest.raises(OperationError, match="TARGET_CONFLICT"):
        apply_add(sync_root, manifest_row, verified, index)

    assert target.read_bytes() == b"do-not-clobber"
    assert verified.staging_path.read_bytes() == JPEG_BYTES


def test_add_equal_target_converges_and_removes_owned_staging(sync_root: Path) -> None:
    index = SyncIndex(sync_root)
    manifest_row = row()
    verified = download(sync_root, manifest_row)
    target = write_file(sync_root, manifest_row.target_relative_path)

    result = apply_add(sync_root, manifest_row, verified, index)

    assert result.status == "SUCCEEDED"
    assert result.relative_path == manifest_row.target_relative_path
    assert target.read_bytes() == JPEG_BYTES
    assert not verified.staging_path.exists()


def test_add_sha_dedupe_hardlinks_at_exact_server_target(sync_root: Path) -> None:
    index = SyncIndex(sync_root)
    other_relative = PurePosixPath(f"images/SF999/{OTHER_CANDIDATE_ID}.jpg")
    other_file = write_file(sync_root, other_relative)
    index.record_success(
        SyncResult(
            candidate_id=OTHER_CANDIDATE_ID,
            review_id=OLD_REVIEW_ID,
            review_version=1,
            action="ADD",
            batch_id=BATCH_ID,
            relative_path=other_relative,
            sha256=JPEG_SHA,
            perceptual_hash=PHASH,
        )
    )
    manifest_row = row()
    verified = download(sync_root, manifest_row)

    result = apply_add(sync_root, manifest_row, verified, index)
    target = local_path(sync_root, manifest_row.target_relative_path)

    assert result.relative_path == manifest_row.target_relative_path
    assert os.path.samefile(target, other_file)
    assert not verified.staging_path.exists()


def test_add_stale_sha_index_falls_back_to_verified_staging(sync_root: Path) -> None:
    index = SyncIndex(sync_root)
    stale_relative = PurePosixPath(f"images/SF999/{OTHER_CANDIDATE_ID}.jpg")
    index.record_success(
        SyncResult(
            candidate_id=OTHER_CANDIDATE_ID,
            review_id=OLD_REVIEW_ID,
            review_version=1,
            action="ADD",
            batch_id=BATCH_ID,
            relative_path=stale_relative,
            sha256=JPEG_SHA,
            perceptual_hash=PHASH,
        )
    )
    manifest_row = row()
    verified = download(sync_root, manifest_row)

    result = apply_add(sync_root, manifest_row, verified, index)

    assert result.relative_path == manifest_row.target_relative_path
    assert local_path(sync_root, manifest_row.target_relative_path).read_bytes() == JPEG_BYTES
    assert not verified.staging_path.exists()


def test_add_unsafe_stale_sha_index_falls_back_to_verified_staging(
    sync_root: Path,
) -> None:
    index = SyncIndex(sync_root)
    stale_relative = PurePosixPath(f"images/SF999/{OTHER_CANDIDATE_ID}.jpg")
    index.record_success(
        SyncResult(
            candidate_id=OTHER_CANDIDATE_ID,
            review_id=OLD_REVIEW_ID,
            review_version=1,
            action="ADD",
            batch_id=BATCH_ID,
            relative_path=stale_relative,
            sha256=JPEG_SHA,
            perceptual_hash=PHASH,
        )
    )
    outside = sync_root / "unindexed-dedupe-source.jpg"
    outside.write_bytes(JPEG_BYTES)
    stale = local_path(sync_root, stale_relative)
    stale.parent.mkdir(parents=True)
    try:
        stale.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    manifest_row = row()
    verified = download(sync_root, manifest_row)

    result = apply_add(sync_root, manifest_row, verified, index)

    assert result.relative_path == manifest_row.target_relative_path
    assert local_path(sync_root, manifest_row.target_relative_path).read_bytes() == JPEG_BYTES
    assert not verified.staging_path.exists()
    assert outside.read_bytes() == JPEG_BYTES


def test_composite_add_removes_previous_only_after_target_converges(sync_root: Path) -> None:
    index = SyncIndex(sync_root)
    previous = PurePosixPath(f"images/OLD/{CANDIDATE_ID}.jpg")
    manifest_row = row(previous_relative_path=previous)
    old = seed_prior(sync_root, index, manifest_row)
    verified = download(sync_root, manifest_row, content=b"new-image")

    result = apply_add(sync_root, manifest_row, verified, index)

    assert result.relative_path == manifest_row.target_relative_path
    assert local_path(sync_root, manifest_row.target_relative_path).read_bytes() == b"new-image"
    assert not old.exists()


def test_composite_add_precommit_failure_preserves_previous(sync_root: Path) -> None:
    index = SyncIndex(sync_root)
    previous = PurePosixPath(f"images/OLD/{CANDIDATE_ID}.jpg")
    manifest_row = row(previous_relative_path=previous)
    old = seed_prior(sync_root, index, manifest_row)
    verified = download(sync_root, manifest_row)
    target = write_file(sync_root, manifest_row.target_relative_path, b"conflict")

    with pytest.raises(OperationError, match="TARGET_CONFLICT"):
        apply_add(sync_root, manifest_row, verified, index)

    assert old.read_bytes() == JPEG_BYTES
    assert target.read_bytes() == b"conflict"


def test_same_path_replacement_installs_new_managed_jpg_generation(
    sync_root: Path,
) -> None:
    index = SyncIndex(sync_root)
    managed = PurePosixPath(f"images/SF006/{CANDIDATE_ID}.jpg")
    replacement_row = row(
        review_version=9,
        target_relative_path=managed,
        previous_relative_path=managed,
    )
    old_jpg, old_phash = encoded_image("JPEG", (13, 9))
    new_jpg, new_phash = encoded_image("JPEG", (19, 11))
    old_sha = hashlib.sha256(old_jpg).hexdigest()
    new_sha = hashlib.sha256(new_jpg).hexdigest()
    target = write_file(sync_root, managed, old_jpg)
    index.record_success(
        SyncResult(
            candidate_id=CANDIDATE_ID,
            review_id=OLD_REVIEW_ID,
            review_version=5,
            action="ADD",
            batch_id=BATCH_ID,
            relative_path=managed,
            sha256=old_sha,
            perceptual_hash=old_phash,
        )
    )
    verified = download(
        sync_root,
        replacement_row,
        content=new_jpg,
        phash=new_phash,
        width=19,
        height=11,
    )

    prior = index.latest_for_candidate(CANDIDATE_ID)
    assert prior is not None
    assert prior.sha256 == old_sha
    result = apply_add(sync_root, replacement_row, verified, index)

    assert result.sha256 == new_sha
    assert target.read_bytes() == new_jpg
    backup = sync_root / "_removed" / str(BATCH_ID) / target.name
    assert backup.read_bytes() == old_jpg
    latest = index.latest_for_candidate(CANDIDATE_ID)
    assert latest is not None
    assert latest.review_version == 9
    assert latest.sha256 == new_sha
    assert index.get_add_intent(CANDIDATE_ID, REVIEW_ID, 9, "ADD") is None
    assert not verified.staging_path.exists()


def test_reissued_replacement_cleans_only_old_batch_owned_intent(
    sync_root: Path,
) -> None:
    index = SyncIndex(sync_root)
    managed = PurePosixPath(f"images/SF006/{CANDIDATE_ID}.jpg")
    old_batch = BATCH_ID
    new_batch = UUID("77777777-7777-4777-8777-777777777777")
    replacement_row = row(
        review_version=9,
        target_relative_path=managed,
        previous_relative_path=managed,
    )
    old_jpg, old_phash = encoded_image("JPEG", (13, 9))
    new_jpg, new_phash = encoded_image("JPEG", (19, 11))
    old_sha = hashlib.sha256(old_jpg).hexdigest()
    new_sha = hashlib.sha256(new_jpg).hexdigest()
    target = write_file(sync_root, managed, new_jpg)
    prior = SyncResult(
        candidate_id=CANDIDATE_ID,
        review_id=OLD_REVIEW_ID,
        review_version=5,
        action="ADD",
        batch_id=old_batch,
        relative_path=managed,
        sha256=old_sha,
        perceptual_hash=old_phash,
    )
    completed = SyncResult(
        candidate_id=CANDIDATE_ID,
        review_id=REVIEW_ID,
        review_version=9,
        action="ADD",
        batch_id=old_batch,
        relative_path=managed,
        sha256=new_sha,
        perceptual_hash=new_phash,
    )
    index.record_success(prior)
    index.record_success(completed)
    backup_relative = PurePosixPath("_removed", str(old_batch), managed.name)
    backup = write_file(sync_root, backup_relative, old_jpg)
    index.record_add_intent(
        completed,
        managed,
        prior_relative_path=managed,
        prior_sha256=old_sha,
        backup_relative_path=backup_relative,
    )
    reissued = replace(replacement_row, batch_id=new_batch)

    result = recover_add(sync_root, reissued, index)

    assert result is not None and result.status == "SKIPPED_ALREADY_COMPLETED"
    assert target.read_bytes() == new_jpg
    assert backup.read_bytes() == old_jpg
    assert not (sync_root / "_removed" / str(new_batch)).exists()
    assert index.get_add_intent(CANDIDATE_ID, REVIEW_ID, 9, "ADD") is None


def test_isolated_stage_promotion_never_clobbers_unowned_shared_stage(
    sync_root: Path,
) -> None:
    manifest_row = row()
    content, phash = encoded_image("JPEG", (17, 11))
    destination = local_path(sync_root, manifest_row.target_relative_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    download_destination = destination.with_name(
        f".{destination.name}.{'a' * 32}.sync-download"
    )
    private_stage = download_destination.with_name(download_destination.name + ".part")
    verified = download(
        sync_root,
        manifest_row,
        content=content,
        phash=phash,
        width=17,
        height=11,
        staging_path=private_stage,
    )
    shared_stage = destination.with_name(destination.name + ".part")
    unowned = b"do-not-clobber"
    shared_stage.write_bytes(unowned)

    with pytest.raises(OperationError, match="TARGET_UNSAFE|TARGET_CONFLICT"):
        operations.promote_isolated_staging(
            sync_root,
            manifest_row,
            destination,
            download_destination,
            verified,
        )

    assert shared_stage.read_bytes() == unowned
    assert private_stage.read_bytes() == content


def test_missing_isolated_stage_outside_root_cannot_bypass_containment(
    sync_root: Path,
) -> None:
    sync_root.mkdir()
    manifest_row = row()
    outside_destination = sync_root.parent / (
        f".{manifest_row.target_relative_path.name}.{'d' * 32}.sync-download"
    )
    outside_stage = outside_destination.with_name(outside_destination.name + ".part")
    downloaded = DownloadResult(
        staging_path=outside_stage,
        sha256="a" * 64,
        phash=PHASH,
        byte_count=1,
        format="JPEG",
        suffix=".jpg",
        width=1,
        height=1,
    )

    with pytest.raises(OperationError, match="STAGING_PATH_INVALID"):
        operations.discard_isolated_staging(
            sync_root,
            manifest_row,
            downloaded,
            outside_destination,
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows junction/rename protection")
def test_isolated_stage_promotion_pins_parent_against_reparse_swap(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_row = row()
    content, phash = encoded_image("JPEG", (17, 11))
    destination = local_path(sync_root, manifest_row.target_relative_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    download_destination = destination.with_name(
        f".{destination.name}.{'b' * 32}.sync-download"
    )
    private_stage = download_destination.with_name(download_destination.name + ".part")
    verified = download(
        sync_root,
        manifest_row,
        content=content,
        phash=phash,
        width=17,
        height=11,
        staging_path=private_stage,
    )
    outside = sync_root.parent / "outside-promotion"
    outside.mkdir()
    replacement_link = sync_root / "prepared-promotion-link"
    try:
        os.symlink(outside, replacement_link, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlink unavailable: {error}")

    shared_stage = destination.with_name(destination.name + ".part")
    original_pin = operations._pin_link_parents
    attempted = False
    blocked = False

    @contextmanager
    def swap_parent_at_link(source: Path, target: Path):
        nonlocal attempted, blocked
        with original_pin(source, target):
            if source == private_stage and target == shared_stage and not attempted:
                attempted = True
                parked = destination.parent.with_name(destination.parent.name + ".parked")
                try:
                    destination.parent.rename(parked)
                    replacement_link.rename(destination.parent)
                except OSError:
                    blocked = True
            yield

    monkeypatch.setattr(operations, "_pin_link_parents", swap_parent_at_link)
    promoted = operations.promote_isolated_staging(
        sync_root,
        manifest_row,
        destination,
        download_destination,
        verified,
    )

    assert attempted and blocked
    assert promoted.staging_path.read_bytes() == content
    assert not private_stage.exists()
    assert list(outside.iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows junction/rename protection")
def test_isolated_stage_discard_pins_parent_against_reparse_swap(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_row = row()
    content, phash = encoded_image("JPEG", (17, 11))
    destination = local_path(sync_root, manifest_row.target_relative_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    download_destination = destination.with_name(
        f".{destination.name}.{'c' * 32}.sync-download"
    )
    private_stage = download_destination.with_name(download_destination.name + ".part")
    verified = download(
        sync_root,
        manifest_row,
        content=content,
        phash=phash,
        width=17,
        height=11,
        staging_path=private_stage,
    )
    outside = sync_root.parent / "outside-discard"
    outside.mkdir()
    replacement_link = sync_root / "prepared-discard-link"
    try:
        os.symlink(outside, replacement_link, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlink unavailable: {error}")

    original_pin = operations._pin_link_parents
    attempted = False
    blocked = False

    @contextmanager
    def swap_parent_at_unlink(source: Path, target: Path):
        nonlocal attempted, blocked
        with original_pin(source, target):
            if source == private_stage and target == private_stage and not attempted:
                attempted = True
                parked = destination.parent.with_name(destination.parent.name + ".parked")
                try:
                    destination.parent.rename(parked)
                    replacement_link.rename(destination.parent)
                except OSError:
                    blocked = True
            yield

    monkeypatch.setattr(operations, "_pin_link_parents", swap_parent_at_unlink)
    operations.discard_isolated_staging(
        sync_root,
        manifest_row,
        verified,
        download_destination,
    )

    assert attempted and blocked
    assert not private_stage.exists()
    assert list(outside.iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows exact-leaf deletion")
def test_isolated_stage_discard_deletes_verified_leaf_not_path_replacement(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_row = row()
    content, phash = encoded_image("JPEG", (17, 11))
    destination = local_path(sync_root, manifest_row.target_relative_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    download_destination = destination.with_name(
        f".{destination.name}.{'e' * 32}.sync-download"
    )
    private_stage = download_destination.with_name(download_destination.name + ".part")
    verified = download(
        sync_root,
        manifest_row,
        content=content,
        phash=phash,
        width=17,
        height=11,
        staging_path=private_stage,
    )
    replacement = b"unowned replacement must survive"
    parked = private_stage.with_name(private_stage.name + ".parked")
    original_pin = operations._pin_link_parents
    injected = False

    @contextmanager
    def replace_leaf_at_unlink(source: Path, target: Path):
        nonlocal injected
        with original_pin(source, target):
            if source == private_stage and target == private_stage and not injected:
                private_stage.rename(parked)
                private_stage.write_bytes(replacement)
                injected = True
            yield

    monkeypatch.setattr(operations, "_pin_link_parents", replace_leaf_at_unlink)

    operations.discard_isolated_staging(
        sync_root,
        manifest_row,
        verified,
        download_destination,
    )

    assert injected
    assert private_stage.read_bytes() == replacement
    assert not parked.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows exact-leaf linking")
def test_isolated_stage_promotion_never_links_replacement_source_leaf(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_row = row()
    content, phash = encoded_image("JPEG", (17, 11))
    destination = local_path(sync_root, manifest_row.target_relative_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    download_destination = destination.with_name(
        f".{destination.name}.{'f' * 32}.sync-download"
    )
    private_stage = download_destination.with_name(download_destination.name + ".part")
    verified = download(
        sync_root,
        manifest_row,
        content=content,
        phash=phash,
        width=17,
        height=11,
        staging_path=private_stage,
    )
    parked = private_stage.with_name(private_stage.name + ".parked")
    replacement = b"unowned replacement must not be linked"
    shared_stage = destination.with_name(destination.name + ".part")
    outside = sync_root.parent / "outside-leaf-race"
    outside.mkdir()
    sentinel = outside / "sentinel.bin"
    sentinel.write_bytes(b"outside must remain unchanged")
    original_pin = operations._pin_link_parents
    injected = False

    @contextmanager
    def replace_leaf_at_link(source: Path, target: Path):
        nonlocal injected
        with original_pin(source, target):
            if source == private_stage and target == shared_stage and not injected:
                private_stage.rename(parked)
                private_stage.write_bytes(replacement)
                injected = True
            yield

    monkeypatch.setattr(operations, "_pin_link_parents", replace_leaf_at_link)

    with pytest.raises(OperationError, match="STAGING_FILE_UNSAFE"):
        operations.promote_isolated_staging(
            sync_root,
            manifest_row,
            destination,
            download_destination,
            verified,
        )

    assert injected
    assert private_stage.read_bytes() == replacement
    assert parked.read_bytes() == content
    if shared_stage.exists():
        assert shared_stage.read_bytes() == content
        assert shared_stage.samefile(parked)
        assert not shared_stage.samefile(private_stage)
    assert sentinel.read_bytes() == b"outside must remain unchanged"
    assert list(outside.iterdir()) == [sentinel]


@pytest.mark.skipif(os.name != "nt", reason="Windows junction/rename protection")
def test_replacement_backup_parent_swap_cannot_escape_sync_root(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pinned managed parent must reject a junction swap before backup link."""

    index = SyncIndex(sync_root)
    managed = PurePosixPath(f"images/SF006/{CANDIDATE_ID}.jpg")
    replacement_row = row(
        review_version=9,
        target_relative_path=managed,
        previous_relative_path=managed,
    )
    old_jpg, old_phash = encoded_image("JPEG", (13, 9))
    new_jpg, new_phash = encoded_image("JPEG", (19, 11))
    target = write_file(sync_root, managed, old_jpg)
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
    verified = download(
        sync_root,
        replacement_row,
        content=new_jpg,
        phash=new_phash,
        width=19,
        height=11,
    )
    outside = sync_root.parent / "outside-backups"
    outside.mkdir()
    replacement_link = sync_root / "prepared-backup-link"
    try:
        os.symlink(outside, replacement_link, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlink unavailable: {error}")

    original_link = operations.os.link
    attempted = False
    blocked = False

    def swap_parent_then_link(source, destination, *args, **kwargs):
        nonlocal attempted, blocked
        destination_path = Path(destination)
        backup_parent = sync_root / "_removed" / str(BATCH_ID)
        if destination_path.parent == backup_parent and not attempted:
            attempted = True
            parked = backup_parent.with_name(backup_parent.name + ".parked")
            try:
                backup_parent.rename(parked)
                replacement_link.rename(backup_parent)
            except OSError:
                blocked = True
        return original_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(operations.os, "link", swap_parent_then_link)
    result = apply_add(sync_root, replacement_row, verified, index)

    assert attempted
    assert blocked
    assert result.status == "SUCCEEDED"
    assert target.read_bytes() == new_jpg
    assert list(outside.iterdir()) == []


def test_same_path_replacement_rejects_user_modified_managed_target(
    sync_root: Path,
) -> None:
    index = SyncIndex(sync_root)
    managed = PurePosixPath(f"images/SF006/{CANDIDATE_ID}.jpg")
    replacement_row = row(
        review_version=9,
        target_relative_path=managed,
        previous_relative_path=managed,
    )
    indexed_jpg, indexed_phash = encoded_image("JPEG", (13, 9))
    modified_jpg, _modified_phash = encoded_image("JPEG", (17, 13))
    new_jpg, new_phash = encoded_image("JPEG", (23, 15))
    target = write_file(sync_root, managed, modified_jpg)
    index.record_success(
        SyncResult(
            candidate_id=CANDIDATE_ID,
            review_id=OLD_REVIEW_ID,
            review_version=5,
            action="ADD",
            batch_id=BATCH_ID,
            relative_path=managed,
            sha256=hashlib.sha256(indexed_jpg).hexdigest(),
            perceptual_hash=indexed_phash,
        )
    )
    verified = download(
        sync_root,
        replacement_row,
        content=new_jpg,
        phash=new_phash,
        width=23,
        height=15,
    )

    with pytest.raises(OperationError, match="SOURCE_STATE_MISMATCH"):
        apply_add(sync_root, replacement_row, verified, index)

    assert target.read_bytes() == modified_jpg
    assert verified.staging_path.read_bytes() == new_jpg
    assert not (sync_root / "_removed" / str(BATCH_ID) / target.name).exists()
    assert index.get_add_intent(CANDIDATE_ID, REVIEW_ID, 9, "ADD") is None
    latest = index.latest_for_candidate(CANDIDATE_ID)
    assert latest is not None
    assert latest.review_version == 5


def test_same_path_replacement_rejects_in_place_change_before_owned_unlink(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Device/inode identity alone cannot authorize deleting changed source bytes."""

    index = SyncIndex(sync_root)
    managed = PurePosixPath(f"images/SF006/{CANDIDATE_ID}.jpg")
    replacement_row = row(
        review_version=9,
        target_relative_path=managed,
        previous_relative_path=managed,
    )
    old_jpg, old_phash = encoded_image("JPEG", (13, 9))
    changed_jpg, _changed_phash = encoded_image("JPEG", (17, 13))
    new_jpg, new_phash = encoded_image("JPEG", (23, 15))
    target = write_file(sync_root, managed, old_jpg)
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
    verified = download(
        sync_root,
        replacement_row,
        content=new_jpg,
        phash=new_phash,
        width=23,
        height=15,
    )
    original_unlink = operations._unlink_owned
    injected = False

    def mutate_then_unlink(path, owned, code, *args, **kwargs):
        nonlocal injected
        if Path(path) == target and not injected:
            injected = True
            target.write_bytes(changed_jpg)
        return original_unlink(path, owned, code, *args, **kwargs)

    monkeypatch.setattr(operations, "_unlink_owned", mutate_then_unlink)

    with pytest.raises(OperationError, match="SOURCE_STATE_MISMATCH"):
        apply_add(sync_root, replacement_row, verified, index)

    assert injected
    assert target.read_bytes() == changed_jpg
    assert verified.staging_path.read_bytes() == new_jpg
    latest = index.latest_for_candidate(CANDIDATE_ID)
    assert latest is not None
    assert latest.review_version == 5


@pytest.mark.skipif(os.name != "nt", reason="Windows file-share ownership pin")
def test_replacement_blocks_in_place_change_through_unlink_boundary(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The final fingerprint remains stable until the owned path is unlinked."""

    index = SyncIndex(sync_root)
    managed = PurePosixPath(f"images/SF006/{CANDIDATE_ID}.jpg")
    replacement_row = row(
        review_version=9,
        target_relative_path=managed,
        previous_relative_path=managed,
    )
    old_jpg, old_phash = encoded_image("JPEG", (13, 9))
    changed_jpg, _changed_phash = encoded_image("JPEG", (17, 13))
    new_jpg, new_phash = encoded_image("JPEG", (23, 15))
    target = write_file(sync_root, managed, old_jpg)
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
    verified = download(
        sync_root,
        replacement_row,
        content=new_jpg,
        phash=new_phash,
        width=23,
        height=15,
    )
    original_unlink = Path.unlink
    attempted = False
    blocked = False

    def mutate_at_unlink(path: Path, *args, **kwargs):
        nonlocal attempted, blocked
        if path == target and not attempted:
            attempted = True
            try:
                target.write_bytes(changed_jpg)
            except PermissionError:
                blocked = True
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", mutate_at_unlink)
    result = apply_add(sync_root, replacement_row, verified, index)

    backup = sync_root / "_removed" / str(BATCH_ID) / target.name
    assert attempted
    assert blocked
    assert result.status == "SUCCEEDED"
    assert target.read_bytes() == new_jpg
    assert backup.read_bytes() == old_jpg


def test_same_path_replacement_rejects_stale_generation_without_touching_files(
    sync_root: Path,
) -> None:
    index = SyncIndex(sync_root)
    managed = PurePosixPath(f"images/SF006/{CANDIDATE_ID}.jpg")
    stale_row = row(
        review_version=5,
        target_relative_path=managed,
        previous_relative_path=managed,
    )
    old_jpg, old_phash = encoded_image("JPEG", (13, 9))
    new_jpg, new_phash = encoded_image("JPEG", (19, 11))
    target = write_file(sync_root, managed, old_jpg)
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
    verified = download(
        sync_root,
        stale_row,
        content=new_jpg,
        phash=new_phash,
        width=19,
        height=11,
    )

    with pytest.raises(OperationError, match="STALE_GENERATION"):
        apply_add(sync_root, stale_row, verified, index)

    assert target.read_bytes() == old_jpg
    assert verified.staging_path.read_bytes() == new_jpg
    assert not (sync_root / "_removed" / str(BATCH_ID) / target.name).exists()


@pytest.mark.parametrize("action", ["MOVE", "REMOVE"])
def test_move_and_remove_link_to_exact_target_then_remove_previous(
    sync_root: Path, action: str
) -> None:
    index = SyncIndex(sync_root)
    manifest_row = row(action)
    source = seed_prior(sync_root, index, manifest_row)

    result = apply_move(sync_root, manifest_row, index) if action == "MOVE" else apply_remove(sync_root, manifest_row, index)
    target = local_path(sync_root, manifest_row.target_relative_path)

    assert result.status == "SUCCEEDED"
    assert result.relative_path == manifest_row.target_relative_path
    assert target.read_bytes() == JPEG_BYTES
    assert not source.exists()
    assert index.get_completed(CANDIDATE_ID, REVIEW_ID, 2, action).relative_path == manifest_row.target_relative_path  # type: ignore[union-attr]


@pytest.mark.parametrize("action", ["MOVE", "REMOVE"])
def test_move_and_remove_equal_target_converge_without_clobber(
    sync_root: Path, action: str
) -> None:
    index = SyncIndex(sync_root)
    manifest_row = row(action)
    source = seed_prior(sync_root, index, manifest_row)
    target = write_file(sync_root, manifest_row.target_relative_path)

    result = apply_move(sync_root, manifest_row, index) if action == "MOVE" else apply_remove(sync_root, manifest_row, index)

    assert result.relative_path == manifest_row.target_relative_path
    assert target.read_bytes() == JPEG_BYTES
    assert not source.exists()


@pytest.mark.parametrize("action", ["MOVE", "REMOVE"])
def test_move_and_remove_recover_when_source_is_gone_and_target_matches_prior(
    sync_root: Path, action: str
) -> None:
    index = SyncIndex(sync_root)
    manifest_row = row(action)
    index.record_success(prior_result(manifest_row))
    target = write_file(sync_root, manifest_row.target_relative_path)

    result = apply_move(sync_root, manifest_row, index) if action == "MOVE" else apply_remove(sync_root, manifest_row, index)

    assert result.status == "SUCCEEDED"
    assert result.relative_path == manifest_row.target_relative_path


@pytest.mark.parametrize("action", ["MOVE", "REMOVE"])
def test_move_and_remove_target_conflict_preserves_previous(
    sync_root: Path, action: str
) -> None:
    index = SyncIndex(sync_root)
    manifest_row = row(action)
    source = seed_prior(sync_root, index, manifest_row)
    target = write_file(sync_root, manifest_row.target_relative_path, b"different")

    with pytest.raises(OperationError, match="TARGET_CONFLICT"):
        if action == "MOVE":
            apply_move(sync_root, manifest_row, index)
        else:
            apply_remove(sync_root, manifest_row, index)

    assert source.read_bytes() == JPEG_BYTES
    assert target.read_bytes() == b"different"


@pytest.mark.parametrize("action", ["MOVE", "REMOVE"])
def test_move_and_remove_fail_closed_without_expected_source_or_target(
    sync_root: Path, action: str
) -> None:
    index = SyncIndex(sync_root)
    manifest_row = row(action)
    index.record_success(prior_result(manifest_row))

    with pytest.raises(OperationError, match="SOURCE_STATE_MISSING"):
        if action == "MOVE":
            apply_move(sync_root, manifest_row, index)
        else:
            apply_remove(sync_root, manifest_row, index)


def test_operation_functions_enforce_exact_action(sync_root: Path) -> None:
    index = SyncIndex(sync_root)

    for call, manifest_row in [
        (lambda: apply_add(sync_root, row("MOVE"), object(), index), row("MOVE")),
        (lambda: apply_move(sync_root, row("REMOVE"), index), row("REMOVE")),
        (lambda: apply_remove(sync_root, row("MOVE"), index), row("MOVE")),
    ]:
        with pytest.raises(OperationError, match="ACTION_MISMATCH"):
            call()


@pytest.mark.parametrize("unsafe_kind", ["parent", "staging", "target", "source", "logs"])
def test_operations_reject_symlink_or_reparse_components(
    sync_root: Path, tmp_path: Path, unsafe_kind: str
) -> None:
    index = SyncIndex(sync_root)
    outside = tmp_path / f"outside-{unsafe_kind}"
    if unsafe_kind in {"parent", "logs"}:
        outside.mkdir()
    else:
        outside.write_bytes(b"outside")
    manifest_row = row()

    try:
        if unsafe_kind == "parent":
            (sync_root / "images").symlink_to(outside, target_is_directory=True)
            verified = DownloadResult(sync_root / "unused", JPEG_SHA, PHASH, len(JPEG_BYTES), "JPEG", ".jpg", 1, 1)
            call = lambda: apply_add(sync_root, manifest_row, verified, index)
        elif unsafe_kind == "logs":
            (sync_root / "logs").symlink_to(outside, target_is_directory=True)
            verified = DownloadResult(sync_root / "unused", JPEG_SHA, PHASH, len(JPEG_BYTES), "JPEG", ".jpg", 1, 1)
            call = lambda: apply_add(sync_root, manifest_row, verified, index)
        elif unsafe_kind == "staging":
            staging = local_path(sync_root, manifest_row.target_relative_path).with_name(manifest_row.target_relative_path.name + ".part")
            staging.parent.mkdir(parents=True)
            staging.symlink_to(outside)
            verified = DownloadResult(staging, JPEG_SHA, PHASH, len(JPEG_BYTES), "JPEG", ".jpg", 1, 1)
            call = lambda: apply_add(sync_root, manifest_row, verified, index)
        elif unsafe_kind == "target":
            verified = download(sync_root, manifest_row)
            local_path(sync_root, manifest_row.target_relative_path).symlink_to(outside)
            call = lambda: apply_add(sync_root, manifest_row, verified, index)
        else:
            moving = row("MOVE")
            assert moving.previous_relative_path is not None
            source = local_path(sync_root, moving.previous_relative_path)
            source.parent.mkdir(parents=True)
            index.record_success(prior_result(moving))
            source.symlink_to(outside)
            call = lambda: apply_move(sync_root, moving, index)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(OperationError, match="UNSAFE|INDEX_READ_FAILED"):
        call()

    assert outside.read_bytes() == b"outside" if outside.is_file() else list(outside.iterdir()) == []


def test_jsonl_has_exact_secret_free_schema_and_shared_logger(sync_root: Path) -> None:
    index = SyncIndex(sync_root)
    logger = OperationLogger(sync_root, BATCH_ID)
    first_row = row()
    first = download(sync_root, first_row)
    apply_add(sync_root, first_row, first, index, logger=logger)

    second_candidate = OTHER_CANDIDATE_ID
    second_row = row(
        candidate_id=second_candidate,
        review_id=OLD_REVIEW_ID,
        target_relative_path=PurePosixPath(f"images/SF006/{second_candidate}.jpg"),
    )
    second = download(sync_root, second_row, content=b"second")
    apply_add(sync_root, second_row, second, index, logger=logger)

    lines = [json.loads(line) for line in logger.path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 2
    assert set(lines[0]) == {
        "candidate_id",
        "action",
        "status",
        "previous_relative_path",
        "relative_path",
        "sha256",
        "error",
        "timestamp",
    }
    assert lines[0]["status"] == "SUCCEEDED"
    assert lines[0]["relative_path"] == first_row.target_relative_path.as_posix()
    assert lines[0]["error"] is None
    serialized = logger.path.read_text(encoding="utf-8")
    assert RECEIPT_TOKEN not in serialized
    assert "original_url" not in serialized
    assert str(sync_root.resolve()) not in serialized


def test_failure_is_logged_with_stable_error_code(sync_root: Path) -> None:
    index = SyncIndex(sync_root)
    manifest_row = row("MOVE")

    with pytest.raises(OperationError, match="SOURCE_STATE_MISSING"):
        apply_move(sync_root, manifest_row, index)

    entry = json.loads(only_log(sync_root).read_text(encoding="utf-8"))
    assert entry["status"] == "FAILED"
    assert entry["error"] == "SOURCE_STATE_MISSING"
    assert entry["sha256"] is None


def test_log_append_failure_does_not_undo_successful_indexed_operation(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = SyncIndex(sync_root)
    logger = OperationLogger(sync_root, BATCH_ID)
    manifest_row = row()
    verified = download(sync_root, manifest_row)

    def fail_append(*args: object, **kwargs: object) -> None:
        raise OSError("log failed")

    monkeypatch.setattr(OperationLogger, "append", fail_append)
    result = apply_add(sync_root, manifest_row, verified, index, logger=logger)

    assert result.status == "SUCCEEDED"
    assert local_path(sync_root, manifest_row.target_relative_path).read_bytes() == JPEG_BYTES
    assert index.get_completed(CANDIDATE_ID, REVIEW_ID, 2, "ADD") is not None


def test_add_index_failure_keeps_staging_for_safe_rerun(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = SyncIndex(sync_root)
    manifest_row = row()
    verified = download(sync_root, manifest_row)
    original_record = SyncIndex.record_success
    failed = False

    def fail_once(self: SyncIndex, result: SyncResult):
        nonlocal failed
        if result.action == "ADD" and not failed:
            failed = True
            raise RuntimeError("simulated index failure")
        return original_record(self, result)

    monkeypatch.setattr(SyncIndex, "record_success", fail_once)
    with pytest.raises(OperationError, match="INDEX_WRITE_FAILED"):
        apply_add(sync_root, manifest_row, verified, index)

    target = local_path(sync_root, manifest_row.target_relative_path)
    assert target.read_bytes() == JPEG_BYTES
    assert verified.staging_path.read_bytes() == JPEG_BYTES

    result = apply_add(sync_root, manifest_row, verified, index)
    assert result.status == "SUCCEEDED"
    assert not verified.staging_path.exists()
    assert index.get_completed(CANDIDATE_ID, REVIEW_ID, 2, "ADD") is not None


def test_operation_rejects_selected_root_symlink(
    sync_root: Path, tmp_path: Path
) -> None:
    index = SyncIndex(sync_root)
    alias = tmp_path / "training-root-alias"
    try:
        alias.symlink_to(sync_root, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    manifest_row = row()
    verified = DownloadResult(
        alias / "unused.part",
        JPEG_SHA,
        PHASH,
        len(JPEG_BYTES),
        "JPEG",
        ".jpg",
        1,
        1,
    )

    with pytest.raises(OperationError, match="ROOT_UNSAFE"):
        apply_add(alias, manifest_row, verified, index)

    assert not local_path(sync_root, manifest_row.target_relative_path).exists()


def test_index_failure_after_move_is_recoverable_without_data_loss(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = SyncIndex(sync_root)
    manifest_row = row("MOVE")
    source = seed_prior(sync_root, index, manifest_row)
    original_record = SyncIndex.record_success
    failed = False

    def fail_once(self: SyncIndex, result: SyncResult):
        nonlocal failed
        if result.action == "MOVE" and not failed:
            failed = True
            raise RuntimeError(f"secret {RECEIPT_TOKEN} {sync_root.resolve()}")
        return original_record(self, result)

    monkeypatch.setattr(SyncIndex, "record_success", fail_once)
    with pytest.raises(OperationError, match="INDEX_WRITE_FAILED") as caught:
        apply_move(sync_root, manifest_row, index)
    assert_secret_free(caught.value, sync_root)
    assert not source.exists()
    assert local_path(sync_root, manifest_row.target_relative_path).read_bytes() == JPEG_BYTES

    result = apply_move(sync_root, manifest_row, index)
    assert result.status == "SUCCEEDED"
    assert index.get_completed(CANDIDATE_ID, REVIEW_ID, 2, "MOVE") is not None


def test_recover_add_returns_none_only_when_no_target_candidate_exists(
    sync_root: Path,
) -> None:
    index = SyncIndex(sync_root)

    result = recover_add(sync_root, row(), index)

    assert result is None
    assert index.get_completed(CANDIDATE_ID, REVIEW_ID, 2, "ADD") is None


def test_recover_add_treats_case_variant_composite_previous_as_same_windows_path(
    sync_root: Path,
) -> None:
    """Case-only suffix differences must not turn the previous file into success."""

    index = SyncIndex(sync_root)
    previous = PurePosixPath(f"images/SF006/{CANDIDATE_ID}.JPG")
    manifest_row = row(
        target_relative_path=PurePosixPath(f"images/SF006/{CANDIDATE_ID}.image"),
        previous_relative_path=previous,
    )
    content, phash = encoded_image("JPEG")
    old = write_file(sync_root, previous, content)
    index.record_success(
        SyncResult(
            candidate_id=CANDIDATE_ID,
            review_id=OLD_REVIEW_ID,
            review_version=1,
            action="ADD",
            batch_id=BATCH_ID,
            relative_path=previous,
            sha256=hashlib.sha256(content).hexdigest(),
            perceptual_hash=phash,
        )
    )

    result = recover_add(sync_root, manifest_row, index)

    assert result is None
    assert old.read_bytes() == content
    assert index.get_completed(CANDIDATE_ID, REVIEW_ID, 2, "ADD") is None


def test_recover_add_ignores_stray_alternate_suffix_without_operation_intent(
    sync_root: Path,
) -> None:
    """An arbitrary valid same-stem image is not evidence that this ADD succeeded."""

    index = SyncIndex(sync_root)
    manifest_row = row(
        target_relative_path=PurePosixPath(f"images/SF006/{CANDIDATE_ID}.image")
    )
    content, _phash = encoded_image("PNG")
    stray_relative = PurePosixPath(f"images/SF006/{CANDIDATE_ID}.png")
    stray = write_file(sync_root, stray_relative, content)

    result = recover_add(sync_root, manifest_row, index)

    assert result is None
    assert stray.read_bytes() == content
    assert index.get_completed(CANDIDATE_ID, REVIEW_ID, 2, "ADD") is None


def test_alternate_suffix_recovery_requires_durable_exact_intent_after_index_crash(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A promoted decoder suffix is recoverable only for its exact failed index write."""

    index = SyncIndex(sync_root)
    manifest_row = row(
        target_relative_path=PurePosixPath(f"images/SF006/{CANDIDATE_ID}.image")
    )
    content, phash = encoded_image("PNG", (17, 11))
    verified = download(
        sync_root,
        manifest_row,
        content=content,
        format="PNG",
        suffix=".png",
        phash=phash,
        width=17,
        height=11,
    )
    original_record = SyncIndex.record_success
    failed = False

    def fail_once(self: SyncIndex, result: SyncResult):
        nonlocal failed
        if result.action == "ADD" and not failed:
            failed = True
            raise RuntimeError("simulated index crash")
        return original_record(self, result)

    monkeypatch.setattr(SyncIndex, "record_success", fail_once)
    with pytest.raises(OperationError, match="INDEX_WRITE_FAILED"):
        apply_add(sync_root, manifest_row, verified, index)

    intent = index.get_add_intent(CANDIDATE_ID, REVIEW_ID, 2, "ADD")
    assert intent is not None
    assert intent.actual_relative_path == PurePosixPath(
        f"images/SF006/{CANDIDATE_ID}.png"
    )
    assert index.get_completed(CANDIDATE_ID, REVIEW_ID, 2, "ADD") is None

    result = recover_add(sync_root, manifest_row, index)

    assert result is not None
    assert result.status == "SUCCEEDED"
    assert result.relative_path == intent.actual_relative_path
    assert index.get_add_intent(CANDIDATE_ID, REVIEW_ID, 2, "ADD") is None


def test_add_intent_survives_crash_before_promotion_without_false_receipt_or_delete(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pre-promotion crash keeps staging and intent but never removes previous."""

    index = SyncIndex(sync_root)
    previous = PurePosixPath(f"images/OLD/{CANDIDATE_ID}.jpg")
    manifest_row = row(
        target_relative_path=PurePosixPath(f"images/SF006/{CANDIDATE_ID}.image"),
        previous_relative_path=previous,
    )
    old = seed_prior(sync_root, index, manifest_row)
    content, phash = encoded_image("PNG", (19, 13))
    verified = download(
        sync_root,
        manifest_row,
        content=content,
        format="PNG",
        suffix=".png",
        phash=phash,
        width=19,
        height=13,
    )
    original_link = operations._link_no_clobber
    failed = False

    def crash_once(*args: object, **kwargs: object) -> None:
        nonlocal failed
        if not failed:
            failed = True
            raise OperationError("FILESYSTEM_OPERATION_FAILED")
        original_link(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(operations, "_link_no_clobber", crash_once)
    with pytest.raises(OperationError, match="FILESYSTEM_OPERATION_FAILED"):
        apply_add(sync_root, manifest_row, verified, index)

    assert index.get_add_intent(CANDIDATE_ID, REVIEW_ID, 2, "ADD") is not None
    assert index.get_completed(CANDIDATE_ID, REVIEW_ID, 2, "ADD") is None
    assert old.read_bytes() == JPEG_BYTES
    assert verified.staging_path.read_bytes() == content

    result = recover_add(sync_root, manifest_row, index)

    assert result is not None
    assert result.status == "SUCCEEDED"
    assert not old.exists()
    assert not verified.staging_path.exists()
    assert index.get_add_intent(CANDIDATE_ID, REVIEW_ID, 2, "ADD") is None


def test_stale_add_intent_filesystem_change_during_clear_fails_closed(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = SyncIndex(sync_root)
    manifest_row = row()
    content, phash = encoded_image("JPEG")
    expected = index.record_add_intent(
        SyncResult(
            candidate_id=manifest_row.candidate_id,
            review_id=manifest_row.review_id,
            review_version=manifest_row.review_version,
            action="ADD",
            batch_id=manifest_row.batch_id,
            relative_path=manifest_row.target_relative_path,
            sha256=hashlib.sha256(content).hexdigest(),
            perceptual_hash=phash,
        ),
        manifest_row.target_relative_path,
    )
    original_clear = SyncIndex.clear_add_intent_if_matches
    target = local_path(sync_root, manifest_row.target_relative_path)

    def clear_then_create(self: SyncIndex, intent):
        cleared = original_clear(self, intent)
        target.write_bytes(content)
        return cleared

    monkeypatch.setattr(SyncIndex, "clear_add_intent_if_matches", clear_then_create)

    with pytest.raises(OperationError, match="ADD_RECOVERY_STATE_CHANGED"):
        recover_add(sync_root, manifest_row, index)

    assert target.read_bytes() == content
    assert index.get_completed(CANDIDATE_ID, REVIEW_ID, 2, "ADD") is None
    assert expected.sha256 == hashlib.sha256(target.read_bytes()).hexdigest()


def test_stale_add_intent_changed_during_cas_is_not_deleted(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = SyncIndex(sync_root)
    manifest_row = row()
    expected = index.record_add_intent(
        SyncResult(
            candidate_id=manifest_row.candidate_id,
            review_id=manifest_row.review_id,
            review_version=manifest_row.review_version,
            action="ADD",
            batch_id=manifest_row.batch_id,
            relative_path=manifest_row.target_relative_path,
            sha256="a" * 64,
            perceptual_hash=PHASH,
        ),
        manifest_row.target_relative_path,
    )
    changed_sha = "b" * 64
    original_clear = SyncIndex.clear_add_intent_if_matches

    def replace_then_compare(self: SyncIndex, intent):
        with self.connect() as connection:
            connection.execute(
                "UPDATE pending_adds SET sha256 = ? WHERE candidate_id = ? "
                "AND review_id = ? AND review_version = ? AND action = ?",
                (
                    changed_sha,
                    str(expected.candidate_id),
                    str(expected.review_id),
                    expected.review_version,
                    expected.action,
                ),
            )
            connection.commit()
        return original_clear(self, intent)

    monkeypatch.setattr(SyncIndex, "clear_add_intent_if_matches", replace_then_compare)

    with pytest.raises(OperationError, match="ADD_RECOVERY_INTENT_CONFLICT"):
        recover_add(sync_root, manifest_row, index)

    current = index.get_add_intent(CANDIDATE_ID, REVIEW_ID, 2, "ADD")
    assert current is not None
    assert current.sha256 == changed_sha


@pytest.mark.parametrize("durable_location", ["target", "shared-stage"])
def test_reconcile_older_ordinary_intent_completes_verified_durable_transition(
    sync_root: Path,
    durable_location: str,
) -> None:
    index = SyncIndex(sync_root)
    content, phash = encoded_image("JPEG", (19, 13))
    older = row(review_id=OLD_REVIEW_ID, review_version=9)
    intent = index.record_add_intent(
        SyncResult(
            candidate_id=older.candidate_id,
            review_id=older.review_id,
            review_version=older.review_version,
            action="ADD",
            batch_id=older.batch_id,
            relative_path=older.target_relative_path,
            sha256=hashlib.sha256(content).hexdigest(),
            perceptual_hash=phash,
        ),
        older.target_relative_path,
    )
    target = local_path(sync_root, intent.actual_relative_path)
    shared_stage = local_path(
        sync_root,
        intent.target_relative_path.with_name(intent.target_relative_path.name + ".part"),
    )
    durable = target if durable_location == "target" else shared_stage
    write_file(
        sync_root,
        PurePosixPath(*durable.relative_to(sync_root).parts),
        content,
    )
    newer = row(
        "MOVE",
        review_id=REVIEW_ID,
        review_version=10,
        previous_relative_path=older.target_relative_path,
    )

    operations.reconcile_older_add_intents(sync_root, newer, index)

    completed = index.get_completed(
        older.candidate_id, older.review_id, older.review_version, "ADD"
    )
    assert completed is not None
    assert completed.relative_path == intent.actual_relative_path
    assert completed.sha256 == intent.sha256
    assert completed.perceptual_hash == intent.perceptual_hash
    assert target.read_bytes() == content
    assert not shared_stage.exists()
    assert index.get_add_intent(
        older.candidate_id, older.review_id, older.review_version, "ADD"
    ) is None


@pytest.mark.parametrize("tampered_location", ["target", "shared-stage"])
def test_reconcile_older_ordinary_intent_rejects_unowned_durable_state(
    sync_root: Path,
    tampered_location: str,
) -> None:
    index = SyncIndex(sync_root)
    expected, expected_phash = encoded_image("JPEG", (19, 13))
    tampered, _tampered_phash = encoded_image("JPEG", (23, 17))
    older = row(review_id=OLD_REVIEW_ID, review_version=9)
    intent = index.record_add_intent(
        SyncResult(
            candidate_id=older.candidate_id,
            review_id=older.review_id,
            review_version=older.review_version,
            action="ADD",
            batch_id=older.batch_id,
            relative_path=older.target_relative_path,
            sha256=hashlib.sha256(expected).hexdigest(),
            perceptual_hash=expected_phash,
        ),
        older.target_relative_path,
    )
    target = local_path(sync_root, intent.actual_relative_path)
    shared_stage = local_path(
        sync_root,
        intent.target_relative_path.with_name(intent.target_relative_path.name + ".part"),
    )
    tampered_path = target if tampered_location == "target" else shared_stage
    write_file(
        sync_root,
        PurePosixPath(*tampered_path.relative_to(sync_root).parts),
        tampered,
    )
    newer = row(
        "REMOVE",
        review_id=REVIEW_ID,
        review_version=10,
        previous_relative_path=older.target_relative_path,
    )

    with pytest.raises(OperationError, match="ADD_RECOVERY_STATE_CHANGED"):
        operations.reconcile_older_add_intents(sync_root, newer, index)

    assert tampered_path.read_bytes() == tampered
    assert index.get_completed(
        older.candidate_id, older.review_id, older.review_version, "ADD"
    ) is None
    assert index.get_add_intent(
        older.candidate_id, older.review_id, older.review_version, "ADD"
    ) == intent


def test_recover_add_exact_completed_record_returns_canonical_skip(
    sync_root: Path,
) -> None:
    index = SyncIndex(sync_root)
    manifest_row = row()
    content, expected_phash = encoded_image("JPEG")
    write_file(sync_root, manifest_row.target_relative_path, content)
    stored = index.record_success(
        SyncResult(
            candidate_id=manifest_row.candidate_id,
            review_id=manifest_row.review_id,
            review_version=manifest_row.review_version,
            action=manifest_row.action,
            batch_id=manifest_row.batch_id,
            relative_path=manifest_row.target_relative_path,
            sha256=hashlib.sha256(content).hexdigest(),
            perceptual_hash=expected_phash,
        )
    )

    result = recover_add(sync_root, manifest_row, index)

    assert result is not None
    assert result.status == "SKIPPED_ALREADY_COMPLETED"
    assert result.relative_path == stored.relative_path
    assert result.sha256 == stored.sha256
    assert result.perceptual_hash == stored.perceptual_hash
    assert result.completed_at == stored.completed_at


def test_recover_add_does_not_infer_decoder_target_without_durable_intent(
    sync_root: Path,
) -> None:
    index = SyncIndex(sync_root)
    manifest_row = row(
        target_relative_path=PurePosixPath(f"images/SF006/{CANDIDATE_ID}.image")
    )
    content, expected_phash = encoded_image("PNG", (13, 9))
    actual_relative = PurePosixPath(f"images/SF006/{CANDIDATE_ID}.png")
    write_file(sync_root, actual_relative, content)

    result = recover_add(sync_root, manifest_row, index)

    assert result is None
    assert local_path(sync_root, actual_relative).read_bytes() == content
    assert index.get_completed(CANDIDATE_ID, REVIEW_ID, 2, "ADD") is None


def test_recover_add_preserves_compatible_uppercase_server_suffix(
    sync_root: Path,
) -> None:
    index = SyncIndex(sync_root)
    manifest_row = row(
        target_relative_path=PurePosixPath(f"images/SF006/{CANDIDATE_ID}.JPG")
    )
    content, _expected_phash = encoded_image("JPEG")
    actual_relative = PurePosixPath(f"images/SF006/{CANDIDATE_ID}.jpg")
    write_file(sync_root, actual_relative, content)

    result = recover_add(sync_root, manifest_row, index)

    assert result is not None
    assert result.relative_path == manifest_row.target_relative_path
    assert index.get_completed(CANDIDATE_ID, REVIEW_ID, 2, "ADD").relative_path == manifest_row.target_relative_path  # type: ignore[union-attr]


def test_recover_add_converges_target_and_staging_crash_state(
    sync_root: Path,
) -> None:
    index = SyncIndex(sync_root)
    manifest_row = row()
    content, expected_phash = encoded_image("JPEG", (15, 7))
    target = write_file(sync_root, manifest_row.target_relative_path, content)
    staging = target.with_name(target.name + ".part")
    os.link(target, staging)

    result = recover_add(sync_root, manifest_row, index)

    assert result is not None
    assert result.perceptual_hash == expected_phash
    assert target.read_bytes() == content
    assert not staging.exists()
    assert index.get_completed(CANDIDATE_ID, REVIEW_ID, 2, "ADD") is not None


def test_recover_add_does_not_promote_staging_without_durable_intent(
    sync_root: Path,
) -> None:
    index = SyncIndex(sync_root)
    manifest_row = row(
        target_relative_path=PurePosixPath(f"images/SF006/{CANDIDATE_ID}.image")
    )
    content, expected_phash = encoded_image("PNG", (17, 11))
    server_target = local_path(sync_root, manifest_row.target_relative_path)
    staging = server_target.with_name(server_target.name + ".part")
    staging.parent.mkdir(parents=True)
    staging.write_bytes(content)

    result = recover_add(sync_root, manifest_row, index)

    actual_relative = PurePosixPath(f"images/SF006/{CANDIDATE_ID}.png")
    assert result is None
    assert not local_path(sync_root, actual_relative).exists()
    assert staging.read_bytes() == content
    assert index.get_completed(CANDIDATE_ID, REVIEW_ID, 2, "ADD") is None


def test_recover_add_ignores_multiple_stray_alternate_candidates(
    sync_root: Path,
) -> None:
    index = SyncIndex(sync_root)
    manifest_row = row(
        target_relative_path=PurePosixPath(f"images/SF006/{CANDIDATE_ID}.image")
    )
    png, _phash = encoded_image("PNG")
    jpeg, _jpeg_phash = encoded_image("JPEG")
    png_relative = PurePosixPath(f"images/SF006/{CANDIDATE_ID}.png")
    jpeg_relative = PurePosixPath(f"images/SF006/{CANDIDATE_ID}.jpg")
    write_file(sync_root, png_relative, png)
    write_file(sync_root, jpeg_relative, jpeg)

    assert recover_add(sync_root, manifest_row, index) is None
    assert local_path(sync_root, png_relative).read_bytes() == png
    assert local_path(sync_root, jpeg_relative).read_bytes() == jpeg
    assert index.get_completed(CANDIDATE_ID, REVIEW_ID, 2, "ADD") is None


def test_recover_add_rejects_invalid_exact_server_target(sync_root: Path) -> None:
    index = SyncIndex(sync_root)
    manifest_row = row()
    write_file(sync_root, manifest_row.target_relative_path, b"not-an-image")

    with pytest.raises(OperationError, match="ADD_RECOVERY_INVALID"):
        recover_add(sync_root, manifest_row, index)

    assert index.get_completed(CANDIDATE_ID, REVIEW_ID, 2, "ADD") is None


def test_recover_add_cleans_composite_previous_after_target_verification(
    sync_root: Path,
) -> None:
    index = SyncIndex(sync_root)
    previous = PurePosixPath(f"images/OLD/{CANDIDATE_ID}.jpg")
    manifest_row = row(previous_relative_path=previous)
    old = seed_prior(sync_root, index, manifest_row)
    content, _phash = encoded_image("JPEG")
    target = write_file(sync_root, manifest_row.target_relative_path, content)

    result = recover_add(sync_root, manifest_row, index)

    assert result is not None
    assert target.read_bytes() == content
    assert not old.exists()
    assert index.get_completed(CANDIDATE_ID, REVIEW_ID, 2, "ADD") is not None


def test_apply_add_retains_staging_until_index_success(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = SyncIndex(sync_root)
    manifest_row = row()
    verified = download(sync_root, manifest_row)
    original_record = SyncIndex.record_success
    observed_staging = False

    def observe_staging(self: SyncIndex, result: SyncResult):
        nonlocal observed_staging
        if result.action == "ADD":
            observed_staging = verified.staging_path.is_file()
        return original_record(self, result)

    monkeypatch.setattr(SyncIndex, "record_success", observe_staging)

    result = apply_add(sync_root, manifest_row, verified, index)

    assert result.status == "SUCCEEDED"
    assert observed_staging
    assert not verified.staging_path.exists()


def test_apply_add_non_manifest_row_raises_secret_free_operation_error(
    sync_root: Path,
) -> None:
    index = SyncIndex(sync_root)
    malicious = {
        "batch_id": RECEIPT_TOKEN,
        "target_relative_path": f"C:/{RECEIPT_TOKEN}/fish.jpg",
    }

    with pytest.raises(OperationError, match="INVALID_ROW") as caught:
        apply_add(sync_root, malicious, object(), index)  # type: ignore[arg-type]

    assert_secret_free(caught.value, sync_root)
    assert not (sync_root / "logs").exists()


def test_malformed_manifest_paths_are_never_logged_or_exposed(
    sync_root: Path,
) -> None:
    index = SyncIndex(sync_root)
    malicious_path = PurePosixPath(f"/{RECEIPT_TOKEN}/absolute-secret.jpg")
    manifest_row = row(
        target_relative_path=malicious_path,
        previous_relative_path=malicious_path,
        original_url=f"https://example.test/{RECEIPT_TOKEN}",
    )

    with pytest.raises(OperationError, match="PATH_UNSAFE") as caught:
        apply_add(sync_root, manifest_row, object(), index)  # type: ignore[arg-type]

    assert_secret_free(caught.value, sync_root)
    logs = sync_root / "logs"
    serialized = "" if not logs.exists() else "".join(
        path.read_text(encoding="utf-8") for path in logs.iterdir()
    )
    assert RECEIPT_TOKEN not in serialized
    assert malicious_path.as_posix() not in serialized


def test_logger_setup_exception_is_sanitized_without_raw_chain(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = SyncIndex(sync_root)
    logger = OperationLogger(sync_root, BATCH_ID)
    manifest_row = row()
    verified = download(sync_root, manifest_row)

    def fail_validate(self: OperationLogger) -> None:
        raise RuntimeError(f"{RECEIPT_TOKEN} {sync_root.resolve()}")

    monkeypatch.setattr(OperationLogger, "validate", fail_validate)
    with pytest.raises(OperationError, match="LOG_SETUP_FAILED") as caught:
        apply_add(sync_root, manifest_row, verified, index, logger=logger)

    assert_secret_free(caught.value, sync_root)
    assert verified.staging_path.read_bytes() == JPEG_BYTES


def test_unsafe_log_parent_failure_is_secret_free_and_non_mutating(
    sync_root: Path, tmp_path: Path
) -> None:
    index = SyncIndex(sync_root)
    outside = tmp_path / RECEIPT_TOKEN
    outside.mkdir()
    logs = sync_root / "logs"
    try:
        logs.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    manifest_row = row()
    verified = DownloadResult(
        sync_root / "unused.part", JPEG_SHA, PHASH, len(JPEG_BYTES), "JPEG", ".jpg", 1, 1
    )

    with pytest.raises(OperationError, match="LOG_PATH_UNSAFE") as caught:
        apply_add(sync_root, manifest_row, verified, index)

    assert_secret_free(caught.value, sync_root)
    assert list(outside.iterdir()) == []
