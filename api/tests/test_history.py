import asyncio
import importlib
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.main import create_app
from app.models import Base, Candidate, Decision, IdempotencyCommand, Review, ReviewRevision
from tests.review_support import candidate_record, review_headers, seed_review_database


FIXED_NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


async def seed_history_database(settings, *, must_change_password=False):
    engine = create_async_engine(settings.DATABASE_URL)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()
    seed = await seed_review_database(
        settings.DATABASE_URL,
        settings,
        candidate_count=5,
        must_change_password=must_change_password,
    )
    engine = create_async_engine(settings.DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        candidates = (
            await db.scalars(select(Candidate).order_by(Candidate.source_record_id))
        ).all()
        candidates[1].species_id = seed.other_species_id
        candidates[1].source_dataset = "Wikimedia"
        reviews = [
            Review(
                candidate_id=candidates[0].id,
                reviewer_id=seed.hassan_id,
                decision=Decision.APPROVED,
                notes="first",
                whole_fish="YES",
                exact_species_verified="YES",
                created_at=FIXED_NOW - timedelta(days=1),
            ),
            Review(
                candidate_id=candidates[1].id,
                reviewer_id=seed.hassan_id,
                decision=Decision.REJECTED,
                rejection_reason="WRONG_SPECIES",
                notes="old attempt",
                whole_fish="REVIEW",
                exact_species_verified="NO",
                is_current=False,
                created_at=FIXED_NOW - timedelta(days=2),
            ),
            Review(
                candidate_id=candidates[2].id,
                reviewer_id=seed.hassan_id,
                decision=Decision.UNSURE,
                notes=None,
                whole_fish="REVIEW",
                exact_species_verified="REVIEW",
                created_at=FIXED_NOW,
            ),
            Review(
                candidate_id=candidates[3].id,
                reviewer_id=seed.mao_id,
                decision=Decision.REJECTED,
                rejection_reason="DUPLICATE",
                notes="Mao private note",
                whole_fish="REVIEW",
                exact_species_verified="REVIEW",
                created_at=FIXED_NOW + timedelta(hours=1),
            ),
        ]
        db.add_all(reviews)
        await db.commit()
        result = {
            "seed": seed,
            "hassan_reviews": reviews[:3],
            "mao_review": reviews[3],
        }
    await engine.dispose()
    return result


def test_history_lists_only_self_with_deterministic_sort_and_read_only_old_attempt(settings):
    data = asyncio.run(seed_history_database(settings))
    seed = data["seed"]

    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/v1/history", headers=review_headers(seed.hassan_token)
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert [item["decision"] for item in payload["items"]] == [
        "UNSURE",
        "APPROVED",
        "REJECTED",
    ]
    assert [item["read_only"] for item in payload["items"]] == [False, False, True]
    assert {item["reviewer_id"] for item in payload["items"]} == {str(seed.hassan_id)}
    assert all(item["notes"] != "Mao private note" for item in payload["items"])
    assert payload["items"][2]["species"]["code"] == "SF002"
    assert payload["items"][2]["source_dataset"] == "Wikimedia"


@pytest.mark.parametrize(
    ("params", "expected_total", "expected_decisions"),
    [
        ({"species_code": "SF002"}, 1, ["REJECTED"]),
        ({"source_dataset": "Wikimedia"}, 1, ["REJECTED"]),
        ({"decision": "APPROVED"}, 1, ["APPROVED"]),
        ({"date_from": "2026-08-25", "date_to": "2026-08-26"}, 2, ["UNSURE", "APPROVED"]),
        ({"limit": 1, "offset": 1}, 3, ["APPROVED"]),
    ],
)
def test_history_filters_and_paginates_with_filtered_total_contract(
    settings, params, expected_total, expected_decisions
):
    data = asyncio.run(seed_history_database(settings))
    seed = data["seed"]

    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/v1/history", params=params, headers=review_headers(seed.hassan_token)
        )

    assert response.status_code == 200
    assert response.json()["total"] == expected_total
    assert [item["decision"] for item in response.json()["items"]] == expected_decisions


