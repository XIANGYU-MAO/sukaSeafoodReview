from __future__ import annotations

import asyncio
from datetime import timedelta
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event, func, select, text
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
BARRIER_LOCK_KEY = 1_953_720_742


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
    config.set_main_option("sqlalchemy.url", POSTGRES_URL.replace("%", "%%"))
    config.set_main_option("migration_schema", schema)
    config.attributes["ignore_environment_database_url"] = True
    return config


def migration_engine(schema):
    quoted_schema = '"' + schema.replace('"', '""') + '"'
    engine = create_async_engine(
        POSTGRES_URL,
        connect_args={"server_settings": {"search_path": quoted_schema}},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def set_schema_search_path(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute(f"SET search_path TO {quoted_schema}")
        cursor.close()

    return engine


def test_migration_config_is_hermetic_with_percent_dsn_and_ambient_url(
    monkeypatch,
):
    schema = asyncio.run(create_migration_schema())
    scheme, authority = POSTGRES_URL.split("://", 1)
    credentials, location = authority.rsplit("@", 1)
    username, password = credentials.split(":", 1)
    encoded_password = f"%{ord(password[0]):02X}{password[1:]}"
    explicit_url = f"{scheme}://{username}:{encoded_password}@{location}"
    monkeypatch.setitem(globals(), "POSTGRES_URL", explicit_url)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://invalid:invalid@127.0.0.1:1/invalid",
    )
    try:
        config = migration_config(schema)
        assert config.get_main_option("sqlalchemy.url") == explicit_url
        command.upgrade(config, "head")
    finally:
        asyncio.run(drop_migration_schema(schema))


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


async def wait_for_advisory_lock_counts(
    schema, lock_key, *, granted_at_least=0, waiting_at_least=0
):
    engine = migration_engine(schema)
    try:
        for _ in range(100):
            async with engine.connect() as connection:
                counts = (
                    await connection.execute(
                        text(
                            "SELECT "
                            "COUNT(*) FILTER (WHERE granted), "
                            "COUNT(*) FILTER (WHERE NOT granted) "
                            "FROM pg_locks "
                            "WHERE locktype = 'advisory' AND classid = 0 "
                            "AND objid = :lock_key"
                        ),
                        {"lock_key": lock_key},
                    )
                ).one()
            if int(counts[0]) >= granted_at_least and int(counts[1]) >= waiting_at_least:
                return
            await asyncio.sleep(0.05)
    finally:
        await engine.dispose()
    raise AssertionError(
        f"advisory lock {lock_key} did not reach "
        f"granted>={granted_at_least}, waiting>={waiting_at_least}"
    )


async def export_batches_lock_count(schema, mode):
    engine = migration_engine(schema)
    try:
        async with engine.connect() as connection:
            return int(
                await connection.scalar(
                    text(
                        "SELECT COUNT(*) FROM pg_locks locks "
                        "JOIN pg_class tables ON tables.oid = locks.relation "
                        "JOIN pg_namespace schemas ON schemas.oid = tables.relnamespace "
                        "WHERE schemas.nspname = :schema "
                        "AND tables.relname = 'export_batches' "
                        "AND locks.mode = :mode AND locks.granted"
                    ),
                    {"schema": schema, "mode": mode},
                )
                or 0
            )
    finally:
        await engine.dispose()


async def wait_for_export_batches_lock_counts(
    schema, mode, *, granted_at_least=0, waiting_at_least=0
):
    engine = migration_engine(schema)
    try:
        for _ in range(100):
            async with engine.connect() as connection:
                counts = (
                    await connection.execute(
                        text(
                            "SELECT "
                            "COUNT(*) FILTER (WHERE locks.granted), "
                            "COUNT(*) FILTER (WHERE NOT locks.granted) "
                            "FROM pg_locks locks "
                            "JOIN pg_class tables ON tables.oid = locks.relation "
                            "JOIN pg_namespace schemas ON schemas.oid = tables.relnamespace "
                            "WHERE schemas.nspname = :schema "
                            "AND tables.relname = 'export_batches' "
                            "AND locks.mode = :mode"
                        ),
                        {"schema": schema, "mode": mode},
                    )
                ).one()
            if int(counts[0]) >= granted_at_least and int(counts[1]) >= waiting_at_least:
                return
            await asyncio.sleep(0.05)
    finally:
        await engine.dispose()
    raise AssertionError(
        f"export_batches {mode} did not reach "
        f"granted>={granted_at_least}, waiting>={waiting_at_least}"
    )


async def install_advisory_barrier_trigger(schema, table):
    engine = migration_engine(schema)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "CREATE OR REPLACE FUNCTION task_final_barrier_update() "
                "RETURNS trigger LANGUAGE plpgsql AS $$ "
                f"BEGIN PERFORM pg_advisory_xact_lock({BARRIER_LOCK_KEY}); "
                "RETURN NEW; END; $$"
            )
        )
        await connection.execute(
            text(
                f"CREATE TRIGGER task_final_barrier_update BEFORE UPDATE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION task_final_barrier_update()"
            )
        )
    await engine.dispose()


