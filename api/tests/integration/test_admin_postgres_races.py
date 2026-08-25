import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import AuditEvent, Base, Candidate, Species, User
from app.schemas.admin import SpeciesPatchRequest
from app.schemas.review import ReviewFilters
from app.services.admin import AdminConflict, patch_species
from app.services.auth import utc_now
from app.services.pool import get_or_open_current
from tests.review_support import candidate_record


POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="TEST_POSTGRES_URL is required for real PostgreSQL concurrency tests",
)


async def in_isolated_schema(operation):
    schema = f"task6_{uuid4().hex}"
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
        reviewer = User(
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
        db.add_all([reviewer, mao, species])
        await db.flush()
        candidate = candidate_record(species.id, 1)
        db.add(candidate)
        await db.commit()
        return reviewer.id, mao.id, species.id, candidate.id


def test_pool_waits_for_species_disable_then_returns_none():
    async def operation(engine):
        reviewer_id, _, species_id, candidate_id = await seed(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as disable, factory() as pool:
            species = await disable.scalar(
                select(Species)
                .where(Species.id == species_id)
                .with_for_update()
            )
            species.active = False
            await disable.flush()
            pool_task = asyncio.create_task(
                get_or_open_current(pool, reviewer_id, ReviewFilters())
            )
            blocked = False
            try:
                try:
                    await asyncio.wait_for(asyncio.shield(pool_task), timeout=0.25)
                except TimeoutError:
                    blocked = True
            finally:
                await disable.commit()
            result = await asyncio.wait_for(pool_task, timeout=2.0)

        async with factory() as verification:
            candidate = await verification.get(Candidate, candidate_id)
        return blocked, result, candidate.current_reviewer_id

    blocked, result, current_reviewer_id = asyncio.run(
        in_isolated_schema(operation)
    )

    assert blocked is True
    assert result is None
    assert current_reviewer_id is None


def test_species_disable_waits_for_shared_assignment_then_conflicts():
    async def operation(engine):
        reviewer_id, mao_id, species_id, candidate_id = await seed(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as assignment, factory() as disable:
            await assignment.scalar(
                select(User).where(User.id == reviewer_id).with_for_update()
            )
            candidate = await assignment.scalar(
                select(Candidate)
                .where(Candidate.id == candidate_id)
                .with_for_update()
            )
            species = await assignment.scalar(
                select(Species)
                .where(Species.id == species_id)
                .with_for_update(read=True)
            )
            assert species.active is True
            candidate.current_reviewer_id = reviewer_id
            candidate.current_started_at = utc_now()
            await assignment.flush()

            disable_task = asyncio.create_task(
                patch_species(
                    disable,
                    mao_id,
                    species_id,
                    SpeciesPatchRequest(
                        active=False,
                        reason="retire species",
                    ),
                )
            )
            blocked = False
            try:
                try:
                    await asyncio.wait_for(
                        asyncio.shield(disable_task), timeout=0.25
                    )
                except TimeoutError:
                    blocked = True
            finally:
                await assignment.commit()

            conflict_code = None
            try:
                await asyncio.wait_for(disable_task, timeout=2.0)
            except AdminConflict as exc:
                conflict_code = exc.code

        async with factory() as verification:
            species = await verification.get(Species, species_id)
            candidate = await verification.get(Candidate, candidate_id)
            audit_count = await verification.scalar(
                select(func.count()).select_from(AuditEvent)
            )
        return (
            blocked,
            conflict_code,
            species.active,
            candidate.current_reviewer_id,
            audit_count,
        )

    blocked, conflict_code, active, current_reviewer_id, audit_count = asyncio.run(
        in_isolated_schema(operation)
    )

    assert blocked is True
    assert conflict_code == "SPECIES_HAS_OPEN_CANDIDATE"
    assert active is True
    assert current_reviewer_id is not None
    assert audit_count == 0
