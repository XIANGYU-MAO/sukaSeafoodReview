import asyncio
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.main import create_app
from app.models import Base, Candidate, Decision, Review, Species
from app.schemas.review import ReviewFilters
from app.services.pool import eligible_candidate_query, get_or_open_current
from tests.review_support import candidate_record, review_headers, seed_review_database


async def create_database(settings, candidate_count=4):
    engine = create_async_engine(settings.DATABASE_URL)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()
    return await seed_review_database(
        settings.DATABASE_URL, settings, candidate_count=candidate_count
    )


def test_current_candidate_is_restored_across_sessions_and_changed_filters(settings):
    seed = asyncio.run(create_database(settings))

    async def exercise():
        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as first_session:
            first = await get_or_open_current(
                first_session, seed.hassan_id, ReviewFilters()
            )
        async with factory() as aging_session:
            assigned = await aging_session.get(Candidate, first.id)
            assigned.current_started_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
            await aging_session.commit()
        async with factory() as second_session:
            restored = await get_or_open_current(
                second_session,
                seed.hassan_id,
                ReviewFilters(species_code="DOES_NOT_MATCH", source_dataset="other"),
            )
        async with factory() as verification:
            persisted = await verification.scalar(
                select(Candidate).where(Candidate.id == first.id)
            )
        await engine.dispose()
        return first, restored, persisted

    first, restored, persisted = asyncio.run(exercise())

    assert restored.id == first.id
    assert persisted.current_reviewer_id == seed.hassan_id
    assert persisted.current_started_at is not None
    assert persisted.current_started_at.year == 2020


def test_postgres_candidate_selection_uses_skip_locked():
    statement = eligible_candidate_query(ReviewFilters())

    compiled = str(statement.compile(dialect=postgresql.dialect()))

    assert "FOR UPDATE OF candidates SKIP LOCKED" in compiled


def test_api_restores_current_candidate_for_a_new_authenticated_request(settings):
    seed = asyncio.run(create_database(settings))
    with TestClient(create_app(settings)) as first_client:
        first = first_client.post(
            "/v1/reviews/current",
            headers=review_headers(seed.hassan_token, seed.hassan_csrf),
        )
    with TestClient(create_app(settings)) as refreshed_client:
        restored = refreshed_client.post(
            "/v1/reviews/current?species_code=SF999&source_dataset=other",
            headers=review_headers(seed.hassan_token, seed.hassan_csrf),
        )

    assert first.status_code == 200
    assert restored.status_code == 200
    assert restored.json()["id"] == first.json()["id"]
    assert set(restored.json()) == {
        "id",
        "species",
        "source_dataset",
        "source_record_id",
        "preview_url",
        "original_url",
        "source_url",
        "creator",
        "license",
        "license_url",
        "attribution",
        "location",
        "observed_on",
        "metadata",
    }


def test_pool_skips_ineligible_and_honors_filters(settings):
    seed = asyncio.run(create_database(settings, candidate_count=0))

    async def exercise():
        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            species = await db.get(Species, seed.species_id)
            inactive_species = Species(
                code="SF003",
                name_zh="停用鱼",
                name_en="Inactive fish",
                scientific_name="Piscis inactive",
                active=False,
            )
            db.add(inactive_species)
            await db.flush()
            inactive_candidate = candidate_record(species.id, 10, active=False)
            inactive_species_candidate = candidate_record(inactive_species.id, 11)
            held = candidate_record(species.id, 12)
            held.current_reviewer_id = seed.mao_id
            reviewed = candidate_record(species.id, 13)
            wrong_species = candidate_record(seed.other_species_id, 14)
            wrong_source = candidate_record(
                species.id, 15, source_dataset="Wikimedia"
            )
            eligible = candidate_record(species.id, 16)
            db.add_all(
                [
                    inactive_candidate,
                    inactive_species_candidate,
                    held,
                    reviewed,
                    wrong_species,
                    wrong_source,
                    eligible,
                ]
            )
            await db.flush()
            db.add(
                Review(
                    candidate_id=reviewed.id,
                    reviewer_id=seed.mao_id,
                    decision=Decision.UNSURE,
                    whole_fish="REVIEW",
                    exact_species_verified="REVIEW",
                )
            )
            await db.commit()

        async with factory() as db:
            result = await get_or_open_current(
                db,
                seed.hassan_id,
                ReviewFilters(species_code="SF001", source_dataset="iNaturalist"),
            )
        await engine.dispose()
        return result, eligible

    result, eligible = asyncio.run(exercise())

    assert result.id == eligible.id


def test_empty_pool_returns_documented_no_content_response(settings):
    seed = asyncio.run(create_database(settings, candidate_count=0))
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/v1/reviews/current",
            headers=review_headers(seed.hassan_token, seed.hassan_csrf),
        )

    assert response.status_code == 204
    assert response.content == b""


def test_current_route_requires_auth_csrf_and_completed_first_password_change(settings):
    async def setup():
        engine = create_async_engine(settings.DATABASE_URL)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await engine.dispose()
        return await seed_review_database(
            settings.DATABASE_URL,
            settings,
            candidate_count=1,
            must_change_password=True,
        )

    seed = asyncio.run(setup())
    with TestClient(create_app(settings)) as client:
        unauthenticated = client.post("/v1/reviews/current")
        missing_csrf = client.post(
            "/v1/reviews/current", headers=review_headers(seed.hassan_token)
        )
        password_change_required = client.post(
            "/v1/reviews/current",
            headers=review_headers(seed.hassan_token, seed.hassan_csrf),
        )
        mao = client.post(
            "/v1/reviews/current",
            headers=review_headers(seed.mao_token, seed.mao_csrf),
        )

    assert unauthenticated.status_code == 401
    assert missing_csrf.status_code == 403
    assert password_change_required.status_code == 403
    assert mao.status_code == 200
