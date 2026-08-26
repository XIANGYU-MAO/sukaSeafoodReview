from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.main import create_app
from app.models import AuditEvent, Candidate, Decision, ExportBatch, ExportItem, Review, Species
from tests.export_support import (
    csv_rows,
    load_models,
    mao_headers,
    mutate,
    reviewer_headers,
    seed_export_database,
    success_receipt,
)


EXPORT_COLUMNS = [
    "batch_id", "receipt_token", "action", "candidate_id", "review_id", "review_version",
    "species_code", "target_relative_path", "previous_relative_path",
    "preview_url", "original_url", "source_url", "creator", "license",
    "license_url", "attribution",
]


def create_seed(settings, decisions=(Decision.APPROVED,)):
    return asyncio.run(seed_export_database(settings, decisions=decisions))


def create_batch(client, seed, species_code=None):
    response = client.post(
        "/v1/admin/exports",
        json={"species_code": species_code},
        headers=mao_headers(seed, csrf=True),
    )
    assert response.status_code == 201
    return response


def download(client, seed, batch_id):
    response = client.get(
        f"/v1/admin/exports/{batch_id}.csv",
        headers=mao_headers(seed),
    )
    assert response.status_code == 200
    return response, csv_rows(response)


def receipt(client, batch_id, token, items):
    return client.post(
        f"/v1/sync/batches/{batch_id}/receipt",
        json={"items": items},
        headers={"Authorization": f"Batch {token}"},
    )


def test_new_approved_export_is_immutable_rfc4180_snapshot_and_csv_get_stays_pending(settings):
    seed = create_seed(settings)
    asyncio.run(
        mutate(
            settings,
            Candidate,
            seed.candidate_ids[0],
            creator='Fish, "Quoted"\nCreator',
            attribution='Fish, "Quoted"\nCreator / source',
        )
    )
    with TestClient(create_app(settings)) as client:
        created = create_batch(client, seed, "SF001")
        batch_id = created.json()["id"]
        csv_response, rows = download(client, seed, batch_id)
        counts = client.get(
            "/v1/admin/exports/pending-counts", headers=mao_headers(seed)
        )

    assert list(rows[0]) == EXPORT_COLUMNS
    assert rows[0]["batch_id"] == batch_id
    assert rows[0]["action"] == "ADD"
    assert rows[0]["candidate_id"] == str(seed.candidate_ids[0])
    assert rows[0]["review_id"] == str(seed.review_ids[0])
    assert rows[0]["review_version"] == "1"
    assert rows[0]["species_code"] == "SF001"
    assert rows[0]["target_relative_path"] == f"images/SF001/{seed.candidate_ids[0]}.jpg"
    assert rows[0]["previous_relative_path"] == ""
    assert rows[0]["original_url"].endswith("/1/original.jpg")
    assert rows[0]["creator"] == 'Fish, "Quoted"\nCreator'
    assert rows[0]["attribution"] == 'Fish, "Quoted"\nCreator / source'
    decoded = csv_response.content.decode("utf-8-sig")
    assert '"Fish, ""Quoted""\nCreator"' in decoded
    assert decoded.splitlines()[0] == ",".join(EXPORT_COLUMNS)
    assert csv_response.headers["content-type"].startswith("text/csv; charset=utf-8")
    assert f"sukaseafood-export-{batch_id}.csv" in csv_response.headers["content-disposition"]
    assert counts.status_code == 200
    assert counts.json() == {"SF001": 1, "SF002": 0}

    batches = asyncio.run(load_models(settings, ExportBatch))
    items = asyncio.run(load_models(settings, ExportItem))
    assert batches[0].status == "pending"
    assert items[0].status == "pending"
    assert items[0].succeeded_at is None


