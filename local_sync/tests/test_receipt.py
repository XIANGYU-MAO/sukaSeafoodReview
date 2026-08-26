from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID

import pytest
import requests

from conftest import BATCH_ID, CANDIDATE_ID, RECEIPT_TOKEN, REVIEW_ID
from sukaseafood_sync.engine import BatchResult, ReceiptItem
from sukaseafood_sync.index import SyncIndex, SyncResult
from sukaseafood_sync.manifest import ExportManifest, ManifestRow
from sukaseafood_sync.receipt import (
    ReceiptError,
    build_receipt,
    save_receipt_file,
    submit_receipt,
)


SHA256 = "a" * 64
SECOND_CANDIDATE = UUID("44444444-4444-4444-8444-444444444444")
SECOND_REVIEW = UUID("55555555-5555-4555-8555-555555555555")


def row(
    candidate_id: UUID = CANDIDATE_ID,
    review_id: UUID = REVIEW_ID,
    *,
    action: str = "ADD",
    review_version: int = 1,
) -> ManifestRow:
    return ManifestRow(
        batch_id=BATCH_ID,
        action=action,  # type: ignore[arg-type]
        candidate_id=candidate_id,
        review_id=review_id,
        review_version=review_version,
        species_code="SF006",
        target_relative_path=PurePosixPath(f"images/SF006/{candidate_id}.jpg"),
        previous_relative_path=None,
        preview_url="https://images.example.test/preview.jpg",
        original_url="https://images.example.test/original.jpg",
        source_url="https://catalog.example.test/record/1",
        creator="Researcher",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Researcher / Catalog",
    )


def manifest(*rows: ManifestRow, token: str = RECEIPT_TOKEN) -> ExportManifest:
    selected = rows or (row(),)
    return ExportManifest(tuple(selected), BATCH_ID, token)


def item(
    manifest_row: ManifestRow | None = None,
    *,
    status: str = "SUCCEEDED",
    error: str | None = None,
) -> ReceiptItem:
    selected = manifest_row or row()
    succeeded = status == "SUCCEEDED"
    return ReceiptItem(
        candidate_id=str(selected.candidate_id),
        review_id=str(selected.review_id),
        review_version=selected.review_version,
        status=status,  # type: ignore[arg-type]
        sha256=SHA256 if succeeded else None,
        relative_path=selected.target_relative_path.as_posix() if succeeded else None,
        error=None if succeeded else (error or "NETWORK_ERROR"),
    )


def batch(
    *items: ReceiptItem,
    total: int | None = None,
    cancelled: bool = False,
    batch_id: str = str(BATCH_ID),
    counts: dict[str, int] | None = None,
) -> BatchResult:
    selected = items or (item(),)
    processed = len(selected)
    failed = sum(entry.status == "FAILED" for entry in selected)
    return BatchResult(
        batch_id=batch_id,
        counts=counts or {"succeeded": processed - failed, "failed": failed, "skipped": 0},
        receipt_items=tuple(selected),
        cancelled=cancelled,
        processed=processed,
        total=processed if total is None else total,
        operation_log_path=Path("hidden-operation-log.jsonl"),
    )


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: object | None = None,
        *,
        headers: dict[str, str] | None = None,
        content: bytes | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.content = (
            content
            if content is not None
            else json.dumps(payload or {}).encode("utf-8")
        )
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def iter_content(self, chunk_size: int) -> object:
        for offset in range(0, len(self.content), chunk_size):
            yield self.content[offset : offset + chunk_size]


class StreamingOnlyResponse(FakeResponse):
    def __init__(self, payload: object) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        super().__init__(200, content=b"")
        self._encoded = encoded

    @property
    def content(self) -> bytes:
        raise AssertionError("unbounded content access")

    @content.setter
    def content(self, _value: bytes) -> None:
        return None

    def iter_content(self, chunk_size: int) -> object:
        for offset in range(0, len(self._encoded), chunk_size):
            yield self._encoded[offset : offset + chunk_size]


