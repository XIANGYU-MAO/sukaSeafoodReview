import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.models import Base, Candidate, IdempotencyCommand, Review, ReviewRevision, Species, User
from app.schemas.review import DecisionRequest, ReviewFilters
from app.services.pool import get_or_open_current
from app.services.reviews import submit_decision
from tests.review_support import candidate_record


POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="TEST_POSTGRES_URL is required for real PostgreSQL concurrency tests",
)


async def in_isolated_schema(operation):
    schema = f"task4_{uuid4().hex}"
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


async def seed(engine, candidate_count):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        hassan = User(
            name="Hassan",
            role="reviewer",
            password_hash="test",
            must_change_password=False,
        )
        mao = User(
            name="Mao",
            role="admin",
            password_hash="test",
            must_change_password=False,
        )
        species = Species(
            code="SF001",
            name_zh="测试鱼",
            name_en="Test fish",
            scientific_name="Piscis probatio",
        )
        db.add_all([hassan, mao, species])
        await db.flush()
        candidates = [
            candidate_record(species.id, number)
            for number in range(1, candidate_count + 1)
        ]
        db.add_all(candidates)
        await db.commit()
        return hassan.id, mao.id, tuple(candidate.id for candidate in candidates)


def test_same_user_concurrent_acquisition_leaves_exactly_one_assignment():
    async def operation(engine):
        hassan_id, _, _ = await seed(engine, 2)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as first, factory() as second:
            results = await asyncio.gather(
                get_or_open_current(first, hassan_id, ReviewFilters()),
                get_or_open_current(second, hassan_id, ReviewFilters()),
            )
        async with factory() as verification:
            assignment_count = await verification.scalar(
                select(func.count())
                .select_from(Candidate)
                .where(Candidate.current_reviewer_id == hassan_id)
            )
        return results, assignment_count

    results, assignment_count = asyncio.run(in_isolated_schema(operation))

    assert results[0].id == results[1].id
    assert assignment_count == 1


def test_skip_locked_immediately_bypasses_a_candidate_held_by_another_transaction():
    async def operation(engine):
        _, mao_id, _ = await seed(engine, 2)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as lock_session, factory() as acquisition_session:
            locked_candidate = await lock_session.scalar(
                select(Candidate)
                .order_by(Candidate.id)
                .limit(1)
                .with_for_update()
            )
            assert locked_candidate is not None
            locked_candidate_id = locked_candidate.id
            assert lock_session.in_transaction()
            try:
                acquired = await asyncio.wait_for(
                    get_or_open_current(
                        acquisition_session, mao_id, ReviewFilters()
                    ),
                    timeout=1.0,
                )
            finally:
                await lock_session.rollback()
        return locked_candidate_id, acquired

    locked_candidate_id, acquired = asyncio.run(in_isolated_schema(operation))

    assert acquired is not None
    assert acquired.id != locked_candidate_id


def test_skip_locked_immediately_returns_empty_when_only_candidate_is_locked():
    async def operation(engine):
        _, mao_id, _ = await seed(engine, 1)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as lock_session, factory() as acquisition_session:
            locked_candidate = await lock_session.scalar(
                select(Candidate).limit(1).with_for_update()
            )
            assert locked_candidate is not None
            assert lock_session.in_transaction()
            try:
                return await asyncio.wait_for(
                    get_or_open_current(
                        acquisition_session, mao_id, ReviewFilters()
                    ),
                    timeout=1.0,
                )
            finally:
                await lock_session.rollback()

    result = asyncio.run(in_isolated_schema(operation))

    assert result is None


def test_concurrent_identical_submission_retries_create_one_command_and_review():
    async def operation(engine):
        hassan_id, _, _ = await seed(engine, 1)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            candidate = await get_or_open_current(db, hassan_id, ReviewFilters())
        async with factory() as first, factory() as second:
            results = await asyncio.gather(
                submit_decision(
                    first,
                    hassan_id,
                    candidate.id,
                    "concurrent-repeat",
                    DecisionRequest(decision="APPROVED"),
                ),
                submit_decision(
                    second,
                    hassan_id,
                    candidate.id,
                    "concurrent-repeat",
                    DecisionRequest(decision="APPROVED"),
                ),
            )
        async with factory() as verification:
            counts = (
                await verification.scalar(select(func.count()).select_from(Review)),
                await verification.scalar(
                    select(func.count()).select_from(ReviewRevision)
                ),
                await verification.scalar(
                    select(func.count()).select_from(IdempotencyCommand)
                ),
            )
        return results, counts

    results, counts = asyncio.run(in_isolated_schema(operation))

    assert results[0].review.id == results[1].review.id
    assert results[0].response_json == results[1].response_json
    assert counts == (1, 1, 1)