def test_history_facets_are_owned_unique_deterministic_and_ignore_active_filters_and_page(settings):
    data = asyncio.run(seed_history_database(settings))
    seed = data["seed"]

    with TestClient(create_app(settings)) as client:
        hassan = client.get(
            "/v1/history",
            params={"decision": "APPROVED", "limit": 1, "offset": 0},
            headers=review_headers(seed.hassan_token),
        )
        mao = client.get(
            "/v1/history",
            params={"decision": "APPROVED", "limit": 1, "offset": 0},
            headers=review_headers(seed.mao_token),
        )

    assert hassan.status_code == mao.status_code == 200
    assert hassan.json()["total"] == 1
    assert hassan.json()["filters"] == {
        "species": [
            {
                "code": "SF001",
                "name_zh": "测试鱼",
                "name_en": "Test fish",
                "scientific_name": "Piscis probatio",
            },
            {
                "code": "SF002",
                "name_zh": "其他鱼",
                "name_en": "Other fish",
                "scientific_name": "Piscis alter",
            },
        ],
        "sources": ["Wikimedia", "iNaturalist"],
    }
    # Three owned rows collapse to catalog-cardinality facets, while Mao's
    # private activity cannot add a source/species to Hassan or vice versa.
    assert len(hassan.json()["filters"]["species"]) == 2
    assert len(hassan.json()["filters"]["sources"]) == 2
    assert mao.json()["filters"] == {
        "species": [
            {
                "code": "SF001",
                "name_zh": "测试鱼",
                "name_en": "Test fish",
                "scientific_name": "Piscis probatio",
            }
        ],
        "sources": ["iNaturalist"],
    }


@pytest.mark.parametrize(
    "params",
    [
        {"date_from": "2026-08-27", "date_to": "2026-08-26"},
        {"decision": "NOT_A_DECISION"},
        {"species_code": "x" * 33},
        {"source_dataset": "x" * 129},
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
    ],
)
def test_history_rejects_invalid_filters(settings, params):
    data = asyncio.run(seed_history_database(settings))
    seed = data["seed"]
    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/v1/history", params=params, headers=review_headers(seed.hassan_token)
        )
    assert response.status_code == 422


def test_history_rejects_overflowing_date_to_and_accepts_safe_upper_bound(settings):
    data = asyncio.run(seed_history_database(settings))
    seed = data["seed"]
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        overflowing = client.get(
            "/v1/history",
            params={"date_to": "9999-12-31"},
            headers=review_headers(seed.hassan_token),
        )
        safe = client.get(
            "/v1/history",
            params={"date_to": "9998-12-31"},
            headers=review_headers(seed.hassan_token),
        )

    assert overflowing.status_code == 422
    assert safe.status_code == 200


def test_history_reviewer_override_is_forbidden_even_for_mao(settings):
    data = asyncio.run(seed_history_database(settings))
    seed = data["seed"]
    with TestClient(create_app(settings)) as client:
        hassan = client.get(
            "/v1/history",
            params={"reviewer": "Mao"},
            headers=review_headers(seed.hassan_token),
        )
        mao = client.get(
            "/v1/history",
            params={"reviewer": "Hassan"},
            headers=review_headers(seed.mao_token),
        )
    assert hassan.status_code == mao.status_code == 403


def test_history_get_requires_auth_and_completed_first_password_change(settings):
    data = asyncio.run(seed_history_database(settings, must_change_password=True))
    seed = data["seed"]
    with TestClient(create_app(settings)) as client:
        unauthenticated = client.get("/v1/history")
        blocked = client.get(
            "/v1/history", headers=review_headers(seed.hassan_token)
        )
    assert unauthenticated.status_code == 401
    assert blocked.status_code == 403


