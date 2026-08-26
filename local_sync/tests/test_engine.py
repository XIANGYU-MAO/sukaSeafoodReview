from __future__ import annotations

from dataclasses import fields, replace
import hashlib
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import queue
import sys
import threading
from uuid import UUID

import imagehash
from PIL import Image
import pytest

from conftest import BATCH_ID, RECEIPT_TOKEN
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
