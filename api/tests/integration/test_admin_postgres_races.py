import asyncio
import os
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    AuditEvent,
    Base,
    Candidate,
    Decision,
    ExportBatch,
    Review,
    ReviewRevision,
    Species,
    User,
)
from app.schemas.admin import (
    CandidatePatchRequest,
    ReopenRequest,
    SpeciesPatchRequest,
    TransferRequest,
)
from app.schemas.review import ReviewFilters
from app.services.admin import (
    AdminConflict,
    patch_candidate,
    patch_species,
    reopen_review,
    list_admin_sources,
    transfer_current,
)
from app.services.auth import utc_now
from app.services.exports import list_batches
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


async def seed_admin_assignment_paths(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        users = {
            name: User(
                name=name,
                role="admin" if name == "Mao" else "reviewer",
                password_hash="test",
                must_change_password=False,
            )
            for name in ("Hassan", "Wahid", "Xinhui", "Sharmaa", "Mao")
        }
        source_species = Species(
            code="SF001",
            name_zh="测试鱼",
            name_en="Test fish",
            scientific_name="Piscis probatio",
        )
        target_species = Species(
            code="SF002",
            name_zh="目标鱼",
            name_en="Target fish",
            scientific_name="Piscis destinatio",
        )
        db.add_all([*users.values(), source_species, target_species])
        await db.flush()

        candidates = [candidate_record(source_species.id, index) for index in range(1, 6)]
        candidates[0].current_reviewer_id = users["Hassan"].id
        candidates[0].current_started_at = utc_now()
        candidates[3].current_reviewer_id = users["Sharmaa"].id
        candidates[3].current_started_at = utc_now()
        candidates[4].current_reviewer_id = users["Wahid"].id
        candidates[4].current_started_at = utc_now()
        db.add_all(candidates)
        await db.flush()

        reviews = []
        for candidate in candidates[1:3]:
            reviews.append(
                Review(
                    candidate_id=candidate.id,
                    reviewer_id=users["Hassan"].id,
                    decision=Decision.APPROVED,
                    whole_fish="YES",
                    exact_species_verified="YES",
                    is_current=True,
                    version=1,
                )
            )
        db.add_all(reviews)
        await db.flush()
        result = {
            "users": {name: user.id for name, user in users.items()},
            "source_species": source_species.id,
            "target_species": target_species.id,
            "candidates": [candidate.id for candidate in candidates],
            "reviews": [review.id for review in reviews],
        }
        await db.commit()
        return result


async def assert_waits_for_species_deactivation(
    factory,
    species_id,
    operation,
):
    async with factory() as holder:
        species = await holder.scalar(
            select(Species).where(Species.id == species_id).with_for_update()
        )
        species.active = False
        await holder.flush()
        operation_task = asyncio.create_task(operation())
        blocked = False
        try:
            try:
                await asyncio.wait_for(
                    asyncio.shield(operation_task), timeout=0.25
                )
            except TimeoutError:
                blocked = True
        finally:
            await holder.commit()

        conflict_code = None
        try:
            await asyncio.wait_for(operation_task, timeout=2.0)
        except AdminConflict as exc:
            conflict_code = exc.code
        return blocked, conflict_code


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


def test_transfer_current_waits_for_species_lock_then_rolls_back_inactive_conflict():
    async def operation(engine):
        seeded = await seed_admin_assignment_paths(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        candidate_id = seeded["candidates"][0]
        async with factory() as initial:
            initial_candidate = await initial.get(Candidate, candidate_id)
            initial_started_at = initial_candidate.current_started_at

        async def transfer():
            async with factory() as db:
                return await transfer_current(
                    db,
                    seeded["users"]["Mao"],
                    candidate_id,
                    TransferRequest(
                        version=1,
                        new_reviewer_id=seeded["users"]["Xinhui"],
                        reason="prove species lock",
                    ),
                )

        blocked, conflict_code = await assert_waits_for_species_deactivation(
            factory,
            seeded["source_species"],
            transfer,
        )
        async with factory() as verification:
            candidate = await verification.get(Candidate, candidate_id)
            audit_count = await verification.scalar(
                select(func.count()).select_from(AuditEvent)
            )
        return (
            blocked,
            conflict_code,
            candidate.current_reviewer_id == seeded["users"]["Hassan"],
            candidate.current_started_at == initial_started_at,
            candidate.version,
            audit_count,
        )

    result = asyncio.run(in_isolated_schema(operation))

    assert result == (True, "SPECIES_NOT_ACTIVE", True, True, 1, 0)


def test_reopen_review_waits_for_species_lock_then_preserves_review_on_conflict():
    async def operation(engine):
        seeded = await seed_admin_assignment_paths(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        candidate_id = seeded["candidates"][1]
        review_id = seeded["reviews"][0]
        async with factory() as initial:
            initial_candidate = await initial.get(Candidate, candidate_id)
            initial_started_at = initial_candidate.current_started_at

        async def reopen():
            async with factory() as db:
                return await reopen_review(
                    db,
                    seeded["users"]["Mao"],
                    review_id,
                    ReopenRequest(
                        candidate_version=1,
                        review_version=1,
                        new_reviewer_id=seeded["users"]["Xinhui"],
                        reason="prove species lock",
                    ),
                )

        blocked, conflict_code = await assert_waits_for_species_deactivation(
            factory,
            seeded["source_species"],
            reopen,
        )
        async with factory() as verification:
            candidate = await verification.get(Candidate, candidate_id)
            review = await verification.get(Review, review_id)
            revision_count = await verification.scalar(
                select(func.count()).select_from(ReviewRevision)
            )
            audit_count = await verification.scalar(
                select(func.count()).select_from(AuditEvent)
            )
        return (
            blocked,
            conflict_code,
            candidate.current_reviewer_id,
            candidate.current_started_at == initial_started_at,
            candidate.version,
            review.is_current,
            review.version,
            revision_count,
            audit_count,
        )

    result = asyncio.run(in_isolated_schema(operation))

    assert result == (
        True,
        "SPECIES_NOT_ACTIVE",
        None,
        True,
        1,
        True,
        1,
        0,
        0,
    )


def test_reviewed_candidate_patch_waits_for_target_species_then_rolls_back():
    async def operation(engine):
        seeded = await seed_admin_assignment_paths(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        candidate_id = seeded["candidates"][2]
        review_id = seeded["reviews"][1]
        async with factory() as initial:
            initial_candidate = await initial.get(Candidate, candidate_id)
            initial_started_at = initial_candidate.current_started_at

        async def change_species():
            async with factory() as db:
                return await patch_candidate(
                    db,
                    seeded["users"]["Mao"],
                    candidate_id,
                    CandidatePatchRequest(
                        version=1,
                        species_id=seeded["target_species"],
                        confirm_review_invalidation=True,
                        new_reviewer_id=seeded["users"]["Xinhui"],
                        reason="prove target species lock",
                    ),
                )

        blocked, conflict_code = await assert_waits_for_species_deactivation(
            factory,
            seeded["target_species"],
            change_species,
        )
        async with factory() as verification:
            candidate = await verification.get(Candidate, candidate_id)
            review = await verification.get(Review, review_id)
            revision_count = await verification.scalar(
                select(func.count()).select_from(ReviewRevision)
            )
            audit_count = await verification.scalar(
                select(func.count()).select_from(AuditEvent)
            )
        return (
            blocked,
            conflict_code,
            candidate.species_id == seeded["source_species"],
            candidate.current_reviewer_id,
            candidate.current_started_at == initial_started_at,
            candidate.version,
            review.is_current,
            review.version,
            revision_count,
            audit_count,
        )

    result = asyncio.run(in_isolated_schema(operation))

    assert result == (
        True,
        "SPECIES_NOT_ACTIVE",
        True,
        None,
        True,
        1,
        True,
        1,
        0,
        0,
    )


def test_concurrent_admin_transfers_to_one_target_return_success_and_stable_conflict():
    async def operation(engine):
        seeded = await seed_admin_assignment_paths(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        candidate_ids = seeded["candidates"][3:5]
        target_id = seeded["users"]["Xinhui"]
        entered = [asyncio.Event(), asyncio.Event()]

        async def transfer(index, candidate_id):
            async with factory() as db:
                try:
                    entered[index].set()
                    await transfer_current(
                        db,
                        seeded["users"]["Mao"],
                        candidate_id,
                        TransferRequest(
                            version=1,
                            new_reviewer_id=target_id,
                            reason="compete for one reviewer",
                        ),
                    )
                    return "success", None
                except AdminConflict as exc:
                    return "conflict", exc.code

        tasks = []
        blocked = []
        try:
            async with factory() as barrier:
                await barrier.scalar(
                    select(User)
                    .where(User.id == target_id)
                    .with_for_update()
                )
                tasks = [
                    asyncio.create_task(transfer(index, candidate_id))
                    for index, candidate_id in enumerate(candidate_ids)
                ]
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*(event.wait() for event in entered)),
                        timeout=1.0,
                    )
                    for task in tasks:
                        try:
                            await asyncio.wait_for(
                                asyncio.shield(task), timeout=0.25
                            )
                            blocked.append(False)
                        except TimeoutError:
                            blocked.append(True)
                finally:
                    if barrier.in_transaction():
                        await barrier.rollback()

            results = await asyncio.wait_for(
                asyncio.gather(*tasks),
                timeout=3.0,
            )
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        async with factory() as verification:
            candidates = [
                await verification.get(Candidate, candidate_id)
                for candidate_id in candidate_ids
            ]
            target_assignments = await verification.scalar(
                select(func.count())
                .select_from(Candidate)
                .where(Candidate.current_reviewer_id == target_id)
            )
            audit_count = await verification.scalar(
                select(func.count()).select_from(AuditEvent)
            )
        return blocked, results, candidates, target_assignments, audit_count

    blocked, results, candidates, target_assignments, audit_count = asyncio.run(
        in_isolated_schema(operation)
    )

    assert blocked == [True, True]
    assert sorted(results) == [
        ("conflict", "REVIEWER_NOT_ELIGIBLE"),
        ("success", None),
    ]
    assert target_assignments == 1
    assert sorted(candidate.version for candidate in candidates) == [1, 2]
    assert audit_count == 1


def test_admin_source_catalog_and_export_pagination_run_on_real_postgres():
    async def operation(engine):
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
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
            db.add_all([mao, species])
            await db.flush()
            db.add_all([
                candidate_record(species.id, 1, source_dataset="Wikimedia"),
                candidate_record(species.id, 2, source_dataset="iNaturalist"),
            ])
            now = utc_now()
            db.add_all([
                ExportBatch(
                    created_by_id=mao.id,
                    species_id=None,
                    scope_key="all",
                    receipt_token_hash=f"{index:064x}",
                    status="completed",
                    expires_at=now + timedelta(days=7),
                    completed_at=now,
                )
                for index in range(1, 22)
            ])
            await db.commit()
            sources = await list_admin_sources(db)
            total, batches = await list_batches(db, limit=10, offset=10)
            return sources.sources, total, len(batches)

    sources, total, page_size = asyncio.run(in_isolated_schema(operation))
    assert sources == ["iNaturalist", "Wikimedia"]
    assert (total, page_size) == (21, 10)
