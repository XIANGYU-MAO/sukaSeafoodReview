from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta, timezone
import json
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.main import create_app
from app.models import AuditEvent, Candidate, ExportBatch, ExportItem
from tests.export_support import (
    csv_rows,
    load_models,
    mao_headers,
    mutate,
    seed_export_database,
    success_receipt,
)


def setup_batch(settings, *, count=1):
    seed = asyncio.run(
        seed_export_database(settings, decisions=tuple(["APPROVED"] * count))
    )
    client = TestClient(create_app(settings))
    client.__enter__()
    created = client.post(
        "/v1/admin/exports", json={}, headers=mao_headers(seed, csrf=True)
    )
    assert created.status_code == 201
    batch_id = created.json()["id"]
    csv_response = client.get(
        f"/v1/admin/exports/{batch_id}.csv", headers=mao_headers(seed)
    )
    assert csv_response.status_code == 200
    rows = csv_rows(csv_response)
    return seed, client, batch_id, rows[0]["receipt_token"], rows


def close(client):
    client.__exit__(None, None, None)


def post_receipt(client, batch_id, token, items):
    return client.post(
        f"/v1/sync/batches/{batch_id}/receipt",
        json={"items": items},
        headers={"Authorization": f"Batch {token}"},
    )


def test_success_failed_partial_completion_retry_and_conflict(settings):
    seed, client, batch_id, token, rows = setup_batch(settings, count=2)
    try:
        failed = {
            "candidate_id": rows[1]["candidate_id"],
            "review_id": rows[1]["review_id"],
            "review_version": int(rows[1]["review_version"]),
            "status": "FAILED",
            "error": "temporary timeout\r\nretry",
        }
        partial = post_receipt(
            client, batch_id, token, [success_receipt(rows[0]), failed]
        )
        retry_same = post_receipt(
            client, batch_id, token, [success_receipt(rows[0])]
        )
        conflict = post_receipt(
            client,
            batch_id,
            token,
            [success_receipt(rows[0], sha256="b" * 64)],
        )
        completed = post_receipt(
            client,
            batch_id,
            token,
            [success_receipt(rows[1], sha256="c" * 64)],
        )
    finally:
        close(client)

    assert partial.status_code == retry_same.status_code == completed.status_code == 200
    assert partial.json()["status"] == "pending"
    assert partial.json()["accepted_candidate_ids"] == [rows[0]["candidate_id"]]
    assert partial.json()["pending_candidate_ids"] == [rows[1]["candidate_id"]]
    assert retry_same.json()["accepted_candidate_ids"] == [rows[0]["candidate_id"]]
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "RECEIPT_SUCCESS_CONFLICT"
    assert completed.json()["status"] == "completed"
    items = asyncio.run(load_models(settings, ExportItem))
    by_candidate = {str(item.candidate_id): item for item in items}
    assert by_candidate[rows[0]["candidate_id"]].status == "succeeded"
    assert by_candidate[rows[1]["candidate_id"]].status == "succeeded"
    assert by_candidate[rows[1]["candidate_id"]].error is None
    assert asyncio.run(load_models(settings, ExportBatch))[0].completed_at is not None
    audits = asyncio.run(load_models(settings, AuditEvent))
    assert [event.action for event in audits].count("EXPORT_RECEIPT_APPLY") == 2
    assert token not in " ".join(str(event.__dict__) for event in audits)


def test_token_receipt_ignores_browser_identity_and_attributes_audit_to_batch_creator(settings):
    seed, client, batch_id, token, rows = setup_batch(settings)
    try:
        response = client.post(
            f"/v1/sync/batches/{batch_id}/receipt",
            json={"items": [success_receipt(rows[0])]},
            headers={
                "Authorization": f"Batch {token}",
                "Cookie": f"review_session={seed.hassan_token}",
            },
        )
    finally:
        close(client)

    assert response.status_code == 200
    receipt_audits = [
        event
        for event in asyncio.run(load_models(settings, AuditEvent))
        if event.action == "EXPORT_RECEIPT_APPLY"
    ]
    assert len(receipt_audits) == 1
    assert receipt_audits[0].actor_id == seed.mao_id
    assert receipt_audits[0].actor_id != seed.hassan_id


