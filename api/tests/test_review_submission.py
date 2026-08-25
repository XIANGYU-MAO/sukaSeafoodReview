import asyncio

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.main import create_app
from app.models import (
    Base,
    Candidate,
    IdempotencyCommand,
    Review,
    ReviewRevision,
)
from app.schemas.review import DecisionRequest, RejectionReason, ReviewFilters
from app.services.pool import get_or_open_current
from app.services.reviews import (
    IdempotencyConflict,
    ReviewAssignmentConflict,
    submit_decision,
)
from tests.review_support import review_headers, seed_review_database


async def create_database(settings, *, must_change_password=False):
    engine = create_async_engine(settings.DATABASE_URL)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()
    return await seed_review_database(
        settings.DATABASE_URL,
        settings,
        candidate_count=3,
        must_change_password=must_change_password,
    )


@pytest.mark.parametrize(
    ("payload", "whole_fish", "exact_species"),
    [
        ({"decision": "APPROVED"}, "YES", "YES"),
        ({"decision": "UNSURE"}, "REVIEW", "REVIEW"),
        (
            {"decision": "REJECTED", "rejection_reason": "WRONG_SPECIES"},
            "REVIEW",
            "NO",
        ),
        (
            {"decision": "REJECTED", "rejection_reason": "NOT_WHOLE_FISH"},
            "NO",
            "REVIEW",
        ),
        (
            {"decision": "REJECTED", "rejection_reason": "TOO_OCCLUDED"},
            "REVIEW",
            "REVIEW",
        ),
        (
            {
                "decision": "REJECTED",
                "rejection_reason": "COOKED_OR_PROCESSED",
            },
            "REVIEW",
            "REVIEW",
        ),
        (
            {
                "decision": "REJECTED",
                "rejection_reason": "TOO_SMALL_OR_BLURRY",
            },
            "REVIEW",
            "REVIEW",
        ),
        (
            {"decision": "REJECTED", "rejection_reason": "DUPLICATE"},
            "REVIEW",
            "REVIEW",
        ),
        (
            {
                "decision": "REJECTED",
                "rejection_reason": "ARTWORK_OR_DIAGRAM",
            },
            "REVIEW",
            "REVIEW",
        ),
        (
            {
                "decision": "REJECTED",
                "rejection_reason": "LICENSE_OR_SOURCE_CONCERN",
            },
            "REVIEW",
            "REVIEW",
        ),
        (
            {
                "decision": "REJECTED",
                "rejection_reason": "IMAGE_URL_UNAVAILABLE",
            },
            "REVIEW",
            "REVIEW",
        ),
        (
            {
                "decision": "REJECTED",
                "rejection_reason": "OTHER",
                "notes": "test note",
            },
            "REVIEW",
            "REVIEW",
        ),
    ],
)
def test_decision_mapping_persists_only_supported_facts(
    settings, payload, whole_fish, exact_species
):
    seed = asyncio.run(create_database(settings))

    async def exercise():
        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            candidate = await get_or_open_current(
                db, seed.hassan_id, ReviewFilters()
            )
        async with factory() as db:
            review = await submit_decision(
                db,
                seed.hassan_id,
                candidate.id,
                "mapping-command",
                DecisionRequest.model_validate(payload),
            )
        await engine.dispose()
        return review

    review = asyncio.run(exercise())

    assert review.whole_fish == whole_fish
    assert review.exact_species_verified == exact_species


@pytest.mark.parametrize(
    "payload",
    [
        {"decision": "APPROVED", "rejection_reason": "TOO_OCCLUDED"},
        {"decision": "UNSURE", "rejection_reason": "TOO_OCCLUDED"},
        {"decision": "REJECTED"},
        {"decision": "REJECTED", "rejection_reason": "NOT_A_CODE"},
        {"decision": "REJECTED", "rejection_reason": "OTHER"},
        {
            "decision": "REJECTED",
            "rejection_reason": "OTHER",
            "notes": "   ",
        },
        {"decision": "APPROVED", "notes": "x" * 2001},
    ],
)
def test_invalid_decision_payloads_are_rejected_before_database_work(payload):
    with pytest.raises(ValidationError):
        DecisionRequest.model_validate(payload)


def test_other_notes_are_trimmed_and_stable_rejection_codes_are_complete():
    payload = DecisionRequest.model_validate(
        {"decision": "REJECTED", "rejection_reason": "OTHER", "notes": "  note  "}
    )

    assert payload.notes == "note"
    assert {reason.value for reason in RejectionReason} == {
        "WRONG_SPECIES",
        "NOT_WHOLE_FISH",
        "COOKED_OR_PROCESSED",
        "TOO_OCCLUDED",
        "TOO_SMALL_OR_BLURRY",
        "DUPLICATE",
        "ARTWORK_OR_DIAGRAM",
        "LICENSE_OR_SOURCE_CONCERN",
        "IMAGE_URL_UNAVAILABLE",
        "OTHER",
    }


