from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
import os
from pathlib import Path, PurePosixPath
import sqlite3
import stat
import subprocess
import sys
from threading import Barrier
import traceback
from uuid import UUID, uuid4

import pytest

from conftest import BATCH_ID, CANDIDATE_ID, RECEIPT_TOKEN, REVIEW_ID
from sukaseafood_sync.index import (
    IndexConflict,
    IndexNotFound,
    SyncIndex,
    SyncIndexError,
    SyncResult,
)


def result(**overrides: object) -> SyncResult:
    values: dict[str, object] = {
        "candidate_id": CANDIDATE_ID,
        "review_id": REVIEW_ID,
        "review_version": 1,
        "action": "ADD",
        "batch_id": BATCH_ID,
        "relative_path": PurePosixPath(f"images/SF006/{CANDIDATE_ID}.jpg"),
        "sha256": "a" * 64,
        "perceptual_hash": "ABCDEF0123456789",
        "completed_at": datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return SyncResult(**values)  # type: ignore[arg-type]


def assert_secret_free_exception_graph(error: BaseException) -> None:
    pending = [error]
    seen: set[int] = set()
    chain: list[BaseException] = []
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        chain.append(current)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)

    assert not any(isinstance(item, RuntimeError) for item in chain)
    surfaces = [
        *(str(item) for item in chain),
        *(repr(item) for item in chain),
        "".join(
            traceback.format_exception(
                type(error), error, error.__traceback__, chain=True
            )
        ),
    ]
    assert all(RECEIPT_TOKEN not in surface for surface in surfaces)


def test_constructing_index_creates_exact_root_database_and_durable_schema(
    sync_root: Path,
) -> None:
    index = SyncIndex(sync_root)

    assert sync_root.is_dir()
    assert index.root == sync_root.resolve()
    assert index.path == sync_root.resolve() / ".sukaseafood-sync.sqlite3"
    with index.connect() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] >= 5_000
        assert connection.execute("PRAGMA synchronous").fetchone()[0] >= 2
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        columns = [row[1] for row in connection.execute("PRAGMA table_info(synced_items)")]
        pending_columns = [
            row[1] for row in connection.execute("PRAGMA table_info(pending_adds)")
        ]
    assert columns == [
        "candidate_id",
        "review_id",
        "review_version",
        "action",
        "batch_id",
        "relative_path",
        "sha256",
        "perceptual_hash",
        "completed_at",
        "receipt_submitted_at",
    ]
    assert pending_columns == [
        "candidate_id",
        "review_id",
        "review_version",
        "action",
        "batch_id",
        "target_relative_path",
        "actual_relative_path",
        "sha256",
        "perceptual_hash",
    ]
    assert not any(
        "token" in column or "url" in column
        for column in [*columns, *pending_columns]
    )


def test_connection_context_closes_owned_connection(sync_root: Path) -> None:
    index = SyncIndex(sync_root)
    with index.connect() as connection:
        assert connection.execute("SELECT 1").fetchone()[0] == 1

    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def test_existing_v1_index_migrates_pending_intents_without_losing_success(
    sync_root: Path,
) -> None:
    """Installing intent support must preserve an existing durable completion row."""

    sync_root.mkdir(parents=True, exist_ok=True)
    path = sync_root / ".sukaseafood-sync.sqlite3"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            """
            CREATE TABLE synced_items (
                candidate_id TEXT NOT NULL,
                review_id TEXT NOT NULL,
                review_version INTEGER NOT NULL CHECK (
                    review_version >= 1 AND review_version <= 9223372036854775807
                ),
                action TEXT NOT NULL CHECK (action IN ('ADD', 'MOVE', 'REMOVE')),
                batch_id TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                perceptual_hash TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                receipt_submitted_at TEXT,
                PRIMARY KEY (candidate_id, review_id, review_version, action)
            )
            """
        )
        connection.execute(
            "INSERT INTO synced_items VALUES (?, ?, 1, 'ADD', ?, ?, ?, ?, ?, NULL)",
            (
                str(CANDIDATE_ID),
                str(REVIEW_ID),
                str(BATCH_ID),
                f"images/SF006/{CANDIDATE_ID}.jpg",
                "a" * 64,
                "abcdef0123456789",
                "2026-08-27T00:00:00.000000+00:00",
            ),
        )
        connection.execute("PRAGMA user_version = 1")
        connection.commit()

    index = SyncIndex(sync_root)

    stored = index.get_completed(CANDIDATE_ID, REVIEW_ID, 1, "ADD")
    assert stored is not None
    assert stored.sha256 == "a" * 64
    with index.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute(
            "SELECT count(*) FROM pending_adds"
        ).fetchone()[0] == 0


