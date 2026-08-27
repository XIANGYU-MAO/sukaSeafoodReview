from __future__ import annotations

import asyncio
from datetime import timedelta
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    AuditEvent,
    Base,
    Candidate,
    Decision,
    ExportAction,
    ExportBatch,
    ExportItem,
    Review,
    ReviewRevision,
    Species,
    User,
)
from app.schemas.exports import ReceiptItem
from app.services.auth import utc_now
from app.services.exports import (
    ReceiptRejected,
    apply_receipt,
    create_export_batch,
    receipt_token,
)


POSTGRES_URL = os.getenv("TEST_POSTGRES_DSN") or os.getenv("TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="TEST_POSTGRES_DSN is required for real PostgreSQL export races",
)
SECRET = "postgres-export-receipt-secret-is-unique-and-long"


async def create_migration_schema():
    schema = f"task1_epoch_{uuid4().hex}"
    admin_engine = create_async_engine(POSTGRES_URL)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    await admin_engine.dispose()
    return schema


async def drop_migration_schema(schema):
    admin_engine = create_async_engine(POSTGRES_URL)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    await admin_engine.dispose()


def migration_config(schema):
    api_root = Path(__file__).parents[2]
    config = Config(api_root / "alembic.ini")
    config.set_main_option("script_location", str(api_root / "alembic"))
    config.set_main_option("sqlalchemy.url", POSTGRES_URL)
    config.set_main_option("migration_schema", schema)
    return config


def migration_engine(schema):
    return create_async_engine(
        POSTGRES_URL,
        connect_args={"server_settings": {"search_path": schema}},
    )


async def install_update_delay(schema, table):
    engine = migration_engine(schema)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "CREATE FUNCTION task1_delay_update() RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN PERFORM pg_sleep(1); RETURN NEW; END; $$"
            )
        )
        await connection.execute(
            text(
                f"CREATE TRIGGER task1_delay_update BEFORE UPDATE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION task1_delay_update()"
            )
        )
    await engine.dispose()


async def wait_for_export_batches_lock(schema, mode):
    engine = migration_engine(schema)
    try:
        for _ in range(100):
            async with engine.connect() as connection:
                locked = await connection.scalar(
                    text(
                        "SELECT EXISTS ("
                        "SELECT 1 FROM pg_locks locks "
                        "JOIN pg_class tables ON tables.oid = locks.relation "
                        "JOIN pg_namespace schemas ON schemas.oid = tables.relnamespace "
                        "WHERE schemas.nspname = :schema AND tables.relname = 'export_batches' "
                        "AND locks.mode = :mode AND locks.granted"
                        ")"
                    ),
                    {"schema": schema, "mode": mode},
                )
            if locked:
                return
            await asyncio.sleep(0.05)
    finally:
        await engine.dispose()
    raise AssertionError(f"migration did not acquire {mode} on export_batches")


async def in_isolated_schema(operation):
    schema = f"task8_export_{uuid4().hex}"
    admin_engine = create_async_engine(POSTGRES_URL)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        POSTGRES_URL,
        connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        return await operation(engine)
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin_engine.dispose()


