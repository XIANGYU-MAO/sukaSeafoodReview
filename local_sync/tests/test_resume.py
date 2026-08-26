from __future__ import annotations

from pathlib import Path
import threading
from uuid import UUID

import pytest

from sukaseafood_sync.downloader import DownloadCancelled, DownloadResult
from sukaseafood_sync.engine import ProgressEvent, SyncCallbacks, SyncEngine
from sukaseafood_sync.index import SyncIndex

from test_engine import JPEG, PHASH, SHA256, Session, fake_download, local, manifest, row


def test_cancelled_batch_keeps_first_durable_item_and_rerun_skips_its_network(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_root.mkdir()
    first, second = row(101), row(102)
    cancel_event = threading.Event()
    first_run_calls: list[UUID] = []
    normal_download = fake_download(sync_root, first_run_calls)

    def cancel_second(session, item, destination, policy, progress, cancel):
        if item == second:
            first_run_calls.append(item.candidate_id)
            cancel_event.set()
            raise DownloadCancelled("download cancelled")
        return normal_download(session, item, destination, policy, progress, cancel)

    monkeypatch.setattr("sukaseafood_sync.engine.download_image", cancel_second)
    first_result = SyncEngine(session=Session()).run(
        manifest(first, second), sync_root, SyncCallbacks(), cancel_event
    )

    assert first_result.cancelled
    assert first_result.processed == 1
    assert [item.candidate_id for item in first_result.receipt_items] == [str(first.candidate_id)]
    index = SyncIndex(sync_root)
    assert index.get_completed(first.candidate_id, first.review_id, 2, "ADD") is not None
    assert index.get_completed(second.candidate_id, second.review_id, 2, "ADD") is None

    second_run_calls: list[UUID] = []
    monkeypatch.setattr(
        "sukaseafood_sync.engine.download_image",
        fake_download(sync_root, second_run_calls),
    )
    second_result = SyncEngine(session=Session()).run(
        manifest(first, second), sync_root, SyncCallbacks(), threading.Event()
    )

    assert not second_result.cancelled
    assert second_result.counts == {"succeeded": 1, "failed": 0, "skipped": 1}
    assert second_result.receipt_items[0].status == "SUCCEEDED"
    assert second_result.receipt_items[1].status == "SUCCEEDED"
    assert second_run_calls == [second.candidate_id]


def test_cancellation_before_first_item_makes_no_request_or_receipt(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_root.mkdir()
    calls: list[UUID] = []
    monkeypatch.setattr(
        "sukaseafood_sync.engine.download_image", fake_download(sync_root, calls)
    )
    cancel_event = threading.Event()
    cancel_event.set()
    events: list[ProgressEvent] = []

    result = SyncEngine(session=Session()).run(
        manifest(row(111)), sync_root, SyncCallbacks(events.append), cancel_event
    )

    assert result.cancelled
    assert result.processed == 0
    assert result.receipt_items == ()
    assert calls == []
    assert [event.phase for event in events] == ["CANCELLED"]


def test_cancellation_during_rate_wait_prevents_next_request(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_root.mkdir()
    first, second = row(121), row(122)
    calls: list[UUID] = []
    monkeypatch.setattr(
        "sukaseafood_sync.engine.download_image", fake_download(sync_root, calls)
    )
    cancel_event = threading.Event()
    events: list[ProgressEvent] = []

    def cancel_wait(delay: float, event: threading.Event) -> bool:
        assert delay == 1.0
        event.set()
        return True

    result = SyncEngine(
        session=Session(), monotonic=lambda: 0.0, wait=cancel_wait
    ).run(manifest(first, second), sync_root, SyncCallbacks(events.append), cancel_event)

    assert result.cancelled
    assert result.processed == 1
    assert calls == [first.candidate_id]
    assert [event.phase for event in events][-2:] == ["WAITING", "CANCELLED"]


def test_default_wait_polls_is_set_only_event_during_commons_delay(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_root.mkdir()
    first = row(123, source_url="https://commons.wikimedia.org/wiki/File:One")
    second = row(124, source_url="https://en.wikipedia.org/wiki/Two")
    calls: list[UUID] = []
    monkeypatch.setattr(
        "sukaseafood_sync.engine.download_image", fake_download(sync_root, calls)
    )

    class IsSetOnly:
        def __init__(self) -> None:
            self.cancelled = False

        def is_set(self) -> bool:
            return self.cancelled

        def set(self) -> None:
            self.cancelled = True

    event = IsSetOnly()
    sleeps: list[float] = []

    def cancel_on_first_poll(delay: float) -> None:
        sleeps.append(delay)
        event.set()

    monkeypatch.setattr("sukaseafood_sync.engine.time.sleep", cancel_on_first_poll)
    result = SyncEngine(session=Session(), monotonic=lambda: 0.0).run(
        manifest(first, second), sync_root, SyncCallbacks(), event
    )

    assert result.cancelled
    assert calls == [first.candidate_id]
    assert sleeps and max(sleeps) <= 0.1


def test_cancellation_after_download_preserves_staging_for_network_free_resume(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_root.mkdir()
    item = row(125)
    cancel_event = threading.Event()
    first_calls: list[UUID] = []

    def download_then_cancel(session, manifest_row, destination, policy, progress, cancel):
        first_calls.append(manifest_row.candidate_id)
        staging = Path(destination).with_name(Path(destination).name + ".part")
        staging.write_bytes(JPEG)
        cancel_event.set()
        return DownloadResult(
            staging, SHA256, PHASH, len(JPEG), "JPEG", ".jpg", 11, 7
        )

    monkeypatch.setattr(
        "sukaseafood_sync.engine.download_image", download_then_cancel
    )
    first = SyncEngine(session=Session()).run(
        manifest(item), sync_root, SyncCallbacks(), cancel_event
    )

    staging = local(sync_root, item.target_relative_path).with_name(
        item.target_relative_path.name + ".part"
    )
    assert first.cancelled
    assert first.counts == {"succeeded": 0, "failed": 0, "skipped": 0}
    assert first.receipt_items == ()
    assert staging.read_bytes() == JPEG
    assert SyncIndex(sync_root).get_completed(
        item.candidate_id, item.review_id, item.review_version, "ADD"
    ) is None

    def no_network(*args, **kwargs):
        raise AssertionError("recovery must happen before network")

    monkeypatch.setattr("sukaseafood_sync.engine.download_image", no_network)
    second = SyncEngine(session=Session()).run(
        manifest(item), sync_root, SyncCallbacks(), threading.Event()
    )

    assert second.counts == {"succeeded": 1, "failed": 0, "skipped": 0}
    assert second.receipt_items[0].status == "SUCCEEDED"
    assert not staging.exists()


def test_add_recovery_happens_before_wait_or_network(
    sync_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_root.mkdir()
    first, second = row(131), row(132)
    calls: list[UUID] = []
    monkeypatch.setattr(
        "sukaseafood_sync.engine.download_image", fake_download(sync_root, calls)
    )
    SyncEngine(session=Session(), monotonic=lambda: 0.0, wait=lambda delay, event: False).run(
        manifest(first), sync_root, SyncCallbacks(), threading.Event()
    )

    waits: list[float] = []
    result = SyncEngine(
        session=Session(),
        monotonic=lambda: 0.0,
        wait=lambda delay, event: waits.append(delay) or False,
    ).run(manifest(first, second), sync_root, SyncCallbacks(), threading.Event())

    assert result.counts == {"succeeded": 1, "failed": 0, "skipped": 1}
    assert calls == [first.candidate_id, second.candidate_id]
    assert waits == []