@pytest.mark.parametrize("route_kind", ["sync", "manual"])
@pytest.mark.parametrize(
    "sensitive_kind",
    ["token", "token_case", "token_base64", "secret", "secret_case", "secret_base64"],
)
def test_failed_receipt_rejects_sensitive_error_without_api_db_audit_or_log_leakage(
    settings, caplog, route_kind, sensitive_kind
):
    seed, client, batch_id, token, rows = setup_batch(settings)
    secret = settings.RECEIPT_SECRET
    assert secret is not None
    sensitive_values = {
        "token": token,
        "token_case": token.swapcase(),
        "token_base64": base64.urlsafe_b64encode(token.encode()).decode(),
        "secret": secret,
        "secret_case": secret.swapcase(),
        "secret_base64": base64.urlsafe_b64encode(secret.encode()).decode(),
    }
    offending = sensitive_values[sensitive_kind]
    failed = {
        "candidate_id": rows[0]["candidate_id"],
        "review_id": rows[0]["review_id"],
        "review_version": int(rows[0]["review_version"]),
        "status": "FAILED",
        "error": f"download diagnostic: Authorization: Batch {offending}",
    }
    try:
        if route_kind == "sync":
            response = post_receipt(client, batch_id, token, [failed])
        else:
            response = client.post(
                f"/v1/admin/exports/{batch_id}/receipt-file",
                json={"batch_id": batch_id, "items": [failed]},
                headers=mao_headers(seed, csrf=True),
            )
    finally:
        close(client)

    stored = asyncio.run(load_models(settings, ExportItem))[0]
    audits = asyncio.run(load_models(settings, AuditEvent))
    persisted = " ".join(
        [str(stored.__dict__), *(str(event.__dict__) for event in audits)]
    )
    assert stored.status == "pending"
    assert stored.error is None
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "RECEIPT_ERROR_SENSITIVE"
    for value in (token, secret, offending):
        assert value not in response.text
        assert value not in persisted
        assert value not in caplog.text


@pytest.mark.parametrize(
    "mutator",
    [
        lambda item, rows: {**item, "candidate_id": str(uuid4())},
        lambda item, rows: {**item, "review_id": str(uuid4())},
        lambda item, rows: {**item, "review_version": item["review_version"] + 1},
        lambda item, rows: {**item, "sha256": "not-a-hash"},
        lambda item, rows: {**item, "relative_path": "../../escape.jpg"},
        lambda item, rows: {**item, "relative_path": "images/SF001/other.jpg"},
    ],
)
def test_invalid_receipt_item_rejects_whole_payload_atomically(settings, mutator):
    seed, client, batch_id, token, rows = setup_batch(settings, count=2)
    try:
        valid = success_receipt(rows[0])
        invalid = mutator(success_receipt(rows[1], sha256="b" * 64), rows)
        response = post_receipt(client, batch_id, token, [valid, invalid])
    finally:
        close(client)
    assert response.status_code in {409, 422}
    assert {item.status for item in asyncio.run(load_models(settings, ExportItem))} == {"pending"}


def test_duplicate_entries_oversized_error_and_body_are_rejected_atomically(settings):
    seed, client, batch_id, token, rows = setup_batch(settings)
    item = success_receipt(rows[0])
    try:
        duplicate = post_receipt(client, batch_id, token, [item, item])
        too_large_error = dict(item, status="FAILED", sha256=None, relative_path=None, error="x" * 2001)
        error_response = post_receipt(client, batch_id, token, [too_large_error])
        body_response = client.post(
            f"/v1/sync/batches/{batch_id}/receipt",
            content=b'{"items":[]}' + b" " * (130 * 1024),
            headers={
                "Authorization": f"Batch {token}",
                "Content-Type": "application/json",
            },
        )
    finally:
        close(client)
    assert duplicate.status_code == error_response.status_code == 422
    assert body_response.status_code == 413
    assert asyncio.run(load_models(settings, ExportItem))[0].status == "pending"


def test_wrong_swapped_and_expired_batch_tokens_are_secret_free(settings):
    seed, client, batch_id, token, rows = setup_batch(settings, count=2)
    try:
        wrong = post_receipt(client, batch_id, "wrong-token", [success_receipt(rows[0])])
        second = client.post(
            "/v1/admin/exports",
            json={"species_code": "SF002"},
            headers=mao_headers(seed, csrf=True),
        )
        assert second.status_code == 200 and second.json()["code"] == "NO_WORK"
        swapped = post_receipt(client, str(uuid4()), token, [success_receipt(rows[0])])
        asyncio.run(
            mutate(
                settings,
                ExportBatch,
                UUID(batch_id),
                expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            )
        )
        expired = post_receipt(client, batch_id, token, [success_receipt(rows[0])])
    finally:
        close(client)
    assert wrong.status_code == swapped.status_code == expired.status_code == 401
    for response in (wrong, swapped, expired):
        assert token not in response.text
        assert settings.RECEIPT_SECRET not in response.text
    assert asyncio.run(load_models(settings, ExportItem))[0].status == "pending"


def test_decoded_extension_becomes_canonical_and_unchanged_export_is_no_work(settings):
    seed, client, batch_id, token, rows = setup_batch(settings)
    item = success_receipt(rows[0])
    item["relative_path"] = item["relative_path"].rsplit(".", 1)[0] + ".png"
    try:
        response = post_receipt(client, batch_id, token, [item])
        unchanged = client.post(
            "/v1/admin/exports", json={}, headers=mao_headers(seed, csrf=True)
        )
    finally:
        close(client)
    assert response.status_code == 200
    stored = asyncio.run(load_models(settings, ExportItem))[0]
    assert stored.local_relative_path == item["relative_path"]
    assert unchanged.status_code == 200
    assert unchanged.json() == {"code": "NO_WORK", "created": False, "batch": None}