class FakeSession(requests.Session):
    def __init__(self, outcomes: list[FakeResponse | Exception]) -> None:
        super().__init__()
        self.outcomes = list(outcomes)
        self.sent: list[tuple[requests.PreparedRequest, dict[str, Any]]] = []
        self.close_calls = 0

    def send(self, request: requests.PreparedRequest, **kwargs: Any) -> FakeResponse:
        self.sent.append((request, kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def close(self) -> None:
        self.close_calls += 1
        super().close()


def success_payload(
    *,
    accepted: list[str] | None = None,
    pending: list[str] | None = None,
    batch_id: str = str(BATCH_ID),
    status: str = "completed",
) -> dict[str, object]:
    return {
        "batch_id": batch_id,
        "status": status,
        "accepted_candidate_ids": accepted or [],
        "pending_candidate_ids": pending or [],
    }


def test_online_and_offline_receipts_have_exact_secret_free_schemas() -> None:
    source = manifest()
    receipt = build_receipt(source, batch())

    online = receipt.to_dict()
    offline = receipt.to_file_dict()
    assert set(online) == {"items"}
    assert set(offline) == {"batch_id", "items"}
    assert offline["batch_id"] == str(BATCH_ID)
    expected_item_keys = {
        "candidate_id",
        "review_id",
        "review_version",
        "status",
        "sha256",
        "relative_path",
        "error",
    }
    assert set(online["items"][0]) == expected_item_keys
    exposed = repr(receipt) + json.dumps(online) + json.dumps(offline)
    for secret in (
        RECEIPT_TOKEN,
        source.rows[0].original_url,
        source.rows[0].source_url,
        "action",
        "perceptual_hash",
    ):
        assert secret not in exposed


def test_build_accepts_ordered_nonempty_partial_cancellation() -> None:
    first = row()
    second = row(SECOND_CANDIDATE, SECOND_REVIEW)
    receipt = build_receipt(
        manifest(first, second),
        batch(item(first), total=2, cancelled=True),
    )
    assert [entry["candidate_id"] for entry in receipt.to_dict()["items"]] == [
        str(first.candidate_id)
    ]


@pytest.mark.parametrize(
    "mutate,code",
    [
        (lambda source, result: (source, replace(result, batch_id=str(SECOND_CANDIDATE))), "BATCH_MISMATCH"),
        (lambda source, result: (source, replace(result, processed=0)), "COUNT_MISMATCH"),
        (lambda source, result: (source, replace(result, total=2)), "COUNT_MISMATCH"),
        (lambda source, result: (source, replace(result, receipt_items=())), "EMPTY_RECEIPT"),
        (
            lambda source, result: (
                manifest(row(), row()),
                replace(result, receipt_items=(result.receipt_items[0], result.receipt_items[0]), processed=2, total=2, counts={"succeeded": 2, "failed": 0, "skipped": 0}),
            ),
            "DUPLICATE_ITEM",
        ),
        (
            lambda source, result: (
                source,
                replace(result, receipt_items=(replace(result.receipt_items[0], review_id=str(SECOND_REVIEW)),)),
            ),
            "ITEM_MISMATCH",
        ),
    ],
)
def test_build_rejects_identity_count_duplicate_and_triple_mismatches(mutate, code) -> None:
    source, result = mutate(manifest(), batch())
    with pytest.raises(ReceiptError) as captured:
        build_receipt(source, result)
    assert captured.value.code == code
    assert RECEIPT_TOKEN not in repr(captured.value)


@pytest.mark.parametrize(
    "bad_item",
    [
        replace(item(), candidate_id="not-a-uuid"),
        replace(item(), review_version=0),
        replace(item(), status="DONE"),
        replace(item(), sha256="A" * 64),
        replace(item(), relative_path="https://evil.test/fish.jpg"),
        replace(item(), relative_path="/absolute/fish.jpg"),
        replace(item(), relative_path="images/fish\n.jpg"),
        replace(item(), status="FAILED", sha256=None, relative_path=None, error="bad detail with spaces"),
        replace(item(status="FAILED"), sha256=SHA256),
        replace(item(status="FAILED"), error=None),
    ],
)
def test_build_rejects_malformed_server_values(bad_item: ReceiptItem) -> None:
    with pytest.raises(ReceiptError) as captured:
        build_receipt(manifest(), batch(bad_item))
    assert captured.value.code in {"INVALID_ITEM", "ITEM_MISMATCH"}


@pytest.mark.parametrize(
    "api_base",
    [
        "http://api.example.test/sukaseafood/api/v1",
        "https://user:pass@api.example.test/sukaseafood/api/v1",
        "https://api.example.test/sukaseafood/api/v1?token=x",
        "https://api.example.test/sukaseafood/api/v1#fragment",
        "https://api.example.test/wrong",
        "https://api.example.test/sukaseafood/api/v1/extra",
        "http://localhost./sukaseafood/api/v1",
    ],
)
def test_submit_rejects_noncanonical_api_base_before_transport(api_base: str) -> None:
    session = FakeSession([])
    result = submit_receipt(build_receipt(manifest(), batch()), api_base, RECEIPT_TOKEN, 5, session=session)
    assert result.code == "INVALID_API_BASE"
    assert result.attempts == 0
    assert session.sent == []


@pytest.mark.parametrize(
    "api_base",
    [
        "https://api.example.test/sukaseafood/api/v1",
        "https://api.example.test/sukaseafood/api/v1/",
        "http://localhost/sukaseafood/api/v1",
        "http://127.0.0.1:8000/sukaseafood/api/v1",
        "http://[::1]:8000/sukaseafood/api/v1",
    ],
)
def test_submit_posts_exact_url_and_bare_headers(
    api_base: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.test:8080")
    monkeypatch.delenv("NO_PROXY", raising=False)
    response = FakeResponse(200, success_payload())
    session = FakeSession([response])
    session.auth = ("origin-user", "origin-password")
    session.cookies.set("origin", "cookie")
    session.headers["X-Origin-Session"] = "must-not-cross"
    session.trust_env = False

    result = submit_receipt(build_receipt(manifest(), batch()), api_base, RECEIPT_TOKEN, 5, session=session)

    assert result.submitted
    request, kwargs = session.sent[0]
    assert request.url == f"{api_base.rstrip('/')}/sync/batches/{BATCH_ID}/receipt"
    assert request.method == "POST"
    assert request.headers["Authorization"] == f"Batch {RECEIPT_TOKEN}"
    assert request.headers["Accept"] == "application/json"
    assert request.headers["Content-Type"] == "application/json"
    assert "Cookie" not in request.headers
    assert "X-Origin-Session" not in request.headers
    assert not request.url.endswith(RECEIPT_TOKEN)
    assert kwargs["allow_redirects"] is False
    assert kwargs["timeout"] == 5
    assert kwargs["proxies"].get("https") == "http://proxy.example.test:8080"
    assert "verify" in kwargs
    assert session.trust_env is True
    assert session.close_calls == 0
    assert response.closed


def test_redirect_is_closed_and_never_retried() -> None:
    response = FakeResponse(302, headers={"Location": "https://evil.test/steal"})
    session = FakeSession([response])
    result = submit_receipt(build_receipt(manifest(), batch()), "https://api.example.test/sukaseafood/api/v1", RECEIPT_TOKEN, 5, session=session)
    assert result.code == "REDIRECT_REJECTED"
    assert result.attempts == 1
    assert response.closed


def test_connection_timeout_429_and_retryable_5xx_stop_after_three_attempts() -> None:
    first = requests.ConnectionError("DNS details secret")
    second = FakeResponse(429, headers={"Retry-After": "7"})
    third = FakeResponse(503)
    session = FakeSession([first, second, third])
    sleeps: list[float] = []
    result = submit_receipt(
        build_receipt(manifest(), batch()),
        "https://api.example.test/sukaseafood/api/v1",
        RECEIPT_TOKEN,
        5,
        session=session,
        sleep=sleeps.append,
    )
    assert result.code == "RETRY_EXHAUSTED"
    assert result.retryable
    assert result.attempts == 3
    assert sleeps == [1.0, 7.0]
    assert second.closed and third.closed
    assert "DNS details secret" not in repr(result)


def test_timeout_uses_fallback_delay_then_succeeds() -> None:
    session = FakeSession([
        requests.Timeout("timeout details secret"),
        FakeResponse(200, success_payload()),
    ])
    sleeps: list[float] = []
    result = submit_receipt(
        build_receipt(manifest(), batch()),
        "https://api.example.test/sukaseafood/api/v1",
        RECEIPT_TOKEN,
        5,
        session=session,
        sleep=sleeps.append,
    )
    assert result.submitted
    assert result.attempts == 2
    assert sleeps == [1.0]
    assert "timeout details secret" not in repr(result)


def test_retry_after_http_date_is_capped_at_sixty_seconds() -> None:
    now = datetime(2026, 8, 26, tzinfo=timezone.utc)
    retry_at = format_datetime(now + timedelta(minutes=5), usegmt=True)
    session = FakeSession([
        FakeResponse(500, headers={"Retry-After": retry_at}),
        FakeResponse(200, success_payload()),
    ])
    sleeps: list[float] = []
    result = submit_receipt(
        build_receipt(manifest(), batch()),
        "https://api.example.test/sukaseafood/api/v1",
        RECEIPT_TOKEN,
        5,
        session=session,
        sleep=sleeps.append,
        now=lambda: now,
    )
    assert result.submitted
    assert sleeps == [60.0]


@pytest.mark.parametrize(
    "status,code",
    [(400, "VALIDATION_FAILED"), (401, "AUTHENTICATION_FAILED"), (403, "AUTHENTICATION_FAILED"), (404, "VALIDATION_FAILED"), (409, "CONFLICT"), (422, "VALIDATION_FAILED")],
)
def test_nonretryable_statuses_return_stable_results(status: int, code: str) -> None:
    session = FakeSession([FakeResponse(status)])
    result = submit_receipt(build_receipt(manifest(), batch()), "https://api.example.test/sukaseafood/api/v1", RECEIPT_TOKEN, 5, session=session)
    assert result.code == code
    assert result.attempts == 1
    assert result.manual_action is (status == 409)
    assert len(session.sent) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"batch_id": str(BATCH_ID)},
        success_payload(batch_id=str(SECOND_CANDIDATE)),
        success_payload(status="expired"),
        success_payload(accepted=["not-a-uuid"]),
        success_payload(accepted=[str(CANDIDATE_ID), str(CANDIDATE_ID)]),
        success_payload(accepted=[str(CANDIDATE_ID)], pending=[str(CANDIDATE_ID)]),
        success_payload(accepted=[str(SECOND_CANDIDATE)]),
        success_payload(pending=[str(SECOND_CANDIDATE)]),
    ],
)
def test_malformed_success_response_fails_without_exposing_body(payload: object) -> None:
    session = FakeSession([FakeResponse(200, payload)])
    result = submit_receipt(build_receipt(manifest(), batch()), "https://api.example.test/sukaseafood/api/v1", RECEIPT_TOKEN, 5, session=session)
    assert result.code == "MALFORMED_RESPONSE"
    assert not result.submitted
    assert json.dumps(payload) not in repr(result)


