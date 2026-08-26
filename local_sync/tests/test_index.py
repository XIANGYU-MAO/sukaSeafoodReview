from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
import os
from pathlib import Path, PurePosixPath
import sqlite3
import stat
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
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        columns = [row[1] for row in connection.execute("PRAGMA table_info(synced_items)")]
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
    assert not any("token" in column or "url" in column for column in columns)


def test_connection_context_closes_owned_connection(sync_root: Path) -> None:
    index = SyncIndex(sync_root)
    with index.connect() as connection:
        assert connection.execute("SELECT 1").fetchone()[0] == 1

    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


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
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        objects = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall()
    assert [(kind, name, table) for kind, name, table, _sql in objects] == [
        ("index", "sqlite_autoindex_synced_items_1", "synced_items"),
        ("table", "synced_items", "synced_items"),
    ]


def test_records_and_checks_exact_four_part_completion_key(sync_root: Path) -> None:
    index = SyncIndex(sync_root)
    stored = index.record_success(result())

    assert stored.candidate_id == CANDIDATE_ID
    assert stored.review_id == REVIEW_ID
    assert stored.review_version == 1
    assert stored.action == "ADD"
    assert stored.sha256 == "a" * 64
    assert stored.perceptual_hash == "abcdef0123456789"
    assert index.is_completed(str(CANDIDATE_ID), REVIEW_ID, 1, "ADD")
    assert not index.is_completed(CANDIDATE_ID, REVIEW_ID, 2, "ADD")
    assert not index.is_completed(CANDIDATE_ID, REVIEW_ID, 1, "MOVE")


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