def test_history_patch_requires_auth_csrf_completed_password_and_version(settings):
    data = asyncio.run(seed_history_database(settings))
    seed = data["seed"]
    review = data["hassan_reviews"][0]
    path = f"/v1/history/{review.id}"
    payload = {"version": review.version, "decision": "UNSURE"}
    with TestClient(create_app(settings)) as client:
        unauthenticated = client.patch(path, json=payload)
        missing_csrf = client.patch(
            path, json=payload, headers=review_headers(seed.hassan_token)
        )
        missing_version = client.patch(
            path,
            json={"decision": "UNSURE"},
            headers=review_headers(seed.hassan_token, seed.hassan_csrf),
        )

    async def require_password_change():
        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            user = await db.get(importlib.import_module("app.models").User, seed.hassan_id)
            user.must_change_password = True
            await db.commit()
        await engine.dispose()

    asyncio.run(require_password_change())
    with TestClient(create_app(settings)) as client:
        blocked = client.patch(
            path,
            json=payload,
            headers=review_headers(seed.hassan_token, seed.hassan_csrf),
        )

    assert unauthenticated.status_code == 401
    assert missing_csrf.status_code == 403
    assert missing_version.status_code == 422
    assert blocked.status_code == 403


@pytest.mark.parametrize(
    ("payload", "whole_fish", "exact_species"),
    [
        ({"version": 1, "decision": "APPROVED"}, "YES", "YES"),
        ({"version": 1, "decision": "UNSURE"}, "REVIEW", "REVIEW"),
        ({"version": 1, "decision": "REJECTED", "rejection_reason": "WRONG_SPECIES"}, "REVIEW", "NO"),
        ({"version": 1, "decision": "REJECTED", "rejection_reason": "NOT_WHOLE_FISH"}, "NO", "REVIEW"),
        ({"version": 1, "decision": "REJECTED", "rejection_reason": "OTHER", "notes": "  detail  "}, "REVIEW", "REVIEW"),
    ],
)
def test_history_patch_reuses_canonical_validation_and_fact_mapping(
    settings, payload, whole_fish, exact_species
):
    data = asyncio.run(seed_history_database(settings))
    seed = data["seed"]
    review = data["hassan_reviews"][0]
    with TestClient(create_app(settings)) as client:
        response = client.patch(
            f"/v1/history/{review.id}",
            json=payload,
            headers=review_headers(seed.hassan_token, seed.hassan_csrf),
        )
    assert response.status_code == 200
    assert response.json()["whole_fish"] == whole_fish
    assert response.json()["exact_species_verified"] == exact_species
    if payload.get("notes"):
        assert response.json()["notes"] == "detail"


@pytest.mark.parametrize(
    "payload",
    [
        {"version": 1, "decision": "APPROVED", "rejection_reason": "DUPLICATE"},
        {"version": 1, "decision": "REJECTED"},
        {"version": 1, "decision": "REJECTED", "rejection_reason": "OTHER"},
        {"version": 1, "decision": "REJECTED", "rejection_reason": "OTHER", "notes": "   "},
        {"version": 0, "decision": "UNSURE"},
    ],
)
def test_history_patch_rejects_states_initial_submission_rejects(settings, payload):
    data = asyncio.run(seed_history_database(settings))
    seed = data["seed"]
    review = data["hassan_reviews"][0]
    with TestClient(create_app(settings)) as client:
        response = client.patch(
            f"/v1/history/{review.id}",
            json=payload,
            headers=review_headers(seed.hassan_token, seed.hassan_csrf),
        )
    assert response.status_code == 422