def test_oversized_or_non_json_success_response_fails_safely() -> None:
    for content in (b"x" * (1024 * 1024 + 1), b"not-json-secret"):
        session = FakeSession([FakeResponse(200, content=content)])
        result = submit_receipt(build_receipt(manifest(), batch()), "https://api.example.test/sukaseafood/api/v1", RECEIPT_TOKEN, 5, session=session)
        assert result.code == "MALFORMED_RESPONSE"
        assert content[:20].decode("ascii") not in repr(result)


def test_success_response_is_streamed_with_a_bounded_read() -> None:
    response = StreamingOnlyResponse(success_payload())
    session = FakeSession([response])
    result = submit_receipt(build_receipt(manifest(), batch()), "https://api.example.test/sukaseafood/api/v1", RECEIPT_TOKEN, 5, session=session)
    assert result.submitted
    assert session.sent[0][1]["stream"] is True
    assert response.closed


def seed_index(index: SyncIndex, manifest_row: ManifestRow) -> None:
    index.record_success(
        SyncResult(
            candidate_id=manifest_row.candidate_id,
            review_id=manifest_row.review_id,
            review_version=manifest_row.review_version,
            action=manifest_row.action,
            batch_id=manifest_row.batch_id,
            relative_path=manifest_row.target_relative_path,
            sha256=SHA256,
            perceptual_hash="b" * 16,
        )
    )


