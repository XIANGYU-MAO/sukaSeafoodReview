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
    CandidateImportPreview,
    Session,
    Species,
    User,
)
from app.services.auth import session_digest, utc_now
from app.services.imports import ImportConflict, commit_candidate_csv, stage_candidate_csv


POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="TEST_POSTGRES_URL is required for real PostgreSQL import race tests",
)


CONTENT = (
    "seafood_code,source_dataset,source_record_id,source_url,image_url,license\n"
    "SF001,INATURALIST,obs:100/photo:200,"
    "https://www.inaturalist.org/observations/100,"
    "https://inaturalist-open-data.s3.amazonaws.com/photos/200/large.jpg,"
    "CC-BY-4.0\n"
).encode()


async def in_isolated_schema(operation):
    schema = f"task7_import_{uuid4().hex}"
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


async def seed_import_actor(engine):
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
        actor_session = Session(
            user_id=mao.id,
            token_hash=session_digest("task7-postgres-session"),
            password_version=mao.password_version,
            expires_at=utc_now() + timedelta(hours=12),
        )
        db.add(actor_session)
        await db.commit()
        return mao.id, actor_session.id


async def stage(engine, actor_id, actor_session_id, filename):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        return await stage_candidate_csv(
            db,
            CONTENT,
            actor_id=actor_id,
            actor_session_id=actor_session_id,
            filename=filename,
        )


async def race_commits(engine, actor_id, actor_session_id, tokens):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    entered = [asyncio.Event() for _ in tokens]

    async def one(index, token):
        async with factory() as db:
            entered[index].set()
            try:
                result = await commit_candidate_csv(
                    db,
                    token,
                    actor_id,
                    actor_session_id=actor_session_id,
                )
                return "success", result.model_dump(mode="json")
            except ImportConflict as exc:
                return "conflict", exc.code

    tasks = []
    blocked = []
    async with factory() as barrier:
        await barrier.scalar(select(User).where(User.id == actor_id).with_for_update())
        tasks = [
            asyncio.create_task(one(index, token))
            for index, token in enumerate(tokens)
        ]
        await asyncio.gather(*(event.wait() for event in entered))
        for task in tasks:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=0.25)
                blocked.append(False)
            except TimeoutError:
                blocked.append(True)
        await barrier.rollback()
    return blocked, await asyncio.gather(*tasks)


async def persisted_state(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        stages = list(
            (
                await db.scalars(
                    select(CandidateImportPreview).order_by(CandidateImportPreview.filename)
                )
            ).all()
        )
        return (
            int(await db.scalar(select(func.count()).select_from(Candidate)) or 0),
            int(await db.scalar(select(func.count()).select_from(AuditEvent)) or 0),
            stages,
        )


def test_same_preview_token_postgres_race_returns_exact_result_and_one_write():
    async def operation(engine):
        actor_id, actor_session_id = await seed_import_actor(engine)
        preview = await stage(engine, actor_id, actor_session_id, "same.csv")
        blocked, results = await race_commits(
            engine,
            actor_id,
            actor_session_id,
            [preview.preview_token, preview.preview_token],
        )
        return blocked, results, await persisted_state(engine)

    blocked, results, (candidate_count, audit_count, stages) = asyncio.run(
        in_isolated_schema(operation)
    )

    assert blocked == [True, True]
    assert results[0][0] == results[1][0] == "success"
    assert results[0][1] == results[1][1]
    assert candidate_count == audit_count == 1
    assert len(stages) == 1
    assert stages[0].content is None
    assert stages[0].result_json == results[0][1]


def test_distinct_preview_tokens_same_identity_postgres_race_has_stable_loser():
    async def operation(engine):
        actor_id, actor_session_id = await seed_import_actor(engine)
        first = await stage(engine, actor_id, actor_session_id, "a.csv")
        second = await stage(engine, actor_id, actor_session_id, "b.csv")
        blocked, results = await race_commits(
            engine,
            actor_id,
            actor_session_id,
            [first.preview_token, second.preview_token],
        )
        return blocked, results, await persisted_state(engine)

    blocked, results, (candidate_count, audit_count, stages) = asyncio.run(
        in_isolated_schema(operation)
    )

    assert blocked == [True, True]
    assert sorted(result[0] for result in results) == ["conflict", "success"]
    assert [result[1] for result in results if result[0] == "conflict"] == [
        "IMPORT_PREVIEW_STALE"
    ]
    assert candidate_count == audit_count == 1
    assert sum(item.content is None for item in stages) == 1
    assert sum(item.committed_at is not None for item in stages) == 1
    assert sum(item.result_json is not None for item in stages) == 1
