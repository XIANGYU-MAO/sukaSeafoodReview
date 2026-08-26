from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import traceback
from uuid import UUID

import pytest

from conftest import BATCH_ID, CANDIDATE_ID, RECEIPT_TOKEN, REVIEW_ID
from sukaseafood_sync.downloader import DownloadResult
from sukaseafood_sync.index import SyncIndex, SyncResult
from sukaseafood_sync.manifest import ManifestRow
from sukaseafood_sync.operations import (
    OperationError,
    OperationLogger,
    apply_add,
    apply_move,
    apply_remove,
)


OLD_REVIEW_ID = UUID("44444444-4444-4444-8444-444444444444")
OTHER_CANDIDATE_ID = UUID("55555555-5555-4555-8555-555555555555")
JPEG_BYTES = b"verified-jpeg-payload"
JPEG_SHA = hashlib.sha256(JPEG_BYTES).hexdigest()
PHASH = "0123456789abcdef"


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