def test_history_patch_increments_once_and_appends_complete_before_after_revision(settings):
    data = asyncio.run(seed_history_database(settings))
    seed = data["seed"]
    review = data["hassan_reviews"][0]
    with TestClient(create_app(settings)) as client:
        response = client.patch(
            f"/v1/history/{review.id}",
            json={
                "version": 1,
                "decision": "REJECTED",
                "rejection_reason": "WRONG_SPECIES",
                "notes": " correction ",
            },
            headers=review_headers(seed.hassan_token, seed.hassan_csrf),
        )

    async def load_state():
        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            stored = await db.get(Review, review.id)
            revisions = (
                await db.scalars(
                    select(ReviewRevision).where(ReviewRevision.review_id == review.id)
                )
            ).all()
        await engine.dispose()
        return stored, revisions

    stored, revisions = asyncio.run(load_state())
    assert response.status_code == 200
    assert response.json()["version"] == stored.version == 2
    assert stored.reviewer_id == seed.hassan_id
    assert stored.is_current is True
    assert len(revisions) == 1
    revision = revisions[0]
    assert revision.actor_id == revision.reviewer_id == seed.hassan_id
    assert revision.candidate_id == review.candidate_id
    assert revision.review_version == 2
    assert revision.snapshot_json == {
        "before": {
            "candidate_id": str(review.candidate_id),
            "review_id": str(review.id),
            "reviewer_id": str(seed.hassan_id),
            "decision": "APPROVED",
            "rejection_reason": None,
            "notes": "first",
            "whole_fish": "YES",
            "exact_species_verified": "YES",
            "is_current": True,
            "version": 1,
        },
        "after": {
            "candidate_id": str(review.candidate_id),
            "review_id": str(review.id),
            "reviewer_id": str(seed.hassan_id),
            "decision": "REJECTED",
            "rejection_reason": "WRONG_SPECIES",
            "notes": "correction",
            "whole_fish": "REVIEW",
            "exact_species_verified": "NO",
            "is_current": True,
            "version": 2,
        },
    }


def test_history_patch_stale_version_returns_latest_and_makes_no_mutation(settings):
    data = asyncio.run(seed_history_database(settings))
    seed = data["seed"]
    review = data["hassan_reviews"][0]
    with TestClient(create_app(settings)) as client:
        response = client.patch(
            f"/v1/history/{review.id}",
            json={"version": 99, "decision": "UNSURE"},
            headers=review_headers(seed.hassan_token, seed.hassan_csrf),
        )

    async def load_state():
        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            stored = await db.get(Review, review.id)
            count = await db.scalar(
                select(func.count())
                .select_from(ReviewRevision)
                .where(ReviewRevision.review_id == review.id)
            )
        await engine.dispose()
        return stored, count

    stored, revision_count = asyncio.run(load_state())
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "STALE_REVIEW_VERSION"
    assert response.json()["detail"]["latest"]["version"] == 1
    assert response.json()["detail"]["latest"]["decision"] == "APPROVED"
    assert stored.version == 1
    assert stored.decision == Decision.APPROVED
    assert stored.notes == "first"
    assert revision_count == 0


def test_history_patch_hides_non_owned_ids_and_rejects_owned_non_current(settings):
    data = asyncio.run(seed_history_database(settings))
    seed = data["seed"]
    old_review = data["hassan_reviews"][1]
    mao_review = data["mao_review"]
    headers = review_headers(seed.hassan_token, seed.hassan_csrf)
    with TestClient(create_app(settings)) as client:
        old = client.patch(
            f"/v1/history/{old_review.id}",
            json={"version": 1, "decision": "UNSURE"},
            headers=headers,
        )
        other = client.patch(
            f"/v1/history/{mao_review.id}",
            json={"version": 1, "decision": "UNSURE"},
            headers=headers,
        )
        missing = client.patch(
            f"/v1/history/{uuid4()}",
            json={"version": 1, "decision": "UNSURE"},
            headers=headers,
        )
    assert old.status_code == 409
    assert old.json()["detail"]["code"] == "REVIEW_NOT_CURRENT"
    assert other.status_code == missing.status_code == 404
    assert other.json() == missing.json()