async def acquire_barrier(engine):
    connection = await engine.connect()
    transaction = await connection.begin()
    await connection.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": BARRIER_LOCK_KEY},
    )
    return connection, transaction


async def release_barrier(connection, transaction):
    if transaction.is_active:
        await transaction.commit()
    await connection.close()


async def seed_high_review(engine):
    _mao_id, candidate_id = await seed(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        candidate = await db.get(Candidate, candidate_id)
        review = await db.scalar(select(Review).where(Review.candidate_id == candidate_id))
        assert candidate is not None and review is not None
        candidate.version = 3
        review.version = 100
        reviewer_id = review.reviewer_id
        review_id = review.id
        await db.commit()
    return candidate_id, reviewer_id, review_id


async def assert_strict_candidate_epoch(engine, candidate_id):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        candidate_version = int(
            await db.scalar(select(Candidate.version).where(Candidate.id == candidate_id))
        )
        maximum = int(
            await db.scalar(
                text(
                    "SELECT MAX(v) FROM ("
                    "SELECT version AS v FROM reviews WHERE candidate_id = :candidate_id "
                    "UNION ALL SELECT review_version FROM review_revisions "
                    "WHERE candidate_id = :candidate_id "
                    "UNION ALL SELECT review_version FROM export_items "
                    "WHERE candidate_id = :candidate_id"
                    ") AS historical"
                ),
                {"candidate_id": candidate_id},
            )
        )
    assert candidate_version > maximum


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
            # Export creation already owns the entry gate in REPEATABLE READ.
            # This read fixes its coherent snapshot before the raw test writer.
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
            await install_update_delay(schema, "export_batches")
            migration = asyncio.create_task(
                asyncio.to_thread(command.upgrade, config, "20260827_07")
            )
            await wait_for_export_batches_lock(schema, "ShareRowExclusiveLock")
            await wait_for_export_batches_lock(schema, "RowExclusiveLock")
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


def test_postgres_epoch_writer_first_waits_at_shared_entry_boundary():
    """A high review writer must commit before the epoch snapshot is chosen."""

    from app.services.sync_generation import acquire_sync_generation_lock

    schema = asyncio.run(create_migration_schema())
    config = migration_config(schema)
    try:
        command.upgrade(config, "20260827_06")

        async def race_writer_first():
            engine = migration_engine(schema)
            candidate_id, _reviewer_id, review_id = await seed_high_review(engine)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            writer_entered = asyncio.Event()
            release_writer = asyncio.Event()
            writer = migration = None

            async def write_high_review():
                async with factory() as writer_db:
                    await acquire_sync_generation_lock(writer_db)
                    writer_entered.set()
                    await release_writer.wait()
                    candidate = await writer_db.scalar(
                        select(Candidate)
                        .where(Candidate.id == candidate_id)
                        .with_for_update()
                    )
                    review = await writer_db.scalar(
                        select(Review)
                        .where(Review.id == review_id)
                        .with_for_update()
                    )
                    assert candidate is not None and review is not None
                    review.version += 1
                    candidate.version += 1
                    writer_db.add(
                        ReviewRevision(
                            candidate_id=candidate.id,
                            review_id=review.id,
                            reviewer_id=review.reviewer_id,
                            actor_id=review.reviewer_id,
                            decision=review.decision,
                            is_current=True,
                            review_version=review.version,
                            snapshot_json={},
                        )
                    )
                    await writer_db.commit()

            try:
                writer = asyncio.create_task(write_high_review())
                await asyncio.wait_for(writer_entered.wait(), timeout=5)
                migration = asyncio.create_task(
                    asyncio.to_thread(command.upgrade, config, "20260827_07")
                )
                await wait_for_export_batches_lock_counts(
                    schema,
                    "ShareRowExclusiveLock",
                    waiting_at_least=1,
                )
                release_writer.set()
                await asyncio.wait_for(writer, timeout=10)
                await asyncio.wait_for(migration, timeout=10)
            finally:
                release_writer.set()
                pending = [task for task in (writer, migration) if task is not None]
                if pending:
                    await asyncio.wait_for(
                        asyncio.gather(*pending, return_exceptions=True), timeout=20
                    )
            await assert_strict_candidate_epoch(engine, candidate_id)
            await engine.dispose()

        asyncio.run(race_writer_first())
    finally:
        asyncio.run(drop_migration_schema(schema))


def test_postgres_epoch_migration_first_blocks_writer_before_row_locks():
    """Migration ownership of the boundary must precede all writer row locks."""

    from app.services.sync_generation import acquire_sync_generation_lock

    schema = asyncio.run(create_migration_schema())
    config = migration_config(schema)
    try:
        command.upgrade(config, "20260827_06")

        async def race_migration_first():
            engine = migration_engine(schema)
            candidate_id, _reviewer_id, review_id = await seed_high_review(engine)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            await install_advisory_barrier_trigger(schema, "candidates")
            barrier_connection, barrier_transaction = await acquire_barrier(engine)
            writer = migration = None

            async def write_high_review():
                async with factory() as writer_db:
                    await acquire_sync_generation_lock(writer_db)
                    candidate = await writer_db.scalar(
                        select(Candidate)
                        .where(Candidate.id == candidate_id)
                        .with_for_update()
                    )
                    review = await writer_db.scalar(
                        select(Review)
                        .where(Review.id == review_id)
                        .with_for_update()
                    )
                    assert candidate is not None and review is not None
                    review.version += 1
                    candidate.version += 1
                    writer_db.add(
                        ReviewRevision(
                            candidate_id=candidate.id,
                            review_id=review.id,
                            reviewer_id=review.reviewer_id,
                            actor_id=review.reviewer_id,
                            decision=review.decision,
                            is_current=True,
                            review_version=review.version,
                            snapshot_json={},
                        )
                    )
                    await writer_db.commit()

            try:
                migration = asyncio.create_task(
                    asyncio.to_thread(command.upgrade, config, "20260827_07")
                )
                await wait_for_advisory_lock_counts(
                    schema, BARRIER_LOCK_KEY, granted_at_least=1, waiting_at_least=1
                )
                writer = asyncio.create_task(write_high_review())
                await wait_for_export_batches_lock_counts(
                    schema,
                    "ShareUpdateExclusiveLock",
                    waiting_at_least=1,
                )
                await release_barrier(barrier_connection, barrier_transaction)
                barrier_connection = barrier_transaction = None
                await asyncio.wait_for(migration, timeout=10)
                await asyncio.wait_for(writer, timeout=10)
            finally:
                if barrier_connection is not None:
                    await release_barrier(barrier_connection, barrier_transaction)
                pending = [task for task in (writer, migration) if task is not None]
                if pending:
                    await asyncio.wait_for(
                        asyncio.gather(*pending, return_exceptions=True), timeout=20
                    )
            await assert_strict_candidate_epoch(engine, candidate_id)
            await engine.dispose()

        asyncio.run(race_migration_first())
    finally:
        asyncio.run(drop_migration_schema(schema))


def test_postgres_epoch_receipt_first_has_no_table_lock_upgrade_deadlock():
    """Receipt-first must make migration wait before its conflicting table lock."""

    schema = asyncio.run(create_migration_schema())
    config = migration_config(schema)
    try:
        command.upgrade(config, "20260827_06")

        async def race_receipt_first():
            engine = migration_engine(schema)
            mao_id, candidate_id = await seed(engine)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as setup:
                created = await create_export_batch(setup, mao_id, None, SECRET)
                item = await setup.scalar(
                    select(ExportItem).where(ExportItem.batch_id == created.batch.id)
                )
                assert item is not None
                batch_id = created.batch.id
                payload = ReceiptItem(
                    candidate_id=item.candidate_id,
                    review_id=item.review_id,
                    review_version=item.review_version,
                    status="SUCCEEDED",
                    sha256="f" * 64,
                    relative_path=item.target_relative_path,
                )
                token = receipt_token(batch_id, SECRET)
            await install_advisory_barrier_trigger(schema, "export_batches")
            barrier_connection, barrier_transaction = await acquire_barrier(engine)
            receipt_task = migration = None
            receipt_result = None
            try:
                async with factory() as receipt_db:
                    receipt_task = asyncio.create_task(
                        apply_receipt(
                            receipt_db, batch_id, [payload], raw_token=token
                        )
                    )
                    await wait_for_advisory_lock_counts(
                        schema, BARRIER_LOCK_KEY, granted_at_least=1, waiting_at_least=1
                    )
                    migration = asyncio.create_task(
                        asyncio.to_thread(command.upgrade, config, "20260827_07")
                    )
                    await wait_for_export_batches_lock_counts(
                        schema,
                        "ShareRowExclusiveLock",
                        waiting_at_least=1,
                    )
                    assert (
                        await export_batches_lock_count(
                            schema, "ShareRowExclusiveLock"
                        )
                        == 0
                    )
                    await release_barrier(barrier_connection, barrier_transaction)
                    barrier_connection = barrier_transaction = None
                    receipt_result = await asyncio.wait_for(receipt_task, timeout=10)
                    await asyncio.wait_for(migration, timeout=10)
            finally:
                if barrier_connection is not None:
                    await release_barrier(barrier_connection, barrier_transaction)
                pending = [task for task in (receipt_task, migration) if task is not None]
                if pending:
                    await asyncio.wait_for(
                        asyncio.gather(*pending, return_exceptions=True), timeout=20
                    )
            async with factory() as verify:
                batch = await verify.get(ExportBatch, batch_id)
            assert receipt_result is not None and receipt_result.status == "completed"
            assert batch is not None and batch.status == "completed"
            await assert_strict_candidate_epoch(engine, candidate_id)
            await engine.dispose()

        asyncio.run(race_receipt_first())
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
            newer_id = uuid4()
            async with factory() as db:
                now = utc_now()
                created = await create_export_batch(db, mao_id, None, SECRET)
                assert created.batch is not None
                oldest_id = created.batch.id
                oldest = await db.get(ExportBatch, oldest_id)
                assert oldest is not None
                oldest.created_at = now
                db.add(
                    ExportBatch(
                        id=newer_id,
                        created_by_id=mao_id,
                        scope_key="ALL",
                        receipt_token_hash="2" * 64,
                        status="pending",
                        expires_at=now + timedelta(days=1),
                        created_at=now + timedelta(seconds=1),
                    )
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