def test_rejects_index_symlink_outside_root_without_touching_target(
    sync_root: Path, tmp_path: Path
) -> None:
    sync_root.mkdir()
    outside = tmp_path / "outside.sqlite3"
    original = b"outside-target-must-remain-byte-identical"
    outside.write_bytes(original)
    index_path = sync_root / ".sukaseafood-sync.sqlite3"
    try:
        index_path.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"file symlink creation unavailable: {exc}")

    with pytest.raises(SyncIndexError, match="symlink|reparse"):
        SyncIndex(sync_root)

    assert outside.read_bytes() == original


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse attribute is Windows-only")
def test_rejects_windows_reparse_point_before_sqlite_open(
    sync_root: Path, tmp_path: Path
) -> None:
    sync_root.mkdir()
    outside = tmp_path / "outside-directory"
    outside.mkdir()
    marker = outside / "marker.bin"
    marker.write_bytes(b"unchanged")
    index_path = sync_root / ".sukaseafood-sync.sqlite3"
    try:
        index_path.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory reparse creation unavailable: {exc}")
    attributes = getattr(os.lstat(index_path), "st_file_attributes", 0)
    assert attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT

    with pytest.raises(SyncIndexError, match="reparse"):
        SyncIndex(sync_root)

    assert marker.read_bytes() == b"unchanged"


def test_absent_database_rejects_linked_rollback_journal_before_sqlite_open(
    sync_root: Path, tmp_path: Path
) -> None:
    sync_root.mkdir()
    database = sync_root / ".sukaseafood-sync.sqlite3"
    journal = Path(f"{database}-journal")
    outside = tmp_path / "outside-journal.bin"
    original = b"outside-journal-must-not-change"
    outside.write_bytes(original)
    try:
        journal.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"journal symlink creation unavailable: {exc}")

    try:
        with pytest.raises(SyncIndexError, match="sidecar|symlink|reparse"):
            SyncIndex(sync_root)
    finally:
        assert outside.read_bytes() == original

    assert not database.exists()
    assert journal.is_symlink()