def test_marks_only_accepted_succeeded_items_and_repeated_submit_is_idempotent(tmp_path: Path) -> None:
    first = row()
    second = row(SECOND_CANDIDATE, SECOND_REVIEW)
    source = manifest(first, second)
    receipt = build_receipt(source, batch(item(first), item(second, status="FAILED")))
    root = tmp_path / "sync-root"
    root.mkdir()
    index = SyncIndex(root)
    seed_index(index, first)
    seed_index(index, second)
    session = FakeSession([
        FakeResponse(200, success_payload(accepted=[str(first.candidate_id)], pending=[str(second.candidate_id)])),
        FakeResponse(200, success_payload(accepted=[str(first.candidate_id)], pending=[str(second.candidate_id)])),
    ])

    first_submit = submit_receipt(receipt, "https://api.example.test/sukaseafood/api/v1", RECEIPT_TOKEN, 5, session=session, index=index)
    marked_at = index.get_completed(first.candidate_id, first.review_id, 1, "ADD").receipt_submitted_at
    second_submit = submit_receipt(receipt, "https://api.example.test/sukaseafood/api/v1", RECEIPT_TOKEN, 5, session=session, index=index)

    assert first_submit.submitted and second_submit.submitted
    assert marked_at is not None
    assert index.get_completed(first.candidate_id, first.review_id, 1, "ADD").receipt_submitted_at == marked_at
    assert index.get_completed(second.candidate_id, second.review_id, 1, "ADD").receipt_submitted_at is None