def test_same_content_species_move_preserves_decoded_suffix_and_prior_csv_snapshot(settings):
    seed, client, batch_id, token, rows = setup_batch(settings)
    original_csv = client.get(
        f"/v1/admin/exports/{batch_id}.csv", headers=mao_headers(seed)
    ).content
    decoded_path = rows[0]["target_relative_path"].rsplit(".", 1)[0] + ".png"
    item = success_receipt(rows[0])
    item["relative_path"] = decoded_path
    try:
        applied = post_receipt(client, batch_id, token, [item])
        assert applied.status_code == 200
        asyncio.run(
            mutate(
                settings,
                Candidate,
                seed.candidate_ids[0],
                species_id=seed.species_ids[1],
                version=2,
            )
        )
        moved = client.post(
            "/v1/admin/exports", json={}, headers=mao_headers(seed, csrf=True)
        )
        moved_csv = client.get(
            f"/v1/admin/exports/{moved.json()['id']}.csv", headers=mao_headers(seed)
        )
        prior_csv = client.get(
            f"/v1/admin/exports/{batch_id}.csv", headers=mao_headers(seed)
        )
    finally:
        close(client)

    assert moved.status_code == 201
    moved_rows = csv_rows(moved_csv)
    assert moved_rows[0]["action"] == "MOVE"
    assert moved_rows[0]["previous_relative_path"] == decoded_path
    assert moved_rows[0]["target_relative_path"] == (
        f"images/SF002/{seed.candidate_ids[0]}.png"
    )
    assert prior_csv.content == original_csv


def test_manual_receipt_file_requires_mao_csrf_bounds_batch_match_and_reuses_semantics(settings):
    seed, client, batch_id, token, rows = setup_batch(settings)
    payload = {"batch_id": batch_id, "items": [success_receipt(rows[0])]}
    try:
        anonymous = client.post(
            f"/v1/admin/exports/{batch_id}/receipt-file",
            content=b"x" * (1024 * 1024),
            headers={"Content-Type": "multipart/form-data; boundary=never-parse"},
        )
        no_csrf = client.post(
            f"/v1/admin/exports/{batch_id}/receipt-file",
            json=payload,
            headers=mao_headers(seed),
        )
        multipart = client.post(
            f"/v1/admin/exports/{batch_id}/receipt-file",
            files={"file": ("download_receipt.json", json.dumps(payload), "application/json")},
            headers=mao_headers(seed, csrf=True),
        )
        mismatch = client.post(
            f"/v1/admin/exports/{batch_id}/receipt-file",
            json={**payload, "batch_id": str(uuid4())},
            headers=mao_headers(seed, csrf=True),
        )
        oversized = client.post(
            f"/v1/admin/exports/{batch_id}/receipt-file",
            content=json.dumps(payload).encode() + b" " * (130 * 1024),
            headers={**mao_headers(seed, csrf=True), "Content-Type": "application/json"},
        )
        applied = client.post(
            f"/v1/admin/exports/{batch_id}/receipt-file",
            json=payload,
            headers=mao_headers(seed, csrf=True),
        )
    finally:
        close(client)
    assert anonymous.status_code == 401
    assert no_csrf.status_code == 403
    assert multipart.status_code == 415
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"]["code"] == "RECEIPT_BATCH_MISMATCH"
    assert oversized.status_code == 413
    assert applied.status_code == 200
    assert applied.json()["status"] == "completed"
    assert token not in applied.text

    with TestClient(create_app(settings)) as docs_client:
        operation = docs_client.get("/openapi.json").json()["paths"][
            "/v1/admin/exports/{batch_id}/receipt-file"
        ]["post"]
    content_types = set(operation["requestBody"]["content"])
    assert content_types == {"application/json"}


def test_receipt_rolls_back_item_when_audit_insert_fails(settings):
    seed, client, batch_id, token, rows = setup_batch(settings)

    def fail_receipt_audit(_mapper, _connection, target):
        if target.action == "EXPORT_RECEIPT_APPLY":
            raise RuntimeError("forced receipt audit failure")

    event.listen(AuditEvent, "before_insert", fail_receipt_audit)
    try:
        with pytest.raises(RuntimeError, match="forced receipt audit failure"):
            post_receipt(client, batch_id, token, [success_receipt(rows[0])])
    finally:
        event.remove(AuditEvent, "before_insert", fail_receipt_audit)
        close(client)
    item = asyncio.run(load_models(settings, ExportItem))[0]
    batch = asyncio.run(load_models(settings, ExportBatch))[0]
    assert item.status == "pending"
    assert item.sha256 is None
    assert batch.status == "pending"
