from __future__ import annotations

import asyncio
from datetime import timedelta
import os
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import AuditEvent, Base, Candidate, Decision, ExportBatch, ExportItem, Review, Species, User
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
                    receipt_secret=SECRET,
                    raw_token=token,
                ),
                apply_receipt(
                    second,
                    batch_id,
                    [payload],
                    receipt_secret=SECRET,
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