def test_server_success_with_index_failure_retains_submitted_state_and_safe_retry(tmp_path: Path) -> None:
    receipt = build_receipt(manifest(), batch())
    root = tmp_path / "sync-root"
    root.mkdir()
    index = SyncIndex(root)
    session = FakeSession([FakeResponse(200, success_payload(accepted=[str(CANDIDATE_ID)]))])

    result = submit_receipt(receipt, "https://api.example.test/sukaseafood/api/v1", RECEIPT_TOKEN, 5, session=session, index=index)

    assert result.submitted
    assert result.code == "INDEX_UPDATE_FAILED"
    assert result.index_update_failed
    assert result.manual_action
    assert receipt.to_dict()["items"][0]["candidate_id"] == str(CANDIDATE_ID)


def test_internal_session_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession([FakeResponse(200, success_payload())])
    monkeypatch.setattr("sukaseafood_sync.receipt.requests.Session", lambda: session)
    result = submit_receipt(build_receipt(manifest(), batch()), "https://api.example.test/sukaseafood/api/v1", RECEIPT_TOKEN, 5)
    assert result.submitted
    assert session.close_calls == 1


def test_atomic_offline_file_round_trip_and_replacement(tmp_path: Path) -> None:
    receipt = build_receipt(manifest(), batch())
    expected = receipt.to_file_dict()
    saved = save_receipt_file(receipt, tmp_path)
    assert saved == tmp_path / f"download_receipt-{BATCH_ID}.json"
    assert json.loads(saved.read_text(encoding="utf-8")) == expected
    saved.write_text("old", encoding="utf-8")
    assert save_receipt_file(receipt, saved) == saved
    assert json.loads(saved.read_text(encoding="utf-8")) == expected
    assert list(tmp_path.iterdir()) == [saved]


def test_online_failure_leaves_receipt_available_for_atomic_offline_save(tmp_path: Path) -> None:
    receipt = build_receipt(manifest(), batch())
    session = FakeSession([FakeResponse(409)])
    result = submit_receipt(receipt, "https://api.example.test/sukaseafood/api/v1", RECEIPT_TOKEN, 5, session=session)
    saved = save_receipt_file(receipt, tmp_path)
    assert result.manual_action
    assert json.loads(saved.read_text(encoding="utf-8")) == receipt.to_file_dict()


def test_atomic_save_cleans_owned_temp_after_replace_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    receipt = build_receipt(manifest(), batch())
    secret = f"{RECEIPT_TOKEN} https://secret.example.test ADD perceptual_hash"
    monkeypatch.setattr("sukaseafood_sync.receipt.os.replace", lambda *_: (_ for _ in ()).throw(OSError(secret)))
    with pytest.raises(ReceiptError) as captured:
        save_receipt_file(receipt, tmp_path)
    assert captured.value.code == "FILE_WRITE_FAILED"
    assert list(tmp_path.iterdir()) == []
    graph: list[str] = []
    current: BaseException | None = captured.value
    while current is not None:
        graph.append(repr(current))
        current = current.__cause__ or current.__context__
    assert secret not in " ".join(graph)


def test_atomic_save_rejects_symlink_target_and_parent(tmp_path: Path) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks unavailable")
    receipt = build_receipt(manifest(), batch())
    outside = tmp_path / "outside"
    outside.mkdir()
    parent_link = tmp_path / "parent-link"
    target_link = tmp_path / "target.json"
    try:
        parent_link.symlink_to(outside, target_is_directory=True)
        target_link.symlink_to(outside / "missing.json")
    except OSError:
        pytest.skip("symlink creation unavailable")

    for unsafe in (parent_link, target_link):
        with pytest.raises(ReceiptError) as captured:
            save_receipt_file(receipt, unsafe)
        assert captured.value.code == "UNSAFE_FILE_PATH"


@pytest.mark.parametrize("path", ["receipt.txt", "bad\n.json", "nested/missing/receipt.json"])
def test_atomic_save_rejects_unsafe_explicit_paths(tmp_path: Path, path: str) -> None:
    with pytest.raises(ReceiptError) as captured:
        save_receipt_file(build_receipt(manifest(), batch()), tmp_path / path)
    assert captured.value.code == "UNSAFE_FILE_PATH"