def test_batch_json_history_never_exposes_token_and_digest_is_recomputable(settings):
    seed = create_seed(settings)
    with TestClient(create_app(settings)) as client:
        created = create_batch(client, seed)
        batch_id = created.json()["id"]
        _, rows = download(client, seed, batch_id)
        history = client.get("/v1/admin/exports", headers=mao_headers(seed))

    token = rows[0]["receipt_token"]
    expected = hmac.new(
        settings.RECEIPT_SECRET.encode(),
        b"sukaseafood:receipt:v1:" + UUID(batch_id).bytes,
        hashlib.sha256,
    ).digest()
    import base64

    assert token == base64.urlsafe_b64encode(expected).rstrip(b"=").decode()
    stored = asyncio.run(load_models(settings, ExportBatch))[0]
    assert stored.receipt_token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in str(stored.__dict__)
    assert token not in history.text
    assert "receipt_token" not in created.text
    assert history.status_code == 200


def test_same_scope_reuses_cross_scope_overlaps_no_work_and_expiry_regenerates(settings):
    seed = create_seed(settings, (Decision.APPROVED, Decision.REJECTED))
    with TestClient(create_app(settings)) as client:
        first = create_batch(client, seed, "SF001")
        repeated = client.post(
            "/v1/admin/exports",
            json={"species_code": "SF001"},
            headers=mao_headers(seed, csrf=True),
        )
        overlap = client.post(
            "/v1/admin/exports",
            json={},
            headers=mao_headers(seed, csrf=True),
        )
    assert repeated.status_code == 200
    assert repeated.json()["id"] == first.json()["id"]
    assert repeated.json()["created"] is False
    assert overlap.status_code == 409
    assert overlap.json()["detail"]["code"] == "EXPORT_SCOPE_OVERLAP"
    assert overlap.json()["detail"]["batch_ids"] == [first.json()["id"]]

    asyncio.run(
        mutate(
            settings,
            ExportBatch,
            UUID(first.json()["id"]),
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
    )
    with TestClient(create_app(settings)) as client:
        regenerated = create_batch(client, seed, "SF001")
    assert regenerated.json()["id"] != first.json()["id"]
    batches = asyncio.run(load_models(settings, ExportBatch))
    assert sorted(batch.status for batch in batches) == ["expired", "pending"]

    empty_settings = Settings(
        DATABASE_URL=settings.DATABASE_URL.replace("review-test", "empty-export"),
        SESSION_COOKIE_NAME=settings.SESSION_COOKIE_NAME,
        SESSION_HOURS=settings.SESSION_HOURS,
        SESSION_SECRET=settings.SESSION_SECRET,
        CSRF_SECRET=settings.CSRF_SECRET,
        RECEIPT_SECRET=settings.RECEIPT_SECRET,
        APP_ENV="test",
        secure_cookie=False,
    )
    empty_seed = create_seed(empty_settings, (Decision.REJECTED,))
    with TestClient(create_app(empty_settings)) as client:
        no_work = client.post(
            "/v1/admin/exports",
            json={},
            headers=mao_headers(empty_seed, csrf=True),
        )
    assert no_work.status_code == 200
    assert no_work.json() == {"code": "NO_WORK", "created": False, "batch": None}
    assert asyncio.run(load_models(empty_settings, ExportBatch)) == []


def _successful_initial_sync(settings):
    seed = create_seed(settings)
    with TestClient(create_app(settings)) as client:
        batch = create_batch(client, seed)
        _, rows = download(client, seed, batch.json()["id"])
        response = receipt(
            client,
            batch.json()["id"],
            rows[0]["receipt_token"],
            [success_receipt(rows[0])],
        )
        assert response.status_code == 200
    return seed, rows[0]


@pytest.mark.parametrize("desired", [Decision.REJECTED, Decision.UNSURE, None])
def test_locally_present_candidate_becoming_absent_emits_remove(settings, desired):
    seed, local = _successful_initial_sync(settings)

    async def change():
        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            review = await db.get(Review, seed.review_ids[0])
            if desired is None:
                review.is_current = False
            else:
                review.decision = desired
            review.version += 1
            await db.commit()
        await engine.dispose()

    asyncio.run(change())
    with TestClient(create_app(settings)) as client:
        batch = create_batch(client, seed)
        _, rows = download(client, seed, batch.json()["id"])
    assert len(rows) == 1
    assert rows[0]["action"] == "REMOVE"
    assert rows[0]["previous_relative_path"] == local["target_relative_path"]
    assert rows[0]["target_relative_path"].startswith(
        f"_removed/{batch.json()['id']}/{seed.candidate_ids[0]}"
    )
    assert rows[0]["review_id"] == str(seed.review_ids[0])
    assert rows[0]["review_version"] == "2"


def test_species_change_emits_move_and_scope_matches_old_or_new_species(settings):
    seed, local = _successful_initial_sync(settings)
    asyncio.run(
        mutate(
            settings,
            Candidate,
            seed.candidate_ids[0],
            species_id=seed.species_ids[1],
            version=2,
        )
    )
    with TestClient(create_app(settings)) as client:
        batch = create_batch(client, seed, "SF001")
        _, rows = download(client, seed, batch.json()["id"])
    assert rows[0]["action"] == "MOVE"
    assert rows[0]["species_code"] == "SF002"
    assert rows[0]["previous_relative_path"] == local["target_relative_path"]
    assert rows[0]["target_relative_path"] == f"images/SF002/{seed.candidate_ids[0]}.jpg"


def test_species_and_original_change_emits_redownload_add_with_old_path_cleanup(settings):
    seed, local = _successful_initial_sync(settings)
    asyncio.run(
        mutate(
            settings,
            Candidate,
            seed.candidate_ids[0],
            species_id=seed.species_ids[1],
            original_url="https://images.example.test/1/new-content.png",
            version=2,
        )
    )
    with TestClient(create_app(settings)) as client:
        batch = create_batch(client, seed)
        _, rows = download(client, seed, batch.json()["id"])

    assert rows == [
        {
            **rows[0],
            "action": "ADD",
            "species_code": "SF002",
            "target_relative_path": f"images/SF002/{seed.candidate_ids[0]}.png",
            "previous_relative_path": local["target_relative_path"],
            "original_url": "https://images.example.test/1/new-content.png",
        }
    ]


def test_metadata_refresh_and_original_url_change_emit_add_but_unchanged_state_does_not(settings):
    seed, local = _successful_initial_sync(settings)
    with TestClient(create_app(settings)) as client:
        unchanged = client.post(
            "/v1/admin/exports", json={}, headers=mao_headers(seed, csrf=True)
        )
    assert unchanged.status_code == 200
    assert unchanged.json()["code"] == "NO_WORK"

    asyncio.run(
        mutate(
            settings,
            Candidate,
            seed.candidate_ids[0],
            creator="Corrected Creator",
            version=2,
        )
    )
    with TestClient(create_app(settings)) as client:
        metadata_batch = create_batch(client, seed)
        _, metadata_rows = download(client, seed, metadata_batch.json()["id"])
        assert metadata_rows[0]["action"] == "ADD"
        assert metadata_rows[0]["creator"] == "Corrected Creator"
        assert metadata_rows[0]["target_relative_path"] == local["target_relative_path"]
        done = receipt(
            client,
            metadata_batch.json()["id"],
            metadata_rows[0]["receipt_token"],
            [success_receipt(metadata_rows[0], sha256="a" * 64)],
        )
        assert done.status_code == 200

    asyncio.run(
        mutate(
            settings,
            Candidate,
            seed.candidate_ids[0],
            original_url="https://images.example.test/1/replacement.png",
            version=3,
        )
    )
    with TestClient(create_app(settings)) as client:
        url_batch = create_batch(client, seed)
        _, url_rows = download(client, seed, url_batch.json()["id"])
    assert url_rows[0]["action"] == "ADD"
    assert url_rows[0]["original_url"].endswith("replacement.png")


def test_csv_uses_creation_snapshot_after_candidate_review_species_edits(settings):
    seed = create_seed(settings)
    with TestClient(create_app(settings)) as client:
        batch = create_batch(client, seed)
        first_response, first_rows = download(client, seed, batch.json()["id"])
    asyncio.run(
        mutate(
            settings,
            Candidate,
            seed.candidate_ids[0],
            creator="Later Creator",
            original_url="https://later.example.test/later.png",
            version=9,
        )
    )
    asyncio.run(mutate(settings, Review, seed.review_ids[0], version=9))
    asyncio.run(mutate(settings, Species, seed.species_ids[0], code="CHANGED"))
    with TestClient(create_app(settings)) as client:
        later_response, later_rows = download(client, seed, batch.json()["id"])
    assert later_response.content == first_response.content
    assert later_rows == first_rows


def test_mid_rereview_remove_then_later_approval_add_and_inactive_catalog_remove(settings):
    seed, _ = _successful_initial_sync(settings)
    asyncio.run(mutate(settings, Review, seed.review_ids[0], is_current=False, version=2))
    with TestClient(create_app(settings)) as client:
        remove_batch = create_batch(client, seed)
        _, remove_rows = download(client, seed, remove_batch.json()["id"])
        removed = receipt(
            client,
            remove_batch.json()["id"],
            remove_rows[0]["receipt_token"],
            [success_receipt(remove_rows[0], sha256="b" * 64)],
        )
        assert removed.status_code == 200

    async def approve_again():
        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            db.add(
                Review(
                    candidate_id=seed.candidate_ids[0],
                    reviewer_id=seed.hassan_id,
                    decision=Decision.APPROVED,
                    whole_fish="YES",
                    exact_species_verified="YES",
                    is_current=True,
                    version=1,
                )
            )
            await db.commit()
        await engine.dispose()

    asyncio.run(approve_again())
    with TestClient(create_app(settings)) as client:
        add_again = create_batch(client, seed)
        _, rows = download(client, seed, add_again.json()["id"])
    assert rows[0]["action"] == "ADD"

    inactive_settings = Settings(
        DATABASE_URL=settings.DATABASE_URL.replace("review-test", "inactive-export"),
        SESSION_COOKIE_NAME=settings.SESSION_COOKIE_NAME,
        SESSION_HOURS=12,
        SESSION_SECRET=settings.SESSION_SECRET,
        CSRF_SECRET=settings.CSRF_SECRET,
        RECEIPT_SECRET=settings.RECEIPT_SECRET,
        APP_ENV="test",
        secure_cookie=False,
    )
    inactive_seed, _ = _successful_initial_sync(inactive_settings)
    asyncio.run(mutate(inactive_settings, Candidate, inactive_seed.candidate_ids[0], active=False, version=2))
    with TestClient(create_app(inactive_settings)) as client:
        batch = create_batch(client, inactive_seed)
        _, rows = download(client, inactive_seed, batch.json()["id"])
    assert rows[0]["action"] == "REMOVE"


def test_inactive_species_makes_a_locally_present_candidate_removable(settings):
    seed, local = _successful_initial_sync(settings)
    asyncio.run(mutate(settings, Species, seed.species_ids[0], active=False))

    with TestClient(create_app(settings)) as client:
        batch = create_batch(client, seed, "SF001")
        _, rows = download(client, seed, batch.json()["id"])

    assert len(rows) == 1
    assert rows[0]["action"] == "REMOVE"
    assert rows[0]["previous_relative_path"] == local["target_relative_path"]


@pytest.mark.parametrize(
    "unsafe_code",
    ["../outside", "SF/001", "SF\\001", ".", "CON", "COM1", "sf001", "鱼001"],
)
def test_admin_and_export_filter_reject_unsafe_species_codes(settings, unsafe_code):
    seed = create_seed(settings)
    create_payload = {
        "code": unsafe_code,
        "name_zh": "不安全鱼种",
        "name_en": "Unsafe fish",
        "scientific_name": "Piscis unsafe",
        "reason": "validate safe code boundary",
    }
    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/v1/admin/species",
            json=create_payload,
            headers=mao_headers(seed, csrf=True),
        )
        exported = client.post(
            "/v1/admin/exports",
            json={"species_code": unsafe_code},
            headers=mao_headers(seed, csrf=True),
        )

    assert created.status_code == 422
    assert exported.status_code == 422