def test_regular_rollback_journal_removed_after_lstat_is_treated_as_absent(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_root.mkdir()
    database = sync_root / ".sukaseafood-sync.sqlite3"
    journal = Path(f"{database}-journal")
    journal.write_bytes(b"transient SQLite rollback journal")
    original_resolve = Path.resolve
    removed_after_lstat = False

    def remove_journal_before_strict_resolution(
        candidate: Path, strict: bool = False
    ) -> Path:
        nonlocal removed_after_lstat
        if candidate == journal and strict and not removed_after_lstat:
            removed_after_lstat = True
            journal.unlink()
        return original_resolve(candidate, strict=strict)

    monkeypatch.setattr(Path, "resolve", remove_journal_before_strict_resolution)

    index = SyncIndex(sync_root)

    assert removed_after_lstat
    assert index.path == database
    assert index.path.is_file()


def test_sidecar_transient_resolution_failure_then_disappearance_is_absent(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = SyncIndex(sync_root)
    journal = Path(f"{index.path}-journal")
    journal.write_bytes(b"transient SQLite rollback journal")
    original_resolve = Path.resolve
    failures = 0

    def disappear_and_fail_once(candidate: Path, strict: bool = False) -> Path:
        nonlocal failures
        if candidate == journal and strict and failures == 0:
            failures += 1
            journal.unlink()
            raise OSError("transient SQLite sidecar lifecycle")
        return original_resolve(candidate, strict=strict)

    monkeypatch.setattr(Path, "resolve", disappear_and_fail_once)

    index._validate_sqlite_path(journal, sidecar=True)

    assert failures == 1
    assert not journal.exists()


def test_sidecar_transient_resolution_failure_then_identity_change_retries(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = SyncIndex(sync_root)
    journal = Path(f"{index.path}-journal")
    retired = sync_root / "retired-transient-journal"
    journal.write_bytes(b"first SQLite sidecar identity")
    original_resolve = Path.resolve
    resolutions = 0

    def replace_and_fail_once(candidate: Path, strict: bool = False) -> Path:
        nonlocal resolutions
        if candidate == journal and strict:
            resolutions += 1
            if resolutions == 1:
                journal.replace(retired)
                journal.write_bytes(b"second SQLite sidecar identity")
                raise OSError("transient SQLite sidecar lifecycle")
        return original_resolve(candidate, strict=strict)

    monkeypatch.setattr(Path, "resolve", replace_and_fail_once)

    index._validate_sqlite_path(journal, sidecar=True)

    assert resolutions == 2
    assert retired.read_bytes() == b"first SQLite sidecar identity"
    assert journal.read_bytes() == b"second SQLite sidecar identity"


def test_same_sidecar_with_persistent_resolution_failure_exhausts_bound(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = SyncIndex(sync_root)
    journal = Path(f"{index.path}-journal")
    journal.write_bytes(b"same regular SQLite sidecar")
    original_resolve = Path.resolve
    resolutions = 0

    def fail_same_entry(candidate: Path, strict: bool = False) -> Path:
        nonlocal resolutions
        if candidate == journal and strict:
            resolutions += 1
            raise OSError("persistent SQLite sidecar resolution failure")
        return original_resolve(candidate, strict=strict)

    monkeypatch.setattr(Path, "resolve", fail_same_entry)

    with pytest.raises(SyncIndexError, match="sidecar.*selected root"):
        index._validate_sqlite_path(journal, sidecar=True)

    assert resolutions == 2
    assert journal.read_bytes() == b"same regular SQLite sidecar"


@pytest.mark.parametrize("replacement", ["symlink", "directory"])
def test_sidecar_symlink_reparse_or_nonregular_swap_is_rejected_without_touching_outside(
    sync_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
) -> None:
    index = SyncIndex(sync_root)
    journal = Path(f"{index.path}-journal")
    journal.write_bytes(b"regular before inspection")
    outside = tmp_path / "outside-sidecar-target.bin"
    original = b"outside bytes must remain unchanged"
    outside.write_bytes(original)
    original_resolve = Path.resolve
    replaced_after_lstat = False

    def replace_journal_before_strict_resolution(
        candidate: Path, strict: bool = False
    ) -> Path:
        nonlocal replaced_after_lstat
        if candidate == journal and strict and not replaced_after_lstat:
            replaced_after_lstat = True
            journal.unlink()
            if replacement == "symlink":
                journal.symlink_to(outside)
            else:
                journal.mkdir()
        return original_resolve(candidate, strict=strict)

    monkeypatch.setattr(Path, "resolve", replace_journal_before_strict_resolution)

    try:
        with pytest.raises(SyncIndexError, match="sidecar|symlink|reparse|regular"):
            index._validate_sqlite_path(journal, sidecar=True)
    finally:
        assert outside.read_bytes() == original

    assert replaced_after_lstat
    if replacement == "symlink" and os.name == "nt":
        attributes = getattr(os.lstat(journal), "st_file_attributes", 0)
        assert attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT


def test_sidecar_reinspection_failure_has_stable_secret_free_exception_graph(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = SyncIndex(sync_root)
    journal = Path(f"{index.path}-journal")
    journal.write_bytes(b"regular before inspection")
    original_lstat = os.lstat
    journal_inspections = 0

    def fail_journal_reinspection(
        candidate: os.PathLike[str] | str,
    ) -> os.stat_result:
        nonlocal journal_inspections
        if Path(candidate) == journal:
            journal_inspections += 1
            if journal_inspections == 2:
                raise OSError(f"unsafe inspection detail: {RECEIPT_TOKEN}")
        return original_lstat(candidate)

    monkeypatch.setattr(os, "lstat", fail_journal_reinspection)

    with pytest.raises(SyncIndexError, match="cannot be inspected safely") as caught:
        index._validate_sqlite_path(journal, sidecar=True)

    assert journal_inspections == 2
    assert_secret_free_exception_graph(caught.value)


def test_sidecar_that_keeps_changing_fails_closed_after_bounded_validation(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = SyncIndex(sync_root)
    journal = Path(f"{index.path}-journal")
    journal.write_bytes(b"first journal identity")
    original_resolve = Path.resolve
    replacements = 0

    def replace_journal_on_every_strict_resolution(
        candidate: Path, strict: bool = False
    ) -> Path:
        nonlocal replacements
        if candidate == journal and strict:
            retired = sync_root / f"retired-journal-{replacements}"
            journal.replace(retired)
            replacements += 1
            journal.write_bytes(f"journal identity {replacements}".encode())
        return original_resolve(candidate, strict=strict)

    monkeypatch.setattr(Path, "resolve", replace_journal_on_every_strict_resolution)

    with pytest.raises(SyncIndexError, match="changed during validation") as caught:
        index._validate_sqlite_path(journal, sidecar=True)

    assert replacements == 2
    assert_secret_free_exception_graph(caught.value)


def test_main_database_removed_after_lstat_is_not_treated_as_transient(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = SyncIndex(sync_root)
    original_resolve = Path.resolve
    removed_after_lstat = False

    def remove_database_before_strict_resolution(
        candidate: Path, strict: bool = False
    ) -> Path:
        nonlocal removed_after_lstat
        if candidate == index.path and strict and not removed_after_lstat:
            removed_after_lstat = True
            index.path.unlink()
        return original_resolve(candidate, strict=strict)

    monkeypatch.setattr(Path, "resolve", remove_database_before_strict_resolution)

    with pytest.raises(SyncIndexError, match="index.*selected root"):
        index._validate_sqlite_path(index.path, sidecar=False)

    assert removed_after_lstat


@pytest.mark.parametrize("replacement", ["dangling_symlink", "directory"])
def test_resolution_missing_reinspection_rejects_unsafe_sidecar_before_retry(
    sync_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
) -> None:
    index = SyncIndex(sync_root)
    journal = Path(f"{index.path}-journal")
    journal.write_bytes(b"regular before strict resolution")
    dangling_target = tmp_path / RECEIPT_TOKEN / "missing-target.bin"
    outside = tmp_path / "outside-marker.bin"
    original = b"outside bytes must stay unchanged"
    outside.write_bytes(original)
    original_resolve = Path.resolve
    original_lstat = os.lstat
    replacement_created = False
    replacement_reinspected = False
    reinspected_metadata: os.stat_result | None = None

    def replace_during_strict_resolution(
        candidate: Path, strict: bool = False
    ) -> Path:
        nonlocal replacement_created
        if candidate != journal or not strict or replacement_created:
            return original_resolve(candidate, strict=strict)
        journal.unlink()
        replacement_created = True
        if replacement == "dangling_symlink":
            try:
                journal.symlink_to(dangling_target)
            except OSError as exc:
                pytest.skip(f"dangling sidecar symlink creation unavailable: {exc}")
            return original_resolve(candidate, strict=strict)
        try:
            return original_resolve(candidate, strict=strict)
        except FileNotFoundError:
            journal.mkdir()
            raise

    def remove_unsafe_entry_after_reinspection(
        candidate: os.PathLike[str] | str,
    ) -> os.stat_result:
        nonlocal replacement_reinspected, reinspected_metadata
        metadata = original_lstat(candidate)
        if Path(candidate) == journal and replacement_created:
            replacement_reinspected = True
            reinspected_metadata = metadata
            if stat.S_ISLNK(metadata.st_mode):
                journal.unlink()
            else:
                journal.rmdir()
        return metadata

    monkeypatch.setattr(Path, "resolve", replace_during_strict_resolution)
    monkeypatch.setattr(os, "lstat", remove_unsafe_entry_after_reinspection)

    with pytest.raises(
        SyncIndexError, match="symlink|reparse|regular"
    ) as caught:
        index._validate_sqlite_path(journal, sidecar=True)

    assert replacement_reinspected
    assert reinspected_metadata is not None
    if replacement == "dangling_symlink" and os.name == "nt":
        attributes = getattr(reinspected_metadata, "st_file_attributes", 0)
        assert attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
    assert outside.read_bytes() == original
    assert_secret_free_exception_graph(caught.value)


@pytest.mark.parametrize("replacement", ["dangling_symlink", "directory"])
def test_post_resolve_absence_reinspection_rejects_unsafe_sidecar_before_retry(
    sync_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
) -> None:
    index = SyncIndex(sync_root)
    journal = Path(f"{index.path}-journal")
    journal.write_bytes(b"regular through strict resolution")
    dangling_target = tmp_path / RECEIPT_TOKEN / "missing-target.bin"
    outside = tmp_path / "outside-marker.bin"
    original = b"outside bytes must stay unchanged"
    outside.write_bytes(original)
    original_resolve = Path.resolve
    original_lstat = os.lstat
    removed_after_resolution = False
    replacement_created = False
    replacement_reinspected = False
    reinspected_metadata: os.stat_result | None = None

    def remove_after_successful_strict_resolution(
        candidate: Path, strict: bool = False
    ) -> Path:
        nonlocal removed_after_resolution
        resolved = original_resolve(candidate, strict=strict)
        if candidate == journal and strict and not removed_after_resolution:
            removed_after_resolution = True
            journal.unlink()
        return resolved

    def inject_unsafe_entry_for_absence_reinspection(
        candidate: os.PathLike[str] | str,
    ) -> os.stat_result:
        nonlocal replacement_created, replacement_reinspected, reinspected_metadata
        try:
            metadata = original_lstat(candidate)
        except FileNotFoundError:
            if (
                Path(candidate) != journal
                or not removed_after_resolution
                or replacement_created
            ):
                raise
            replacement_created = True
            if replacement == "dangling_symlink":
                try:
                    journal.symlink_to(dangling_target)
                except OSError as exc:
                    pytest.skip(f"dangling sidecar symlink creation unavailable: {exc}")
            else:
                journal.mkdir()
            raise
        if Path(candidate) == journal and replacement_created:
            replacement_reinspected = True
            reinspected_metadata = metadata
            if stat.S_ISLNK(metadata.st_mode):
                journal.unlink()
            else:
                journal.rmdir()
        return metadata

    monkeypatch.setattr(Path, "resolve", remove_after_successful_strict_resolution)
    monkeypatch.setattr(os, "lstat", inject_unsafe_entry_for_absence_reinspection)

    with pytest.raises(
        SyncIndexError, match="symlink|reparse|regular"
    ) as caught:
        index._validate_sqlite_path(journal, sidecar=True)

    assert replacement_reinspected
    assert reinspected_metadata is not None
    if replacement == "dangling_symlink" and os.name == "nt":
        attributes = getattr(reinspected_metadata, "st_file_attributes", 0)
        assert attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
    assert outside.read_bytes() == original
    assert_secret_free_exception_graph(caught.value)


@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
def test_canonical_database_rejects_linked_wal_sidecars_without_touching_them(
    sync_root: Path, tmp_path: Path, suffix: str
) -> None:
    index = SyncIndex(sync_root)
    sidecar = Path(f"{index.path}{suffix}")
    assert not sidecar.exists()
    outside = tmp_path / f"outside-{suffix[1:]}.bin"
    original = f"outside-{suffix}-must-not-change".encode()
    outside.write_bytes(original)
    try:
        sidecar.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"{suffix} symlink creation unavailable: {exc}")
    database_before = index.path.read_bytes()

    try:
        with pytest.raises(SyncIndexError, match="sidecar|symlink|reparse"):
            SyncIndex(sync_root)
    finally:
        assert outside.read_bytes() == original

    assert index.path.read_bytes() == database_before
    assert sidecar.is_symlink()


@pytest.mark.parametrize("attempt", range(3))
def test_concurrent_first_initialization_is_serialized_and_idempotent(
    sync_root: Path, attempt: int
) -> None:
    root = sync_root / f"attempt-{attempt}"
    worker_count = 16
    barrier = Barrier(worker_count)

    def construct() -> Path:
        barrier.wait(timeout=10)
        return SyncIndex(root).path

    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        paths = list(pool.map(lambda _number: construct(), range(worker_count)))

    expected = root.resolve() / ".sukaseafood-sync.sqlite3"
    assert paths == [expected] * worker_count
    with closing(sqlite3.connect(expected)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        objects = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall()
    assert [(kind, name, table) for kind, name, table, _sql in objects] == [
        ("index", "sqlite_autoindex_pending_adds_1", "pending_adds"),
        ("index", "sqlite_autoindex_synced_items_1", "synced_items"),
        ("table", "pending_adds", "pending_adds"),
        ("table", "synced_items", "synced_items"),
    ]


def test_records_and_checks_exact_four_part_completion_key(sync_root: Path) -> None:
    index = SyncIndex(sync_root)
    stored = index.record_success(result())

    assert result().status == "SUCCEEDED"
    assert stored.candidate_id == CANDIDATE_ID
    assert stored.review_id == REVIEW_ID
    assert stored.review_version == 1
    assert stored.action == "ADD"
    assert stored.sha256 == "a" * 64
    assert stored.perceptual_hash == "abcdef0123456789"
    assert index.is_completed(str(CANDIDATE_ID), REVIEW_ID, 1, "ADD")
    assert not index.is_completed(CANDIDATE_ID, REVIEW_ID, 2, "ADD")
    assert not index.is_completed(CANDIDATE_ID, REVIEW_ID, 1, "MOVE")


def test_exact_key_lookup_returns_record_or_none(sync_root: Path) -> None:
    index = SyncIndex(sync_root)
    stored = index.record_success(result())

    assert index.get_completed(CANDIDATE_ID, REVIEW_ID, 1, "ADD") == stored
    assert index.get_completed(CANDIDATE_ID, REVIEW_ID, 2, "ADD") is None


def test_pending_add_compare_and_delete_requires_every_expected_value(
    sync_root: Path,
) -> None:
    index = SyncIndex(sync_root)
    expected = index.record_add_intent(
        result(), PurePosixPath(f"images/SF006/{CANDIDATE_ID}.image")
    )
    changed = replace(expected, sha256="b" * 64)

    with index.connect() as connection:
        connection.execute(
            "UPDATE pending_adds SET sha256 = ? WHERE candidate_id = ?",
            (changed.sha256, str(CANDIDATE_ID)),
        )
        connection.commit()

    assert not index.clear_add_intent_if_matches(expected)
    assert index.get_add_intent(CANDIDATE_ID, REVIEW_ID, 1, "ADD") == changed
    assert index.clear_add_intent_if_matches(changed)
    assert index.get_add_intent(CANDIDATE_ID, REVIEW_ID, 1, "ADD") is None


def test_two_real_processes_clear_at_most_one_exact_pending_add_intent(
    sync_root: Path, tmp_path: Path
) -> None:
    index = SyncIndex(sync_root)
    expected = index.record_add_intent(
        result(), PurePosixPath(f"images/SF006/{CANDIDATE_ID}.image")
    )
    gate = tmp_path / "clear-start"
    worker = r"""
import sys
import time
from pathlib import Path, PurePosixPath
from uuid import UUID
from sukaseafood_sync.index import AddIntent, SyncIndex

root = Path(sys.argv[1])
gate = Path(sys.argv[2])
index = SyncIndex(root)
while not gate.exists():
    time.sleep(0.01)
expected = AddIntent(
    candidate_id=UUID(sys.argv[3]), review_id=UUID(sys.argv[4]),
    review_version=int(sys.argv[5]), action='ADD', batch_id=UUID(sys.argv[6]),
    target_relative_path=PurePosixPath(sys.argv[7]),
    actual_relative_path=PurePosixPath(sys.argv[8]), sha256=sys.argv[9],
    perceptual_hash=sys.argv[10],
)
print('CLEARED' if index.clear_add_intent_if_matches(expected) else 'UNCHANGED')
"""
    arguments = [
        str(sync_root),
        str(gate),
        str(expected.candidate_id),
        str(expected.review_id),
        str(expected.review_version),
        str(expected.batch_id),
        expected.target_relative_path.as_posix(),
        expected.actual_relative_path.as_posix(),
        expected.sha256,
        expected.perceptual_hash,
    ]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", worker, *arguments],
            cwd=Path(__file__).parents[1],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(2)
    ]
    gate.write_text("go", encoding="ascii")
    outputs = [process.communicate(timeout=30) for process in processes]

    observed = sorted(
        (process.returncode, stdout.strip(), stderr)
        for process, (stdout, stderr) in zip(processes, outputs, strict=True)
    )
    assert observed == [
        (0, "CLEARED", ""),
        (0, "UNCHANGED", ""),
    ]
    assert index.get_add_intent(CANDIDATE_ID, REVIEW_ID, 1, "ADD") is None


def test_record_success_rejects_non_success_invocation_status(sync_root: Path) -> None:
    index = SyncIndex(sync_root)

    with pytest.raises(SyncIndexError, match="status"):
        index.record_success(result(status="SKIPPED_ALREADY_COMPLETED"))

    assert index.get_completed(CANDIDATE_ID, REVIEW_ID, 1, "ADD") is None


def test_same_result_is_idempotent_but_same_key_conflict_is_rejected(
    sync_root: Path,
) -> None:
    index = SyncIndex(sync_root)
    first = index.record_success(result())
    same = index.record_success(result())

    assert same == first
    with pytest.raises(IndexConflict, match="conflict"):
        index.record_success(result(sha256="b" * 64))
    assert index.latest_for_candidate(CANDIDATE_ID) == first


def test_latest_candidate_state_marks_remove_absent_and_later_move_present(
    sync_root: Path,
) -> None:
    index = SyncIndex(sync_root)
    add = result()
    removed = result(
        review_id=UUID("44444444-4444-4444-8444-444444444444"),
        review_version=2,
        action="REMOVE",
        batch_id=UUID("55555555-5555-4555-8555-555555555555"),
        relative_path=PurePosixPath(f"_removed/{BATCH_ID}/{CANDIDATE_ID}.jpg"),
        completed_at=datetime(2026, 8, 26, 11, 0, tzinfo=timezone.utc),
    )
    moved = result(
        review_id=UUID("66666666-6666-4666-8666-666666666666"),
        review_version=3,
        action="MOVE",
        batch_id=UUID("77777777-7777-4777-8777-777777777777"),
        relative_path=PurePosixPath(f"images/SHELLFISH_A/{CANDIDATE_ID}.jpg"),
        completed_at=datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc),
    )

    index.record_success(add)
    index.record_success(removed)
    latest_removed = index.latest_for_candidate(CANDIDATE_ID)
    assert latest_removed is not None
    assert latest_removed.action == "REMOVE"
    assert not latest_removed.present
    assert index.find_present_by_sha256("a" * 64) is None

    index.record_success(moved)
    latest_moved = index.latest_for_candidate(CANDIDATE_ID)
    assert latest_moved is not None
    assert latest_moved.action == "MOVE"
    assert latest_moved.present
    assert index.find_present_by_sha256("A" * 64) == latest_moved


def test_sha_lookup_ignores_candidates_whose_latest_operation_removed_them(
    sync_root: Path,
) -> None:
    index = SyncIndex(sync_root)
    other_candidate = UUID("44444444-4444-4444-8444-444444444444")
    other = result(
        candidate_id=other_candidate,
        review_id=UUID("55555555-5555-4555-8555-555555555555"),
        relative_path=PurePosixPath(f"images/SF006/{other_candidate}.jpg"),
        completed_at=datetime(2026, 8, 26, 9, 0, tzinfo=timezone.utc),
    )
    index.record_success(other)
    index.record_success(result())
    index.record_success(
        result(
            review_id=UUID("66666666-6666-4666-8666-666666666666"),
            review_version=2,
            action="REMOVE",
            relative_path=PurePosixPath(f"_removed/{BATCH_ID}/{CANDIDATE_ID}.jpg"),
            completed_at=datetime(2026, 8, 26, 11, 0, tzinfo=timezone.utc),
        )
    )

    found = index.find_present_by_sha256("a" * 64)

    assert found is not None
    assert found.candidate_id == other_candidate


def test_marks_receipt_only_for_existing_exact_completed_key(sync_root: Path) -> None:
    index = SyncIndex(sync_root)
    index.record_success(result())
    submitted_at = datetime(2026, 8, 26, 13, 0, tzinfo=timezone.utc)

    marked = index.mark_receipt_submitted(
        CANDIDATE_ID, REVIEW_ID, 1, "ADD", submitted_at=submitted_at
    )
    repeated = index.mark_receipt_submitted(
        CANDIDATE_ID,
        REVIEW_ID,
        1,
        "ADD",
        submitted_at=datetime(2026, 8, 26, 14, 0, tzinfo=timezone.utc),
    )

    assert marked.receipt_submitted_at == submitted_at
    assert repeated.receipt_submitted_at == submitted_at
    with pytest.raises(IndexNotFound, match="completed"):
        index.mark_receipt_submitted(CANDIDATE_ID, REVIEW_ID, 2, "ADD")


def test_reopen_persists_records_and_receipt_state(sync_root: Path) -> None:
    first = SyncIndex(sync_root)
    first.record_success(result())
    first.mark_receipt_submitted(CANDIDATE_ID, REVIEW_ID, 1, "ADD")

    reopened = SyncIndex(sync_root)
    stored = reopened.latest_for_candidate(CANDIDATE_ID)

    assert stored is not None
    assert stored.relative_path == PurePosixPath(
        f"images/SF006/{CANDIDATE_ID}.jpg"
    )
    assert stored.receipt_submitted_at is not None


@pytest.mark.parametrize(
    "change",
    [
        {"candidate_id": "not-a-uuid"},
        {"review_id": "not-a-uuid"},
        {"batch_id": "not-a-uuid"},
        {"review_version": 0},
        {"review_version": True},
        {"action": "add"},
        {"relative_path": "../outside.jpg"},
        {"relative_path": "C:/outside.jpg"},
        {"sha256": "not-a-hash"},
        {"perceptual_hash": "not hex"},
        {"perceptual_hash": "a" * 257},
        {"completed_at": datetime(2026, 8, 26, 10, 0)},
    ],
)
def test_invalid_record_rolls_back_without_partial_row(
    sync_root: Path, change: dict[str, object]
) -> None:
    index = SyncIndex(sync_root)

    with pytest.raises((SyncIndexError, ValueError, TypeError)):
        index.record_success(result(**change))

    with index.connect() as connection:
        assert connection.execute("SELECT count(*) FROM synced_items").fetchone()[0] == 0


def test_conflict_transaction_preserves_original_record(sync_root: Path) -> None:
    index = SyncIndex(sync_root)
    original = index.record_success(result())

    with pytest.raises(IndexConflict):
        index.record_success(
            replace(
                result(),
                relative_path=PurePosixPath(f"images/SF006/{CANDIDATE_ID}.png"),
            )
        )

    assert index.latest_for_candidate(CANDIDATE_ID) == original


def test_revalidates_paths_read_from_database(sync_root: Path) -> None:
    index = SyncIndex(sync_root)
    index.record_success(result())
    with index.connect() as connection:
        connection.execute(
            "UPDATE synced_items SET relative_path = '../outside.jpg'"
        )
        connection.commit()

    with pytest.raises(SyncIndexError, match="relative_path"):
        index.latest_for_candidate(CANDIDATE_ID)


def test_rejects_stored_path_that_now_resolves_through_symlink(
    sync_root: Path, tmp_path: Path
) -> None:
    index = SyncIndex(sync_root)
    index.record_success(result())
    outside = tmp_path / "outside"
    outside.mkdir()
    images = sync_root / "images"
    try:
        images.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(SyncIndexError, match="root"):
        index.latest_for_candidate(CANDIDATE_ID)


@pytest.mark.parametrize(
    "component",
    [
        "CON .txt",
        "COM1 .jpg",
        "LPT¹ .image",
        "CONIN$ .bin",
        "CONOUT$  ..data",
        "CLOCK$ . .asset",
    ],
)
def test_index_rejects_windows_normalized_device_aliases(
    sync_root: Path, component: str
) -> None:
    index = SyncIndex(sync_root)

    with pytest.raises(SyncIndexError, match="reserved"):
        index.record_success(
            result(relative_path=PurePosixPath(f"images/{component}/fish.jpg"))
        )


def test_index_symlink_loop_has_no_raw_or_secret_bearing_exception_chain(
    sync_root: Path,
) -> None:
    index = SyncIndex(sync_root)
    secret_link = sync_root / RECEIPT_TOKEN
    peer_link = sync_root / "loop-peer"
    try:
        secret_link.symlink_to(peer_link, target_is_directory=True)
        peer_link.symlink_to(secret_link, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink loop creation unavailable: {exc}")

    with pytest.raises(SyncIndexError, match="root") as caught:
        index.record_success(
            result(
                relative_path=PurePosixPath(f"{RECEIPT_TOKEN}/fish.jpg")
            )
        )

    assert_secret_free_exception_graph(caught.value)


def test_rejects_future_or_incompatible_existing_schema(sync_root: Path) -> None:
    sync_root.mkdir()
    db = sync_root / ".sukaseafood-sync.sqlite3"
    with closing(sqlite3.connect(db)) as connection:
        connection.execute("PRAGMA user_version = 99")
        connection.commit()
    with pytest.raises(SyncIndexError, match="newer schema version"):
        SyncIndex(sync_root)

    db.unlink()
    with closing(sqlite3.connect(db)) as connection:
        connection.execute("CREATE TABLE synced_items (candidate_id TEXT)")
        connection.execute("PRAGMA user_version = 1")
        connection.commit()
    with pytest.raises(SyncIndexError, match="incompatible"):
        SyncIndex(sync_root)


def test_rejects_current_version_schema_with_wrong_types_or_nullability(
    sync_root: Path,
) -> None:
    sync_root.mkdir()
    db = sync_root / ".sukaseafood-sync.sqlite3"
    with closing(sqlite3.connect(db)) as connection:
        connection.execute(
            """
            CREATE TABLE synced_items (
                candidate_id TEXT,
                review_id TEXT NOT NULL,
                review_version TEXT NOT NULL,
                action TEXT NOT NULL,
                batch_id TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                perceptual_hash TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                receipt_submitted_at TEXT,
                PRIMARY KEY (candidate_id, review_id, review_version, action)
            )
            """
        )
        connection.execute("PRAGMA user_version = 1")
        connection.commit()

    with pytest.raises(SyncIndexError, match="incompatible"):
        SyncIndex(sync_root)


def test_rejects_exact_columns_and_primary_key_when_checks_are_missing(
    sync_root: Path,
) -> None:
    sync_root.mkdir()
    db = sync_root / ".sukaseafood-sync.sqlite3"
    with closing(sqlite3.connect(db)) as connection:
        connection.execute(
            """
            CREATE TABLE synced_items (
                candidate_id TEXT NOT NULL,
                review_id TEXT NOT NULL,
                review_version INTEGER NOT NULL,
                action TEXT NOT NULL,
                batch_id TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                perceptual_hash TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                receipt_submitted_at TEXT,
                PRIMARY KEY (candidate_id, review_id, review_version, action)
            )
            """
        )
        connection.execute("PRAGMA user_version = 1")
        connection.commit()

    with pytest.raises(SyncIndexError, match="incompatible"):
        SyncIndex(sync_root)


def test_schema_fingerprint_preserves_action_literal_case_and_database_bytes(
    sync_root: Path,
) -> None:
    sync_root.mkdir()
    db = sync_root / ".sukaseafood-sync.sqlite3"
    with closing(sqlite3.connect(db)) as connection:
        connection.execute(
            """
            CREATE TABLE synced_items (
                candidate_id TEXT NOT NULL,
                review_id TEXT NOT NULL,
                review_version INTEGER NOT NULL CHECK (
                    review_version >= 1 AND review_version <= 9223372036854775807
                ),
                action TEXT NOT NULL CHECK (action IN ('add', 'move', 'remove')),
                batch_id TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                perceptual_hash TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                receipt_submitted_at TEXT,
                PRIMARY KEY (candidate_id, review_id, review_version, action)
            )
            """
        )
        connection.execute("PRAGMA user_version = 1")
        connection.commit()
    before = db.read_bytes()

    with pytest.raises(SyncIndexError, match="incompatible"):
        SyncIndex(sync_root)

    assert db.read_bytes() == before
    with closing(sqlite3.connect(db)) as connection:
        assert connection.execute("SELECT count(*) FROM synced_items").fetchone()[0] == 0


def test_canonical_action_literal_case_is_accepted(sync_root: Path) -> None:
    index = SyncIndex(sync_root)

    assert index.path.is_file()
    assert not index.is_completed(CANDIDATE_ID, REVIEW_ID, 1, "ADD")


def test_rejects_sha_corrupting_trigger_before_any_record_and_without_db_change(
    sync_root: Path,
) -> None:
    index = SyncIndex(sync_root)
    with index.connect() as connection:
        connection.execute(
            """
            CREATE TRIGGER corrupt_sha AFTER INSERT ON synced_items
            BEGIN
                UPDATE synced_items SET sha256 = '0' WHERE rowid = NEW.rowid;
            END
            """
        )
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    before = index.path.read_bytes()

    with pytest.raises(SyncIndexError, match="incompatible"):
        SyncIndex(sync_root)

    assert index.path.read_bytes() == before
    with closing(sqlite3.connect(index.path)) as connection:
        assert connection.execute("SELECT count(*) FROM synced_items").fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type = 'trigger' AND name = 'corrupt_sha'"
        ).fetchone()[0] == 1


@pytest.mark.parametrize(
    "object_sql",
    [
        "CREATE TABLE unexpected_table (value TEXT)",
        "CREATE VIEW unexpected_view AS SELECT candidate_id FROM synced_items",
        "CREATE INDEX unexpected_index ON synced_items(sha256)",
    ],
    ids=["table", "view", "index"],
)
def test_rejects_every_unexpected_user_schema_object(
    sync_root: Path, object_sql: str
) -> None:
    index = SyncIndex(sync_root)
    with index.connect() as connection:
        connection.execute(object_sql)
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    before = index.path.read_bytes()

    with pytest.raises(SyncIndexError, match="incompatible"):
        SyncIndex(sync_root)

    assert index.path.read_bytes() == before


def test_rejects_unversioned_existing_database_instead_of_rewriting(
    sync_root: Path,
) -> None:
    sync_root.mkdir()
    db = sync_root / ".sukaseafood-sync.sqlite3"
    with closing(sqlite3.connect(db)) as connection:
        connection.execute("CREATE TABLE unrelated (value TEXT)")
        connection.commit()

    with pytest.raises(SyncIndexError, match="unversioned"):
        SyncIndex(sync_root)
    with closing(sqlite3.connect(db)) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall() == [("unrelated",)]


def test_receipt_token_and_urls_never_enter_schema_or_database_bytes(
    sync_root: Path,
) -> None:
    index = SyncIndex(sync_root)
    index.record_success(result())
    index.mark_receipt_submitted(CANDIDATE_ID, REVIEW_ID, 1, "ADD")
    with index.connect() as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    combined = b"".join(
        path.read_bytes()
        for path in sync_root.iterdir()
        if path.is_file()
    )

    assert RECEIPT_TOKEN.encode() not in combined
    assert b"original_url" not in combined
    assert b"source_url" not in combined
    assert b"receipt_token" not in combined


def test_two_independent_connections_have_busy_timeout_and_foreign_keys(
    sync_root: Path,
) -> None:
    index = SyncIndex(sync_root)
    with index.connect() as first, index.connect() as second:
        assert first is not second
        assert first.execute("PRAGMA busy_timeout").fetchone()[0] >= 5_000
        assert second.execute("PRAGMA busy_timeout").fetchone()[0] >= 5_000
        assert first.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert second.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_query_arguments_are_validated_and_not_interpolated(sync_root: Path) -> None:
    index = SyncIndex(sync_root)
    index.record_success(result())

    with pytest.raises(SyncIndexError):
        index.is_completed("' OR 1=1 --", REVIEW_ID, 1, "ADD")
    with pytest.raises(SyncIndexError):
        index.find_present_by_sha256("a' OR 1=1 --")
    assert index.is_completed(CANDIDATE_ID, REVIEW_ID, 1, "ADD")