async def seed(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        mao = User(name="Mao", role="admin", password_hash="test", must_change_password=False)
        hassan = User(name="Hassan", role="reviewer", password_hash="test", must_change_password=False)
        species = Species(code="SF001", name_zh="测试鱼", name_en="Test fish", scientific_name="Piscis probatio")
        db.add_all([mao, hassan, species])
        await db.flush()
        candidate = Candidate(
            species_id=species.id,
            source_dataset="INATURALIST",
            source_record_id="race-1",
            preview_url="https://images.example.test/race/preview.jpg",
            original_url="https://images.example.test/race/original.jpg",
            source_url="https://source.example.test/race",
            creator="Race Creator",
            license="CC-BY-4.0",
            attribution="Race Creator",
            metadata_json={},
        )
        db.add(candidate)
        await db.flush()
        review = Review(
            candidate_id=candidate.id,
            reviewer_id=hassan.id,
            decision=Decision.APPROVED,
            whole_fish="YES",
            exact_species_verified="YES",
        )
        db.add(review)
        await db.commit()
        return mao.id, candidate.id


def test_simultaneous_same_scope_create_converges_on_one_batch_and_item_set():
    from app.services.exports import create_export_batch

    async def operation(engine):
        mao_id, candidate_id = await seed(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as first, factory() as second:
            results = await asyncio.gather(
                create_export_batch(first, mao_id, None, SECRET),
                create_export_batch(second, mao_id, None, SECRET),
            )
        async with factory() as verify:
            return results, (
                int(await verify.scalar(select(func.count()).select_from(ExportBatch)) or 0),
                int(await verify.scalar(select(func.count()).select_from(ExportItem)) or 0),
                int(await verify.scalar(select(func.count()).select_from(AuditEvent)) or 0),
            ), candidate_id

    results, counts, candidate_id = asyncio.run(in_isolated_schema(operation))
    assert results[0].batch.id == results[1].batch.id
    assert sorted(result.created for result in results) == [False, True]
    assert counts == (1, 1, 1)


def test_concurrent_identical_receipt_converges_without_errors_or_duplicate_audit():
    from app.schemas.exports import ReceiptItem
    from app.services.exports import apply_receipt, create_export_batch, receipt_token

    async def operation(engine):
        mao_id, candidate_id = await seed(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            created = await create_export_batch(db, mao_id, None, SECRET)
            item = await db.scalar(select(ExportItem).where(ExportItem.batch_id == created.batch.id))
            token = receipt_token(created.batch.id, SECRET)
            payload = ReceiptItem(
                candidate_id=item.candidate_id,
                review_id=item.review_id,
                review_version=item.review_version,
                status="SUCCEEDED",
                sha256="d" * 64,
                relative_path=item.target_relative_path,
            )
            batch_id = created.batch.id
        async with factory() as first, factory() as second:
            results = await asyncio.gather(
                apply_receipt(
                    first,
                    batch_id,
                    [payload],
                    raw_token=token,
                ),
                apply_receipt(
                    second,
                    batch_id,
                    [payload],
                    raw_token=token,
                ),
            )
        async with factory() as verify:
            stored = await verify.scalar(select(ExportItem).where(ExportItem.candidate_id == candidate_id))
            receipt_audits = int(
                await verify.scalar(
                    select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "EXPORT_RECEIPT_APPLY")
                ) or 0
            )
        return results, stored, receipt_audits

    results, stored, receipt_audits = asyncio.run(in_isolated_schema(operation))
    assert results[0].status == results[1].status == "completed"
    assert stored.status == "succeeded"
    assert stored.sha256 == "d" * 64
    assert receipt_audits == 1


@pytest.mark.parametrize(
    "mutation",
    ["candidate_url", "species_correction", "review_reopen", "review_revision"],
)
def test_export_persists_one_pre_or_post_mutation_snapshot_at_deterministic_barrier(
    monkeypatch, mutation
):
    from app.services import exports

    async def operation(engine):
        mao_id, candidate_id = await seed(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as setup:
            second_species = Species(
                code="SF002",
                name_zh="第二鱼种",
                name_en="Second fish",
                scientific_name="Piscis secundus",
            )
            setup.add(second_species)
            await setup.commit()
            second_species_id = second_species.id

        snapshot_established = asyncio.Event()
        mutation_committed = asyncio.Event()
        original_state_maps = exports._state_maps

        async def barrier_state_maps(session):
            # Export creation has already begun its REPEATABLE READ transaction.
            # This read makes the PostgreSQL snapshot boundary explicit before
            # the competing writer commits.
            await session.scalar(
                select(Candidate.id).where(Candidate.id == candidate_id)
            )
            snapshot_established.set()
            await mutation_committed.wait()
            return await original_state_maps(session)

        monkeypatch.setattr(exports, "_state_maps", barrier_state_maps)
        async with factory() as export_db:
            task = asyncio.create_task(
                exports.create_export_batch(export_db, mao_id, None, SECRET)
            )
            await asyncio.wait_for(snapshot_established.wait(), timeout=5)
            async with factory() as writer:
                candidate = await writer.get(Candidate, candidate_id)
                assert candidate is not None
                review = await writer.scalar(
                    select(Review).where(
                        Review.candidate_id == candidate_id,
                        Review.is_current.is_(True),
                    )
                )
                assert review is not None
                candidate.version += 1
                if mutation == "candidate_url":
                    candidate.preview_url = "https://images.example.test/changed/preview.jpg"
                    candidate.original_url = "https://images.example.test/changed/original.jpg"
                elif mutation == "species_correction":
                    candidate.species_id = second_species_id
                elif mutation == "review_reopen":
                    review.is_current = False
                    writer.add(
                        Review(
                            candidate_id=candidate_id,
                            reviewer_id=review.reviewer_id,
                            decision=Decision.UNSURE,
                            is_current=True,
                            version=1,
                        )
                    )
                else:
                    review.decision = Decision.REJECTED
                    review.rejection_reason = "OTHER"
                    review.whole_fish = "NO"
                    review.exact_species_verified = "NO"
                    review.version += 1
                await writer.commit()
            mutation_committed.set()
            result = await asyncio.wait_for(task, timeout=10)

        async with factory() as verify:
            item = await verify.scalar(
                select(ExportItem).where(ExportItem.batch_id == result.batch.id)
            )
            current = await verify.get(Candidate, candidate_id)
        return item, current

    item, current = asyncio.run(in_isolated_schema(operation))

    # The writer committed before delta derivation continued, but the persisted
    # item is wholly from the old state.  Seeing any new field paired with old
    # fields would prove a torn READ COMMITTED export.
    assert item.action == ExportAction.ADD
    assert item.candidate_version == item.review_version == 1
    assert item.species_code == "SF001"
    assert item.original_url == "https://images.example.test/race/original.jpg"
    assert current.version == 2


def test_postgres_epoch_migration_uses_greatest_and_expires_populated_batches_then_downgrades():
    schema = asyncio.run(create_migration_schema())
    config = migration_config(schema)
    try:
        command.upgrade(config, "20260827_06")

        async def seed_populated_epoch():
            engine = migration_engine(schema)
            mao_id, candidate_id = await seed(engine)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as db:
                candidate = await db.get(Candidate, candidate_id)
                review = await db.scalar(select(Review).where(Review.candidate_id == candidate_id))
                assert candidate is not None and review is not None
                candidate.version = 3
                review.version = 5
                db.add(
                    ReviewRevision(
                        candidate_id=candidate.id,
                        review_id=review.id,
                        reviewer_id=review.reviewer_id,
                        actor_id=review.reviewer_id,
                        decision=Decision.APPROVED,
                        is_current=True,
                        review_version=5,
                        snapshot_json={},
                    )
                )
                await db.commit()
                created = await create_export_batch(db, mao_id, None, SECRET)
                item = await db.scalar(select(ExportItem).where(ExportItem.batch_id == created.batch.id))
                assert item is not None
                item.review_version = 8
                await db.commit()
                batch_id = created.batch.id
            await engine.dispose()
            return candidate_id, batch_id

        candidate_id, batch_id = asyncio.run(seed_populated_epoch())
        command.upgrade(config, "20260827_07")

        async def verify_epoch():
            engine = migration_engine(schema)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as db:
                candidate = await db.get(Candidate, candidate_id)
                batch = await db.get(ExportBatch, batch_id)
            await engine.dispose()
            return candidate, batch

        candidate, batch = asyncio.run(verify_epoch())
        assert candidate is not None and candidate.version == 9
        assert batch is not None and batch.status == "expired"
        command.downgrade(config, "20260827_06")
    finally:
        asyncio.run(drop_migration_schema(schema))


@pytest.mark.parametrize(
    ("starting_version", "expected_version", "raises"),
    [
        (2_147_483_646, 2_147_483_647, False),
        (2_147_483_647, None, True),
    ],
)
def test_postgres_epoch_migration_integer_boundaries(starting_version, expected_version, raises):
    schema = asyncio.run(create_migration_schema())
    config = migration_config(schema)
    try:
        command.upgrade(config, "20260827_06")

        async def seed_boundary():
            engine = migration_engine(schema)
            _, candidate_id = await seed(engine)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as db:
                candidate = await db.get(Candidate, candidate_id)
                assert candidate is not None
                candidate.version = starting_version
                await db.commit()
            await engine.dispose()
            return candidate_id

        candidate_id = asyncio.run(seed_boundary())
        if raises:
            with pytest.raises(RuntimeError, match="candidate synchronization generation exhausted"):
                command.upgrade(config, "20260827_07")
        else:
            command.upgrade(config, "20260827_07")

            async def read_version():
                engine = migration_engine(schema)
                factory = async_sessionmaker(engine, expire_on_commit=False)
                async with factory() as db:
                    candidate = await db.get(Candidate, candidate_id)
                await engine.dispose()
                return candidate.version if candidate is not None else None

            assert asyncio.run(read_version()) == expected_version
    finally:
        asyncio.run(drop_migration_schema(schema))


def test_postgres_epoch_migration_serializes_against_receipt_completion():
    schema = asyncio.run(create_migration_schema())
    config = migration_config(schema)
    try:
        command.upgrade(config, "20260827_06")

        async def race_receipt():
            engine = migration_engine(schema)
            mao_id, _ = await seed(engine)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as db:
                created = await create_export_batch(db, mao_id, None, SECRET)
                item = await db.scalar(select(ExportItem).where(ExportItem.batch_id == created.batch.id))
                assert item is not None
                batch_id = created.batch.id
                payload = ReceiptItem(
                    candidate_id=item.candidate_id,
                    review_id=item.review_id,
                    review_version=item.review_version,
                    status="SUCCEEDED",
                    sha256="e" * 64,
                    relative_path=item.target_relative_path,
                )
                token = receipt_token(batch_id, SECRET)
            await install_update_delay(schema, "candidates")
            migration = asyncio.create_task(
                asyncio.to_thread(command.upgrade, config, "20260827_07")
            )
            await wait_for_export_batches_lock(schema, "ShareRowExclusiveLock")
            async with factory() as receipt_db:
                receipt_result = await asyncio.gather(
                    apply_receipt(receipt_db, batch_id, [payload], raw_token=token),
                    return_exceptions=True,
                )
            await migration
            async with factory() as verify:
                batch = await verify.get(ExportBatch, batch_id)
            await engine.dispose()
            return receipt_result[0], batch

        receipt_result, batch = asyncio.run(race_receipt())
        assert isinstance(receipt_result, ReceiptRejected)
        assert batch is not None and batch.status == "expired"
    finally:
        asyncio.run(drop_migration_schema(schema))


def test_postgres_chunking_downgrade_serializes_against_export_creation():
    schema = asyncio.run(create_migration_schema())
    config = migration_config(schema)
    try:
        command.upgrade(config, "20260827_06")

        async def race_export():
            engine = migration_engine(schema)
            mao_id, _ = await seed(engine)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            oldest_id, newer_id = uuid4(), uuid4()
            async with factory() as db:
                now = utc_now()
                db.add_all(
                    [
                        ExportBatch(
                            id=oldest_id,
                            created_by_id=mao_id,
                            scope_key="ALL",
                            receipt_token_hash="1" * 64,
                            status="pending",
                            expires_at=now + timedelta(days=1),
                            created_at=now,
                        ),
                        ExportBatch(
                            id=newer_id,
                            created_by_id=mao_id,
                            scope_key="ALL",
                            receipt_token_hash="2" * 64,
                            status="pending",
                            expires_at=now + timedelta(days=1),
                            created_at=now + timedelta(seconds=1),
                        ),
                    ]
                )
                await db.commit()
            await install_update_delay(schema, "export_batches")
            downgrade = asyncio.create_task(
                asyncio.to_thread(command.downgrade, config, "20260826_05")
            )
            await wait_for_export_batches_lock(schema, "ShareUpdateExclusiveLock")
            async with factory() as exporter_db:
                export_result = await create_export_batch(exporter_db, mao_id, None, SECRET)
            await downgrade
            async with factory() as verify:
                pending_ids = list(
                    (
                        await verify.scalars(
                            select(ExportBatch.id)
                            .where(ExportBatch.status == "pending")
                            .order_by(ExportBatch.created_at, ExportBatch.id)
                        )
                    ).all()
                )
            await engine.dispose()
            return export_result, pending_ids, oldest_id

        export_result, pending_ids, oldest_id = asyncio.run(race_export())
        assert export_result.created is False
        assert export_result.batch.id == oldest_id
        assert pending_ids == [oldest_id]
    finally:
        asyncio.run(drop_migration_schema(schema))