def test_database_constraint_rejects_unsafe_species_code(settings):
    create_seed(settings)

    async def insert_unsafe():
        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            db.add(
                Species(
                    code="../outside",
                    name_zh="不安全鱼种",
                    name_en="Unsafe fish",
                    scientific_name="Piscis unsafe",
                )
            )
            with pytest.raises(IntegrityError):
                await db.commit()
            await db.rollback()
        await engine.dispose()

    asyncio.run(insert_unsafe())


def test_export_boundary_rejects_corrupted_unsafe_species_without_creating_paths(settings):
    seed = create_seed(settings)

    async def corrupt_legacy_code():
        engine = create_async_engine(settings.DATABASE_URL)
        async with engine.begin() as connection:
            await connection.execute(text("PRAGMA ignore_check_constraints = ON"))
            await connection.execute(
                text("UPDATE species SET code = '../outside' WHERE id = :species_id"),
                {"species_id": seed.species_ids[0].hex},
            )
        await engine.dispose()

    asyncio.run(corrupt_legacy_code())
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/v1/admin/exports",
            json={},
            headers=mao_headers(seed, csrf=True),
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "UNSAFE_SPECIES_CODE"
    assert asyncio.run(load_models(settings, ExportBatch)) == []


def test_export_history_and_pending_count_gets_compute_expiry_without_writes(settings):
    seed = create_seed(settings)
    with TestClient(create_app(settings)) as client:
        created = create_batch(client, seed)
    batch_id = UUID(created.json()["id"])
    asyncio.run(
        mutate(
            settings,
            ExportBatch,
            batch_id,
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
    )

    with TestClient(create_app(settings)) as client:
        history = client.get("/v1/admin/exports", headers=mao_headers(seed))
        counts = client.get(
            "/v1/admin/exports/pending-counts", headers=mao_headers(seed)
        )

    assert history.status_code == 200
    assert history.json()["items"][0]["status"] == "expired"
    assert history.json()["items"][0]["expired_at"] is None
    assert counts.json() == {"SF001": 1, "SF002": 0}
    stored = asyncio.run(load_models(settings, ExportBatch))[0]
    audits = asyncio.run(load_models(settings, AuditEvent))
    assert stored.status == "pending"
    assert stored.expired_at is None
    assert [audit.action for audit in audits] == ["EXPORT_BATCH_CREATE"]


def test_admin_permissions_csrf_unknown_species_and_stable_zero_pending_counts(settings):
    seed = create_seed(settings, (Decision.REJECTED,))
    with TestClient(create_app(settings)) as client:
        anonymous = client.get("/v1/admin/exports")
        reviewer = client.get("/v1/admin/exports", headers=reviewer_headers(seed))
        no_csrf = client.post("/v1/admin/exports", json={}, headers=mao_headers(seed))
        reviewer_post = client.post(
            "/v1/admin/exports", json={}, headers=reviewer_headers(seed)
        )
        unknown = client.post(
            "/v1/admin/exports",
            json={"species_code": "NOPE"},
            headers=mao_headers(seed, csrf=True),
        )
        counts = client.get(
            "/v1/admin/exports/pending-counts", headers=mao_headers(seed)
        )
    assert anonymous.status_code == 401
    assert reviewer.status_code == reviewer_post.status_code == 403
    assert no_csrf.status_code == 403
    assert unknown.status_code == 404
    assert counts.json() == {"SF001": 0, "SF002": 0}


def test_batch_create_rolls_back_items_when_audit_insert_fails(settings):
    seed = create_seed(settings)

    def fail_audit(*_):
        raise RuntimeError("forced export audit failure")

    event.listen(AuditEvent, "before_insert", fail_audit)
    try:
        with pytest.raises(RuntimeError, match="forced export audit failure"):
            with TestClient(create_app(settings), raise_server_exceptions=True) as client:
                create_batch(client, seed)
    finally:
        event.remove(AuditEvent, "before_insert", fail_audit)
    assert asyncio.run(load_models(settings, ExportBatch)) == []
    assert asyncio.run(load_models(settings, ExportItem)) == []


def test_production_api_rejects_missing_weak_or_reused_receipt_secret(tmp_path):
    common = dict(
        DATABASE_URL="postgresql+asyncpg://review:password@db.example.test/review",
        SESSION_COOKIE_NAME="review_session",
        SESSION_HOURS=12,
        SESSION_SECRET="session-secret-that-is-long-enough-1234",
        CSRF_SECRET="csrf-secret-that-is-long-enough-123456",
        APP_ENV="production",
        TRUSTED_PROXY_CIDRS=("127.0.0.1/32",),
    )
    for value in (
        None,
        "weak",
        "x" * 64,
        common["SESSION_SECRET"],
        common["CSRF_SECRET"],
    ):
        with pytest.raises(ValueError, match="RECEIPT_SECRET"):
            create_app(Settings(**common, RECEIPT_SECRET=value))


def test_export_migration_upgrade_downgrade_reupgrade_and_postgres_offline_ddl(tmp_path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect
    from sqlalchemy.ext.asyncio import create_async_engine
    import io

    api_root = Path(__file__).parents[1]
    database = tmp_path / "export-migration.sqlite3"
    config = Config(api_root / "alembic.ini")
    config.set_main_option("script_location", str(api_root / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database.as_posix()}")
    command.upgrade(config, "head")

    async def shape():
        engine = create_async_engine(f"sqlite+aiosqlite:///{database.as_posix()}")
        async with engine.connect() as connection:
            result = await connection.run_sync(
                lambda sync: {
                    "batch": {column["name"] for column in inspect(sync).get_columns("export_batches")},
                    "item": {column["name"] for column in inspect(sync).get_columns("export_items")},
                    "checks": {check["name"] for check in inspect(sync).get_check_constraints("export_items")},
                }
            )
        await engine.dispose()
        return result

    current = asyncio.run(shape())
    assert {"scope_key", "completed_at", "expired_at"} <= current["batch"]
    assert {
        "candidate_version", "species_code", "preview_url", "original_url",
        "source_url", "creator", "license", "license_url", "attribution",
        "original_fingerprint", "metadata_fingerprint", "local_relative_path",
    } <= current["item"]
    assert "ck_export_items_status" in current["checks"]
    command.downgrade(config, "20260826_04")
    assert "scope_key" not in asyncio.run(shape())["batch"]
    command.upgrade(config, "head")
    assert "scope_key" in asyncio.run(shape())["batch"]

    output = io.StringIO()
    pg = Config(api_root / "alembic.ini", output_buffer=output)
    pg.set_main_option("script_location", str(api_root / "alembic"))
    pg.set_main_option("sqlalchemy.url", "postgresql://review:password@db/review")
    command.upgrade(pg, "head", sql=True)
    ddl = output.getvalue()
    assert "20260826_05" in ddl
    assert "CREATE UNIQUE INDEX uq_export_batches_pending_scope" in ddl
    assert "original_fingerprint" in ddl


def test_populated_revision_04_backfills_canonical_scope_and_reuses_after_reupgrade(tmp_path):
    from alembic import command
    from alembic.config import Config

    api_root = Path(__file__).parents[1]
    database = tmp_path / "populated-export-migration.sqlite3"
    database_url = f"sqlite+aiosqlite:///{database.as_posix()}"
    config = Config(api_root / "alembic.ini")
    config.set_main_option("script_location", str(api_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "20260826_04")
    user_id = uuid4()
    species_id = uuid4()
    batch_id = uuid4()

    async def seed_revision_04():
        engine = create_async_engine(database_url)
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO users "
                    "(id, name, role, password_hash, must_change_password, active, "
                    "failed_login_count, password_version) "
                    "VALUES (:id, 'Mao', 'admin', 'hash', 0, 1, 0, 1)"
                ),
                {"id": user_id.hex},
            )
            await connection.execute(
                text(
                    "INSERT INTO species "
                    "(id, code, name_zh, name_en, scientific_name, active, sort_order) "
                    "VALUES (:id, 'SF001', '鱼', 'Fish', 'Piscis', 1, 1)"
                ),
                {"id": species_id.hex},
            )
            await connection.execute(
                text(
                    "INSERT INTO export_batches "
                    "(id, created_by_id, species_id, receipt_token_hash, status, expires_at) "
                    "VALUES (:id, :user_id, :species_id, :digest, 'pending', :expires_at)"
                ),
                {
                    "id": batch_id.hex,
                    "user_id": user_id.hex,
                    "species_id": species_id.hex,
                    "digest": "a" * 64,
                    "expires_at": "2099-01-01 00:00:00+00:00",
                },
            )
        await engine.dispose()

    async def scope_and_reuse():
        from app.services.exports import create_export_batch

        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            scope = await db.scalar(
                select(ExportBatch.scope_key).where(ExportBatch.id == batch_id)
            )
            reused = await create_export_batch(
                db,
                user_id,
                "SF001",
                "migration-receipt-secret-that-is-long-enough",
            )
        await engine.dispose()
        return scope, reused

    asyncio.run(seed_revision_04())
    command.upgrade(config, "head")
    first_scope, first_reuse = asyncio.run(scope_and_reuse())
    assert first_scope == str(species_id)
    assert first_reuse.batch.id == batch_id
    assert first_reuse.created is False

    command.downgrade(config, "20260826_04")
    command.upgrade(config, "head")
    second_scope, second_reuse = asyncio.run(scope_and_reuse())
    assert second_scope == str(species_id)
    assert second_reuse.batch.id == batch_id
    assert second_reuse.created is False


def test_revision_05_rejects_existing_unsafe_species_codes_before_schema_changes(tmp_path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    api_root = Path(__file__).parents[1]
    database = tmp_path / "unsafe-species-migration.sqlite3"
    database_url = f"sqlite+aiosqlite:///{database.as_posix()}"
    config = Config(api_root / "alembic.ini")
    config.set_main_option("script_location", str(api_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "20260826_04")

    async def seed_and_columns():
        engine = create_async_engine(database_url)
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO species "
                    "(id, code, name_zh, name_en, scientific_name, active, sort_order) "
                    "VALUES (:id, '../outside', '鱼', 'Fish', 'Piscis', 1, 1)"
                ),
                {"id": uuid4().hex},
            )
        async with engine.connect() as connection:
            columns = await connection.run_sync(
                lambda sync: {
                    column["name"]
                    for column in inspect(sync).get_columns("export_batches")
                }
            )
        await engine.dispose()
        return columns

    before = asyncio.run(seed_and_columns())
    assert "scope_key" not in before
    with pytest.raises(RuntimeError, match=r"unsafe species codes.*\.\./outside"):
        command.upgrade(config, "head")

    async def columns_after_failure():
        engine = create_async_engine(database_url)
        async with engine.connect() as connection:
            columns = await connection.run_sync(
                lambda sync: {
                    column["name"]
                    for column in inspect(sync).get_columns("export_batches")
                }
            )
        await engine.dispose()
        return columns

    assert "scope_key" not in asyncio.run(columns_after_failure())