def test_two_tabs_using_same_version_allow_one_edit_and_one_conflict(settings):
    data = asyncio.run(seed_history_database(settings))
    seed = data["seed"]
    review = data["hassan_reviews"][0]
    headers = review_headers(seed.hassan_token, seed.hassan_csrf)
    with TestClient(create_app(settings)) as client:
        first = client.patch(
            f"/v1/history/{review.id}",
            json={"version": 1, "decision": "UNSURE"},
            headers=headers,
        )
        second = client.patch(
            f"/v1/history/{review.id}",
            json={"version": 1, "decision": "REJECTED", "rejection_reason": "DUPLICATE"},
            headers=headers,
        )
    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["detail"]["latest"] == first.json()


def test_history_edit_query_uses_postgresql_row_lock():
    history = importlib.import_module("app.services.history")
    statement = history.review_for_update_query(
        UUID("11111111-1111-1111-1111-111111111111"),
        UUID("22222222-2222-2222-2222-222222222222"),
    )
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE OF reviews" in compiled


def test_history_edit_rolls_back_when_revision_insert_fails(settings):
    data = asyncio.run(seed_history_database(settings))
    seed = data["seed"]
    review = data["hassan_reviews"][0]
    history = importlib.import_module("app.services.history")
    schema = importlib.import_module("app.schemas.history")

    async def exercise():
        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        def fail_revision_insert(*_):
            raise RuntimeError("forced edit revision failure")

        event.listen(ReviewRevision, "before_insert", fail_revision_insert)
        try:
            async with factory() as db:
                with pytest.raises(RuntimeError, match="forced edit revision failure"):
                    await history.edit_review(
                        db,
                        seed.hassan_id,
                        review.id,
                        schema.HistoryEditRequest(version=1, decision="UNSURE"),
                    )
        finally:
            event.remove(ReviewRevision, "before_insert", fail_revision_insert)
        async with factory() as db:
            stored = await db.get(Review, review.id)
            count = await db.scalar(
                select(func.count())
                .select_from(ReviewRevision)
                .where(ReviewRevision.review_id == review.id)
            )
        await engine.dispose()
        return stored, count

    stored, revision_count = asyncio.run(exercise())
    assert stored.version == 1
    assert stored.decision == Decision.APPROVED
    assert stored.notes == "first"
    assert revision_count == 0


def test_history_edit_does_not_change_persisted_idempotent_response(settings):
    async def create_database():
        engine = create_async_engine(settings.DATABASE_URL)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await engine.dispose()
        return await seed_review_database(settings.DATABASE_URL, settings, candidate_count=1)

    seed = asyncio.run(create_database())
    headers = {
        **review_headers(seed.hassan_token, seed.hassan_csrf),
        "Idempotency-Key": "history-replay",
    }
    with TestClient(create_app(settings)) as client:
        current = client.post("/v1/reviews/current", headers=headers)
        first = client.post(
            f"/v1/reviews/{current.json()['id']}/decision",
            json={"decision": "APPROVED"},
            headers=headers,
        )
        edited = client.patch(
            f"/v1/history/{first.json()['id']}",
            json={"version": 1, "decision": "UNSURE"},
            headers=headers,
        )
        replay = client.post(
            f"/v1/reviews/{current.json()['id']}/decision",
            json={"decision": "APPROVED"},
            headers=headers,
        )

    async def load_state():
        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            stored = await db.get(Review, UUID(first.json()["id"]))
            command = await db.scalar(select(IdempotencyCommand))
            revisions = await db.scalar(select(func.count()).select_from(ReviewRevision))
        await engine.dispose()
        return stored, command, revisions

    stored, command, revision_count = asyncio.run(load_state())
    assert first.status_code == 201
    assert edited.status_code == 200
    assert edited.json()["version"] == 2
    assert replay.status_code == 201
    assert replay.json() == first.json()
    assert replay.json()["decision"] == "APPROVED"
    assert replay.json()["version"] == 1
    assert stored.decision == Decision.UNSURE
    assert stored.version == 2
    assert command.response_json == first.json()
    assert revision_count == 2
