from __future__ import annotations

import asyncio
from datetime import timedelta
import os
from uuid import uuid4

import pytest
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
    Species,
    User,
)
from app.services.auth import utc_now


POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="TEST_POSTGRES_URL is required for real PostgreSQL export races",
)
SECRET = "postgres-export-receipt-secret-is-unique-and-long"


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