def test_submission_is_atomic_and_next_request_advances(settings):
    seed = asyncio.run(create_database(settings))

    async def exercise():
        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            candidate = await get_or_open_current(db, seed.hassan_id, ReviewFilters())
        async with factory() as db:
            review = await submit_decision(
                db,
                seed.hassan_id,
                candidate.id,
                "atomic-command",
                DecisionRequest(decision="APPROVED"),
            )
        async with factory() as db:
            next_candidate = await get_or_open_current(
                db, seed.hassan_id, ReviewFilters()
            )
        async with factory() as db:
            stored_candidate = await db.get(Candidate, candidate.id)
            stored_review = await db.scalar(
                select(Review).where(Review.id == review.id)
            )
            revisions = (
                await db.scalars(
                    select(ReviewRevision).where(
                        ReviewRevision.review_id == review.id
                    )
                )
            ).all()
            commands = (
                await db.scalars(
                    select(IdempotencyCommand).where(
                        IdempotencyCommand.user_id == seed.hassan_id
                    )
                )
            ).all()
        await engine.dispose()
        return (
            candidate,
            next_candidate,
            stored_candidate,
            stored_review,
            revisions,
            commands,
        )

    candidate, next_candidate, stored_candidate, review, revisions, commands = (
        asyncio.run(exercise())
    )

    assert next_candidate.id != candidate.id
    assert stored_candidate.current_reviewer_id is None
    assert stored_candidate.current_started_at is None
    assert stored_candidate.version == 2
    assert review.is_current is True
    assert len(revisions) == 1
    assert revisions[0].snapshot_json == {
        "candidate_id": str(candidate.id),
        "review_id": str(review.id),
        "reviewer_id": str(seed.hassan_id),
        "decision": "APPROVED",
        "rejection_reason": None,
        "notes": None,
        "whole_fish": "YES",
        "exact_species_verified": "YES",
        "is_current": True,
        "version": 1,
    }
    assert len(commands) == 1
    assert commands[0].response_json["review_id"] == str(review.id)


def test_only_assigned_user_may_submit_and_retry_is_idempotent(settings):
    seed = asyncio.run(create_database(settings))

    async def exercise():
        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            candidate = await get_or_open_current(db, seed.hassan_id, ReviewFilters())
        async with factory() as db:
            with pytest.raises(ReviewAssignmentConflict):
                await submit_decision(
                    db,
                    seed.mao_id,
                    candidate.id,
                    "mao-command",
                    DecisionRequest(decision="APPROVED"),
                )
        async with factory() as db:
            first = await submit_decision(
                db,
                seed.hassan_id,
                candidate.id,
                "repeat-command",
                DecisionRequest(decision="UNSURE"),
            )
        async with factory() as db:
            replay = await submit_decision(
                db,
                seed.hassan_id,
                candidate.id,
                "repeat-command",
                DecisionRequest(decision="UNSURE"),
            )
        async with factory() as db:
            counts = (
                await db.scalar(select(func.count()).select_from(Review)),
                await db.scalar(select(func.count()).select_from(ReviewRevision)),
                await db.scalar(select(func.count()).select_from(IdempotencyCommand)),
            )
        await engine.dispose()
        return first, replay, counts

    first, replay, counts = asyncio.run(exercise())

    assert replay.id == first.id
    assert counts == (1, 1, 1)


def test_reusing_idempotency_key_for_different_request_conflicts_without_mutation(
    settings,
):
    seed = asyncio.run(create_database(settings))

    async def exercise():
        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            candidate = await get_or_open_current(db, seed.hassan_id, ReviewFilters())
        async with factory() as db:
            first = await submit_decision(
                db,
                seed.hassan_id,
                candidate.id,
                "conflict-command",
                DecisionRequest(decision="APPROVED"),
            )
        async with factory() as db:
            next_candidate = await get_or_open_current(
                db, seed.hassan_id, ReviewFilters()
            )
        async with factory() as db:
            with pytest.raises(IdempotencyConflict):
                await submit_decision(
                    db,
                    seed.hassan_id,
                    candidate.id,
                    "conflict-command",
                    DecisionRequest(decision="UNSURE"),
                )
        async with factory() as db:
            with pytest.raises(IdempotencyConflict):
                await submit_decision(
                    db,
                    seed.hassan_id,
                    next_candidate.id,
                    "conflict-command",
                    DecisionRequest(decision="APPROVED"),
                )
        async with factory() as db:
            counts = (
                await db.scalar(select(func.count()).select_from(Review)),
                await db.scalar(select(func.count()).select_from(ReviewRevision)),
                await db.scalar(select(func.count()).select_from(IdempotencyCommand)),
            )
            retained_assignment = await db.get(Candidate, next_candidate.id)
        await engine.dispose()
        return first, counts, retained_assignment

    first, counts, retained_assignment = asyncio.run(exercise())

    assert first.decision.value == "APPROVED"
    assert counts == (1, 1, 1)
    assert retained_assignment.current_reviewer_id == seed.hassan_id


def test_forced_database_failure_rolls_back_and_keeps_assignment(settings):
    seed = asyncio.run(create_database(settings))

    async def exercise():
        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            candidate = await get_or_open_current(db, seed.hassan_id, ReviewFilters())

        def fail_revision_insert(*_):
            raise RuntimeError("forced revision failure")

        event.listen(ReviewRevision, "before_insert", fail_revision_insert)
        try:
            async with factory() as db:
                with pytest.raises(RuntimeError, match="forced revision failure"):
                    await submit_decision(
                        db,
                        seed.hassan_id,
                        candidate.id,
                        "failed-command",
                        DecisionRequest(decision="APPROVED"),
                    )
        finally:
            event.remove(ReviewRevision, "before_insert", fail_revision_insert)

        async with factory() as db:
            stored_candidate = await db.get(Candidate, candidate.id)
            counts = (
                await db.scalar(select(func.count()).select_from(Review)),
                await db.scalar(select(func.count()).select_from(ReviewRevision)),
                await db.scalar(select(func.count()).select_from(IdempotencyCommand)),
            )
        await engine.dispose()
        return stored_candidate, counts

    candidate, counts = asyncio.run(exercise())

    assert candidate.current_reviewer_id == seed.hassan_id
    assert candidate.current_started_at is not None
    assert candidate.version == 1
    assert counts == (0, 0, 0)


def test_decision_api_validates_headers_auth_and_assignment(settings):
    seed = asyncio.run(create_database(settings))
    candidate_id = seed.candidate_ids[0]
    with TestClient(create_app(settings)) as client:
        unauthenticated = client.post(
            f"/v1/reviews/{candidate_id}/decision",
            headers={"Idempotency-Key": "api-command"},
            json={"decision": "APPROVED"},
        )
        missing_csrf = client.post(
            f"/v1/reviews/{candidate_id}/decision",
            headers={
                **review_headers(seed.hassan_token),
                "Idempotency-Key": "api-command",
            },
            json={"decision": "APPROVED"},
        )
        unassigned = client.post(
            f"/v1/reviews/{candidate_id}/decision",
            headers={
                **review_headers(seed.hassan_token, seed.hassan_csrf),
                "Idempotency-Key": "api-command",
            },
            json={"decision": "APPROVED"},
        )
        malformed_id = client.post(
            "/v1/reviews/not-a-uuid/decision",
            headers={
                **review_headers(seed.hassan_token, seed.hassan_csrf),
                "Idempotency-Key": "api-command",
            },
            json={"decision": "APPROVED"},
        )
        overlong_key = client.post(
            f"/v1/reviews/{candidate_id}/decision",
            headers={
                **review_headers(seed.hassan_token, seed.hassan_csrf),
                "Idempotency-Key": "x" * 256,
            },
            json={"decision": "APPROVED"},
        )

    assert unauthenticated.status_code == 401
    assert missing_csrf.status_code == 403
    assert unassigned.status_code == 409
    assert malformed_id.status_code == 422
    assert overlong_key.status_code == 422


def test_decision_api_rejects_first_password_change_required(settings):
    seed = asyncio.run(create_database(settings, must_change_password=True))
    with TestClient(create_app(settings)) as client:
        response = client.post(
            f"/v1/reviews/{seed.candidate_ids[0]}/decision",
            headers={
                **review_headers(seed.hassan_token, seed.hassan_csrf),
                "Idempotency-Key": "blocked-command",
            },
            json={"decision": "APPROVED"},
        )

    assert response.status_code == 403


def test_decision_api_replays_same_response_and_rejects_changed_request(settings):
    seed = asyncio.run(create_database(settings))
    with TestClient(create_app(settings)) as client:
        current = client.post(
            "/v1/reviews/current",
            headers=review_headers(seed.mao_token, seed.mao_csrf),
        )
        candidate_id = current.json()["id"]
        headers = {
            **review_headers(seed.mao_token, seed.mao_csrf),
            "Idempotency-Key": "api-repeat-command",
        }
        first = client.post(
            f"/v1/reviews/{candidate_id}/decision",
            headers=headers,
            json={"decision": "REJECTED", "rejection_reason": "DUPLICATE"},
        )
        replay = client.post(
            f"/v1/reviews/{candidate_id}/decision",
            headers=headers,
            json={"decision": "REJECTED", "rejection_reason": "DUPLICATE"},
        )
        conflict = client.post(
            f"/v1/reviews/{candidate_id}/decision",
            headers=headers,
            json={"decision": "UNSURE"},
        )

    assert current.status_code == 200
    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.json() == first.json()
    assert conflict.status_code == 409


def test_whitespace_and_missing_or_overlong_idempotency_keys_are_rejected(settings):
    seed = asyncio.run(create_database(settings))
    with TestClient(create_app(settings)) as client:
        base_headers = review_headers(seed.mao_token, seed.mao_csrf)
        responses = [
            client.post(
                f"/v1/reviews/{seed.candidate_ids[0]}/decision",
                headers={**base_headers, **extra},
                json={"decision": "APPROVED"},
            )
            for extra in (
                {},
                {"Idempotency-Key": "   "},
                {"Idempotency-Key": "x" * 256},
            )
        ]

    assert [response.status_code for response in responses] == [422, 422, 422]
