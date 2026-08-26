import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.main import create_app
from app.models import (
    AuditEvent,
    Candidate,
    Decision,
    Review,
    ReviewRevision,
    Session,
    Species,
    User,
)
from app.services.auth import verify_password
from tests.admin_support import admin_headers, seed_admin_database


FIXED_NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


async def mutate_database(settings, callback):
    engine = create_async_engine(settings.DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        result = await callback(db)
        await db.commit()
    await engine.dispose()
    return result


async def seed_current_database(settings):
    seed = await seed_admin_database(settings, candidate_count=6)

    async def arrange(db):
        candidates = [await db.get(Candidate, value) for value in seed.candidate_ids]
        candidates[0].current_reviewer_id = seed.user_ids["Hassan"]
        candidates[0].current_started_at = FIXED_NOW - timedelta(hours=2)
        candidates[1].current_reviewer_id = seed.user_ids["Wahid"]
        candidates[1].current_started_at = FIXED_NOW - timedelta(hours=1)
        reviews = [
            Review(
                candidate_id=candidates[2].id,
                reviewer_id=seed.user_ids["Hassan"],
                decision=Decision.APPROVED,
                notes="Hassan approved",
                whole_fish="YES",
                exact_species_verified="YES",
                is_current=True,
                version=1,
                created_at=FIXED_NOW - timedelta(days=1),
            ),
            Review(
                candidate_id=candidates[3].id,
                reviewer_id=seed.user_ids["Mao"],
                decision=Decision.REJECTED,
                rejection_reason="DUPLICATE",
                notes="Mao rejected",
                whole_fish="REVIEW",
                exact_species_verified="REVIEW",
                is_current=True,
                version=1,
                created_at=FIXED_NOW,
            ),
            Review(
                candidate_id=candidates[3].id,
                reviewer_id=seed.user_ids["Xinhui"],
                decision=Decision.UNSURE,
                notes="old attempt",
                whole_fish="REVIEW",
                exact_species_verified="REVIEW",
                is_current=False,
                version=2,
                created_at=FIXED_NOW - timedelta(days=2),
            ),
        ]
        db.add_all(reviews)
        await db.flush()
        return tuple(review.id for review in reviews)

    review_ids = await mutate_database(settings, arrange)
    return seed, review_ids


async def load_state(settings, candidate_id=None, review_id=None, user_id=None):
    engine = create_async_engine(settings.DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        candidate = await db.get(Candidate, candidate_id) if candidate_id else None
        review = await db.get(Review, review_id) if review_id else None
        user = await db.get(User, user_id) if user_id else None
        revisions = list((await db.scalars(select(ReviewRevision))).all())
        audits = list((await db.scalars(select(AuditEvent))).all())
        sessions = list((await db.scalars(select(Session))).all())
    await engine.dispose()
    return candidate, review, user, revisions, audits, sessions


def test_admin_user_directory_discovers_all_fixed_accounts_without_auth_state(
    settings,
):
    seed, _ = asyncio.run(seed_current_database(settings))
    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/v1/admin/users", headers=admin_headers(seed)
        )

    assert response.status_code == 200
    assert response.json()["total"] == 6
    assert [item["display_name"] for item in response.json()["items"]] == [
        "Hassan",
        "Mao",
        "Xinhui",
        "Wahid",
        "Sharmaa",
        "Yiming",
    ]
    assert [item["role"] for item in response.json()["items"]] == [
        "reviewer",
        "admin",
        "reviewer",
        "reviewer",
        "reviewer",
        "reviewer",
    ]
    assert all(item["active"] is True for item in response.json()["items"])
    assert {item["id"] for item in response.json()["items"]} == {
        str(value) for value in seed.user_ids.values()
    }
    lowered = response.text.lower()
    for forbidden in (
        "password",
        "hash",
        "session",
        "token",
        "failed_login",
        "locked_until",
    ):
        assert forbidden not in lowered


def test_zero_activity_users_are_discoverable_for_transfer_and_reset(settings):
    seed, _ = asyncio.run(seed_current_database(settings))
    with TestClient(create_app(settings)) as client:
        directory = client.get(
            "/v1/admin/users", headers=admin_headers(seed)
        )
        assert directory.status_code == 200
        by_name = {
            item["display_name"]: item["id"]
            for item in directory.json()["items"]
        }
        transfer = client.post(
            f"/v1/admin/current/{seed.candidate_ids[0]}/transfer",
            headers=admin_headers(seed, csrf=True),
            json={
                "version": 1,
                "new_reviewer_id": by_name["Sharmaa"],
                "reason": "use discoverable zero-activity reviewer",
            },
        )
        reset = client.post(
            f"/v1/admin/users/{by_name['Yiming']}/reset-password",
            headers=admin_headers(seed, csrf=True),
            json={"reason": "use discoverable zero-activity reviewer"},
        )

    candidate, _, yiming, _, audits, sessions = asyncio.run(
        load_state(
            settings,
            candidate_id=seed.candidate_ids[0],
            user_id=seed.user_ids["Yiming"],
        )
    )
    yiming_session = next(
        session for session in sessions if session.user_id == seed.user_ids["Yiming"]
    )
    assert directory.status_code == 200
    assert transfer.status_code == 200
    assert candidate.current_reviewer_id == seed.user_ids["Sharmaa"]
    assert reset.status_code == 200
    assert set(reset.json()) == {"temporary_password"}
    assert yiming.must_change_password is True
    assert yiming_session.revoked_at is not None
    assert [audit.action for audit in audits] == [
        "CURRENT_TRANSFER",
        "USER_PASSWORD_RESET",
    ]


def test_directory_uuid_can_reopen_for_a_zero_activity_user(settings):
    seed, review_ids = asyncio.run(seed_current_database(settings))
    review_id = review_ids[0]
    candidate_id = seed.candidate_ids[2]
    with TestClient(create_app(settings)) as client:
        directory = client.get(
            "/v1/admin/users", headers=admin_headers(seed)
        )
        assert directory.status_code == 200
        sharmaa_id = next(
            item["id"]
            for item in directory.json()["items"]
            if item["display_name"] == "Sharmaa"
        )
        response = client.post(
            f"/v1/admin/reviews/{review_id}/reopen",
            headers=admin_headers(seed, csrf=True),
            json={
                "candidate_version": 1,
                "review_version": 1,
                "new_reviewer_id": sharmaa_id,
                "reason": "use directory for zero-activity reviewer",
            },
        )

    candidate, review, _, revisions, audits, _ = asyncio.run(
        load_state(settings, candidate_id=candidate_id, review_id=review_id)
    )
    assert response.status_code == 200
    assert candidate.current_reviewer_id == seed.user_ids["Sharmaa"]
    assert candidate.version == 2
    assert review.is_current is False
    assert review.version == 2
    assert len(revisions) == 1
    assert [audit.action for audit in audits] == ["REVIEW_REOPEN"]


def test_admin_review_history_cross_user_filters_all_attempts_and_paginates(settings):
    seed, review_ids = asyncio.run(seed_current_database(settings))
    with TestClient(create_app(settings)) as client:
        all_attempts = client.get(
            "/v1/admin/reviews",
            params={"limit": 2, "offset": 0},
            headers=admin_headers(seed),
        )
        filtered = client.get(
            "/v1/admin/reviews",
            params={
                "reviewer_id": str(seed.user_ids["Hassan"]),
                "species_code": "SF001",
                "source_dataset": "iNaturalist",
                "decision": "APPROVED",
                "current": True,
                "date_from": "2026-08-25",
                "date_to": "2026-08-26",
            },
            headers=admin_headers(seed),
        )

    assert all_attempts.status_code == 200
    assert all_attempts.json()["total"] == 3
    assert len(all_attempts.json()["items"]) == 2
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    item = filtered.json()["items"][0]
    assert item["id"] == str(review_ids[0])
    assert item["candidate"]["source_record_id"] == "record-003"
    assert item["species"]["code"] == "SF001"
    assert item["reviewer"]["display_name"] == "Hassan"
    assert "password" not in filtered.text.lower()
    assert "token" not in filtered.text.lower()


def test_admin_reviews_reject_overflowing_date_to_and_accept_safe_upper_bound(settings):
    seed, _ = asyncio.run(seed_current_database(settings))
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        overflowing = client.get(
            "/v1/admin/reviews",
            params={"date_to": "9999-12-31"},
            headers=admin_headers(seed),
        )
        safe = client.get(
            "/v1/admin/reviews",
            params={"date_to": "9998-12-31"},
            headers=admin_headers(seed),
        )

    assert overflowing.status_code == 422
    assert safe.status_code == 200


def test_admin_review_edit_uses_canonical_mapping_revision_version_and_audit(settings):
    seed, review_ids = asyncio.run(seed_current_database(settings))
    review_id = review_ids[0]
    with TestClient(create_app(settings)) as client:
        response = client.patch(
            f"/v1/admin/reviews/{review_id}",
            headers=admin_headers(seed, csrf=True),
            json={
                "version": 1,
                "decision": "REJECTED",
                "rejection_reason": "WRONG_SPECIES",
                "notes": " correction ",
                "reason": " moderator correction ",
            },
        )

    _, review, _, revisions, audits, _ = asyncio.run(
        load_state(settings, review_id=review_id)
    )
    assert response.status_code == 200
    assert response.json()["version"] == review.version == 2
    assert review.reviewer_id == seed.user_ids["Hassan"]
    assert review.decision == Decision.REJECTED
    assert review.whole_fish == "REVIEW"
    assert review.exact_species_verified == "NO"
    assert len(revisions) == 1
    assert revisions[0].actor_id == seed.user_ids["Mao"]
    assert revisions[0].reason == "moderator correction"
    assert revisions[0].snapshot_json["before"]["decision"] == "APPROVED"
    assert revisions[0].snapshot_json["after"]["decision"] == "REJECTED"
    assert revisions[0].snapshot_json["after"]["version"] == 2
    assert len(audits) == 1
    assert audits[0].action == "REVIEW_ADMIN_UPDATE"
    assert audits[0].before_json == revisions[0].snapshot_json["before"]
    assert audits[0].after_json == revisions[0].snapshot_json["after"]


def test_admin_review_edit_rejects_stale_and_noncurrent_without_mutation(settings):
    seed, review_ids = asyncio.run(seed_current_database(settings))
    headers = admin_headers(seed, csrf=True)
    with TestClient(create_app(settings)) as client:
        stale = client.patch(
            f"/v1/admin/reviews/{review_ids[0]}",
            headers=headers,
            json={"version": 99, "decision": "UNSURE", "reason": "edit"},
        )
        old = client.patch(
            f"/v1/admin/reviews/{review_ids[2]}",
            headers=headers,
            json={"version": 2, "decision": "APPROVED", "reason": "edit"},
        )

    _, current, _, revisions, audits, _ = asyncio.run(
        load_state(settings, review_id=review_ids[0])
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "STALE_REVIEW_VERSION"
    assert stale.json()["detail"]["latest"]["version"] == 1
    assert old.status_code == 409
    assert old.json()["detail"]["code"] == "REVIEW_NOT_CURRENT"
    assert current.version == 1
    assert current.decision == Decision.APPROVED
    assert revisions == audits == []


def test_admin_current_lists_only_open_assignments_with_filters(settings):
    seed, _ = asyncio.run(seed_current_database(settings))
    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/v1/admin/current",
            params={
                "species_code": "SF001",
                "source_dataset": "iNaturalist",
                "reviewer_id": str(seed.user_ids["Hassan"]),
                "limit": 10,
                "offset": 0,
            },
            headers=admin_headers(seed),
        )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    item = response.json()["items"][0]
    assert item["candidate"]["id"] == str(seed.candidate_ids[0])
    assert item["reviewer"]["display_name"] == "Hassan"
    assert item["current_started_at"] is not None


def test_admin_current_excludes_an_assignment_that_already_has_a_current_review(
    settings,
):
    seed, _ = asyncio.run(seed_current_database(settings))

    async def arrange(db):
        candidate = await db.get(Candidate, seed.candidate_ids[2])
        candidate.current_reviewer_id = seed.user_ids["Xinhui"]
        candidate.current_started_at = FIXED_NOW

    asyncio.run(mutate_database(settings, arrange))
    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/v1/admin/current", headers=admin_headers(seed)
        )

    assert response.status_code == 200
    returned_ids = {
        item["candidate"]["id"] for item in response.json()["items"]
    }
    assert str(seed.candidate_ids[2]) not in returned_ids


def test_release_clears_unreviewed_assignment_increments_candidate_and_audits(settings):
    seed, _ = asyncio.run(seed_current_database(settings))
    candidate_id = seed.candidate_ids[0]
    with TestClient(create_app(settings)) as client:
        response = client.post(
            f"/v1/admin/current/{candidate_id}/release",
            headers=admin_headers(seed, csrf=True),
            json={"version": 1, "reason": " reviewer unavailable "},
        )

    candidate, _, _, revisions, audits, _ = asyncio.run(
        load_state(settings, candidate_id=candidate_id)
    )
    assert response.status_code == 200
    assert candidate.current_reviewer_id is None
    assert candidate.current_started_at is None
    assert candidate.version == 2
    assert revisions == []
    assert len(audits) == 1
    assert audits[0].action == "CURRENT_RELEASE"
    assert audits[0].before_json["current_reviewer_id"] == str(
        seed.user_ids["Hassan"]
    )
    assert audits[0].after_json["current_reviewer_id"] is None
    assert audits[0].reason == "reviewer unavailable"


def test_release_rejects_missing_assignment_stale_version_and_existing_review(settings):
    seed, review_ids = asyncio.run(seed_current_database(settings))

    async def impossible_reviewed_open(db):
        candidate = await db.get(Candidate, seed.candidate_ids[2])
        candidate.current_reviewer_id = seed.user_ids["Xinhui"]
        candidate.current_started_at = FIXED_NOW

    asyncio.run(mutate_database(settings, impossible_reviewed_open))
    headers = admin_headers(seed, csrf=True)
    with TestClient(create_app(settings)) as client:
        missing = client.post(
            f"/v1/admin/current/{seed.candidate_ids[4]}/release",
            headers=headers,
            json={"version": 1, "reason": "release"},
        )
        stale = client.post(
            f"/v1/admin/current/{seed.candidate_ids[0]}/release",
            headers=headers,
            json={"version": 99, "reason": "release"},
        )
        reviewed = client.post(
            f"/v1/admin/current/{seed.candidate_ids[2]}/release",
            headers=headers,
            json={"version": 1, "reason": "release"},
        )

    candidate, review, _, revisions, audits, _ = asyncio.run(
        load_state(
            settings,
            candidate_id=seed.candidate_ids[2],
            review_id=review_ids[0],
        )
    )
    assert missing.status_code == 409
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "STALE_CANDIDATE_VERSION"
    assert reviewed.status_code == 409
    assert reviewed.json()["detail"]["code"] == "CANDIDATE_ALREADY_REVIEWED"
    assert candidate.current_reviewer_id == seed.user_ids["Xinhui"]
    assert review.is_current is True
    assert revisions == audits == []


def test_transfer_assigns_distinct_eligible_fixed_user_and_audits(settings):
    seed, _ = asyncio.run(seed_current_database(settings))
    candidate_id = seed.candidate_ids[0]
    with TestClient(create_app(settings)) as client:
        response = client.post(
            f"/v1/admin/current/{candidate_id}/transfer",
            headers=admin_headers(seed, csrf=True),
            json={
                "version": 1,
                "new_reviewer_id": str(seed.user_ids["Xinhui"]),
                "reason": "rebalance",
            },
        )

    candidate, _, _, revisions, audits, _ = asyncio.run(
        load_state(settings, candidate_id=candidate_id)
    )
    assert response.status_code == 200
    assert candidate.current_reviewer_id == seed.user_ids["Xinhui"]
    assert candidate.version == 2
    assert revisions == []
    assert len(audits) == 1
    assert audits[0].action == "CURRENT_TRANSFER"
    assert audits[0].after_json["current_reviewer_id"] == str(
        seed.user_ids["Xinhui"]
    )


@pytest.mark.parametrize(
    "target_state", ["same", "inactive", "busy", "prior_review", "non_fixed"]
)
def test_transfer_rejects_ineligible_busy_or_repeat_reviewer(settings, target_state):
    seed, _ = asyncio.run(seed_current_database(settings))
    candidate_id = seed.candidate_ids[0]
    target_id = seed.user_ids["Xinhui"]

    async def arrange(db):
        nonlocal target_id
        target = await db.get(User, target_id)
        if target_state == "same":
            target_id = seed.user_ids["Hassan"]
        elif target_state == "inactive":
            target.active = False
        elif target_state == "busy":
            other = await db.get(Candidate, seed.candidate_ids[4])
            other.current_reviewer_id = target.id
            other.current_started_at = FIXED_NOW
        elif target_state == "prior_review":
            db.add(
                Review(
                    candidate_id=candidate_id,
                    reviewer_id=target.id,
                    decision=Decision.UNSURE,
                    whole_fish="REVIEW",
                    exact_species_verified="REVIEW",
                    is_current=False,
                )
            )
        elif target_state == "non_fixed":
            outsider = User(
                name="Outsider",
                role="reviewer",
                password_hash="test-only-password-hash",
                must_change_password=False,
            )
            db.add(outsider)
            await db.flush()
            target_id = outsider.id

    asyncio.run(mutate_database(settings, arrange))
    with TestClient(create_app(settings)) as client:
        response = client.post(
            f"/v1/admin/current/{candidate_id}/transfer",
            headers=admin_headers(seed, csrf=True),
            json={
                "version": 1,
                "new_reviewer_id": str(target_id),
                "reason": "handoff",
            },
        )

    candidate, _, _, revisions, audits, _ = asyncio.run(
        load_state(settings, candidate_id=candidate_id)
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "REVIEWER_NOT_ELIGIBLE"
    assert candidate.current_reviewer_id == seed.user_ids["Hassan"]
    assert candidate.version == 1
    assert revisions == audits == []


def test_reopen_invalidates_current_review_assigns_target_and_preserves_old_attempt(
    settings,
):
    seed, review_ids = asyncio.run(seed_current_database(settings))
    review_id = review_ids[0]
    candidate_id = seed.candidate_ids[2]
    with TestClient(create_app(settings)) as client:
        response = client.post(
            f"/v1/admin/reviews/{review_id}/reopen",
            headers=admin_headers(seed, csrf=True),
            json={
                "candidate_version": 1,
                "review_version": 1,
                "new_reviewer_id": str(seed.user_ids["Xinhui"]),
                "reason": " second opinion ",
            },
        )

    candidate, review, _, revisions, audits, _ = asyncio.run(
        load_state(settings, candidate_id=candidate_id, review_id=review_id)
    )
    assert response.status_code == 200
    assert candidate.current_reviewer_id == seed.user_ids["Xinhui"]
    assert candidate.current_started_at is not None
    assert candidate.version == 2
    assert review.is_current is False
    assert review.version == 2
    assert len(revisions) == 1
    assert revisions[0].snapshot_json["before"]["is_current"] is True
    assert revisions[0].snapshot_json["after"]["is_current"] is False
    assert revisions[0].reason == "second opinion"
    assert [audit.action for audit in audits] == ["REVIEW_REOPEN"]
    assert audits[0].after_json["candidate"]["current_reviewer_id"] == str(
        seed.user_ids["Xinhui"]
    )


def test_reopen_cannot_assign_a_candidate_from_an_inactive_species(settings):
    seed, review_ids = asyncio.run(seed_current_database(settings))

    async def deactivate(db):
        candidate = await db.get(Candidate, seed.candidate_ids[2])
        species = await db.get(Species, candidate.species_id)
        species.active = False

    asyncio.run(mutate_database(settings, deactivate))
    with TestClient(create_app(settings)) as client:
        response = client.post(
            f"/v1/admin/reviews/{review_ids[0]}/reopen",
            headers=admin_headers(seed, csrf=True),
            json={
                "candidate_version": 1,
                "review_version": 1,
                "new_reviewer_id": str(seed.user_ids["Xinhui"]),
                "reason": "second opinion",
            },
        )

    candidate, review, _, revisions, audits, _ = asyncio.run(
        load_state(
            settings,
            candidate_id=seed.candidate_ids[2],
            review_id=review_ids[0],
        )
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "SPECIES_NOT_ACTIVE"
    assert candidate.current_reviewer_id is None
    assert candidate.version == 1
    assert review.is_current is True
    assert review.version == 1
    assert revisions == audits == []


@pytest.mark.parametrize(
    "conflict", ["candidate_open", "candidate_stale", "review_stale", "non_current"]
)
def test_reopen_rejects_open_stale_or_noncurrent_state_without_mutation(
    settings, conflict
):
    seed, review_ids = asyncio.run(seed_current_database(settings))
    review_id = review_ids[0]
    candidate_id = seed.candidate_ids[2]
    candidate_version = 1
    review_version = 1
    if conflict == "candidate_open":
        async def assign(db):
            candidate = await db.get(Candidate, candidate_id)
            candidate.current_reviewer_id = seed.user_ids["Yiming"]
            candidate.current_started_at = FIXED_NOW
        asyncio.run(mutate_database(settings, assign))
    elif conflict == "candidate_stale":
        candidate_version = 99
    elif conflict == "review_stale":
        review_version = 99
    else:
        review_id = review_ids[2]
        candidate_id = seed.candidate_ids[3]
        review_version = 2

    with TestClient(create_app(settings)) as client:
        response = client.post(
            f"/v1/admin/reviews/{review_id}/reopen",
            headers=admin_headers(seed, csrf=True),
            json={
                "candidate_version": candidate_version,
                "review_version": review_version,
                "new_reviewer_id": str(seed.user_ids["Xinhui"]),
                "reason": "second opinion",
            },
        )

    candidate, review, _, revisions, audits, _ = asyncio.run(
        load_state(settings, candidate_id=candidate_id, review_id=review_id)
    )
    assert response.status_code == 409
    expected_codes = {
        "candidate_open": "CANDIDATE_CURRENTLY_OPEN",
        "candidate_stale": "STALE_CANDIDATE_VERSION",
        "review_stale": "STALE_REVIEW_VERSION",
        "non_current": "REVIEW_NOT_CURRENT",
    }
    assert response.json()["detail"]["code"] == expected_codes[conflict]
    assert review.is_current is (conflict != "non_current")
    assert revisions == audits == []


@pytest.mark.parametrize("target_state", ["inactive", "busy", "prior_review"])
def test_reopen_rejects_ineligible_or_repeat_target(settings, target_state):
    seed, review_ids = asyncio.run(seed_current_database(settings))
    candidate_id = seed.candidate_ids[2]
    target_id = seed.user_ids["Xinhui"]

    async def arrange(db):
        target = await db.get(User, target_id)
        if target_state == "inactive":
            target.active = False
        elif target_state == "busy":
            other = await db.get(Candidate, seed.candidate_ids[4])
            other.current_reviewer_id = target.id
            other.current_started_at = FIXED_NOW
        else:
            db.add(
                Review(
                    candidate_id=candidate_id,
                    reviewer_id=target.id,
                    decision=Decision.UNSURE,
                    whole_fish="REVIEW",
                    exact_species_verified="REVIEW",
                    is_current=False,
                )
            )

    asyncio.run(mutate_database(settings, arrange))
    with TestClient(create_app(settings)) as client:
        response = client.post(
            f"/v1/admin/reviews/{review_ids[0]}/reopen",
            headers=admin_headers(seed, csrf=True),
            json={
                "candidate_version": 1,
                "review_version": 1,
                "new_reviewer_id": str(target_id),
                "reason": "second opinion",
            },
        )

    candidate, review, _, revisions, audits, _ = asyncio.run(
        load_state(
            settings, candidate_id=candidate_id, review_id=review_ids[0]
        )
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "REVIEWER_NOT_ELIGIBLE"
    assert candidate.current_reviewer_id is None
    assert candidate.version == 1
    assert review.is_current is True
    assert review.version == 1
    assert revisions == audits == []


def test_reopen_revision_failure_rolls_back_assignment_review_and_audit(settings):
    seed, review_ids = asyncio.run(seed_current_database(settings))

    def fail_revision(*_):
        raise RuntimeError("forced reopen revision failure")

    event.listen(ReviewRevision, "before_insert", fail_revision)
    try:
        with TestClient(create_app(settings), raise_server_exceptions=False) as client:
            response = client.post(
                f"/v1/admin/reviews/{review_ids[0]}/reopen",
                headers=admin_headers(seed, csrf=True),
                json={
                    "candidate_version": 1,
                    "review_version": 1,
                    "new_reviewer_id": str(seed.user_ids["Xinhui"]),
                    "reason": "second opinion",
                },
            )
    finally:
        event.remove(ReviewRevision, "before_insert", fail_revision)

    candidate, review, _, revisions, audits, _ = asyncio.run(
        load_state(
            settings,
            candidate_id=seed.candidate_ids[2],
            review_id=review_ids[0],
        )
    )
    assert response.status_code == 500
    assert candidate.current_reviewer_id is None
    assert candidate.version == 1
    assert review.is_current is True
    assert review.version == 1
    assert revisions == audits == []


def test_http_password_reset_is_reviewer_only_random_revokes_sessions_and_audits(
    settings,
):
    seed, _ = asyncio.run(seed_current_database(settings))
    target_id = seed.user_ids["Hassan"]
    with TestClient(create_app(settings)) as client:
        response = client.post(
            f"/v1/admin/users/{target_id}/reset-password",
            headers=admin_headers(seed, csrf=True),
            json={"reason": " account recovery "},
        )
        mao_target = client.post(
            f"/v1/admin/users/{seed.user_ids['Mao']}/reset-password",
            headers=admin_headers(seed, csrf=True),
            json={"reason": "not allowed"},
        )

    _, _, user, _, audits, sessions = asyncio.run(
        load_state(settings, user_id=target_id)
    )
    temporary_password = response.json()["temporary_password"]
    hassan_session = next(session for session in sessions if session.user_id == target_id)
    assert response.status_code == 200
    assert set(response.json()) == {"temporary_password"}
    assert len(temporary_password) >= 20
    assert verify_password(temporary_password, user.password_hash)
    assert user.password_version == 2
    assert user.must_change_password is True
    assert user.failed_login_count == 0
    assert user.locked_until is None
    assert hassan_session.revoked_at is not None
    assert mao_target.status_code == 409
    assert len(audits) == 1
    assert audits[0].action == "USER_PASSWORD_RESET"
    assert audits[0].actor_id == seed.user_ids["Mao"]
    serialized = json.dumps(
        {"before": audits[0].before_json, "after": audits[0].after_json}
    ).lower()
    for forbidden in ("password", "hash", "token", temporary_password.lower()):
        assert forbidden not in serialized


def test_cli_reset_uses_shared_transaction_and_actor_null_secret_free_audit(
    settings, monkeypatch
):
    from app.commands import reset_password as command

    seed, _ = asyncio.run(seed_current_database(settings))
    runtime = SimpleNamespace(**vars(settings))
    monkeypatch.setattr(command, "get_settings", lambda: runtime)
    temporary_password = asyncio.run(command.reset_password("Mao"))

    _, _, mao, _, audits, sessions = asyncio.run(
        load_state(settings, user_id=seed.user_ids["Mao"])
    )
    mao_session = next(
        session for session in sessions if session.user_id == seed.user_ids["Mao"]
    )
    assert verify_password(temporary_password, mao.password_hash)
    assert mao.must_change_password is True
    assert mao_session.revoked_at is not None
    assert len(audits) == 1
    assert audits[0].action == "USER_PASSWORD_RESET_SYSTEM"
    assert audits[0].actor_id is None
    serialized = json.dumps(
        {"before": audits[0].before_json, "after": audits[0].after_json}
    ).lower()
    assert temporary_password.lower() not in serialized
    assert all(word not in serialized for word in ("password", "hash", "token"))


def test_password_reset_audit_failure_rolls_back_hash_version_and_session(settings):
    seed, _ = asyncio.run(seed_current_database(settings))
    target_id = seed.user_ids["Hassan"]
    _, _, before, _, _, before_sessions = asyncio.run(
        load_state(settings, user_id=target_id)
    )
    original_hash = before.password_hash
    original_version = before.password_version
    original_revoked = next(
        session.revoked_at for session in before_sessions if session.user_id == target_id
    )

    def fail_audit(*_):
        raise RuntimeError("forced reset audit failure")

    event.listen(AuditEvent, "before_insert", fail_audit)
    try:
        with TestClient(create_app(settings), raise_server_exceptions=False) as client:
            response = client.post(
                f"/v1/admin/users/{target_id}/reset-password",
                headers=admin_headers(seed, csrf=True),
                json={"reason": "account recovery"},
            )
    finally:
        event.remove(AuditEvent, "before_insert", fail_audit)

    _, _, after, _, audits, sessions = asyncio.run(
        load_state(settings, user_id=target_id)
    )
    after_session = next(session for session in sessions if session.user_id == target_id)
    assert response.status_code == 500
    assert after.password_hash == original_hash
    assert after.password_version == original_version
    assert after_session.revoked_at == original_revoked is None
    assert audits == []


def test_task5_self_edit_now_appends_secret_free_audit(settings):
    seed, review_ids = asyncio.run(seed_current_database(settings))
    with TestClient(create_app(settings)) as client:
        response = client.patch(
            f"/v1/history/{review_ids[0]}",
            headers=admin_headers(seed, "Hassan", csrf=True),
            json={"version": 1, "decision": "UNSURE"},
        )

    _, _, _, revisions, audits, _ = asyncio.run(load_state(settings))
    assert response.status_code == 200
    assert len(revisions) == 1
    assert len(audits) == 1
    assert audits[0].action == "REVIEW_SELF_UPDATE"
    assert audits[0].actor_id == seed.user_ids["Hassan"]
    assert audits[0].reason is None
    serialized = json.dumps(
        {"before": audits[0].before_json, "after": audits[0].after_json}
    ).lower()
    assert all(word not in serialized for word in ("password", "hash", "token", "csrf"))


def test_admin_review_audit_failure_rolls_back_review_and_revision(settings):
    seed, review_ids = asyncio.run(seed_current_database(settings))

    def fail_audit(*_):
        raise RuntimeError("forced edit audit failure")

    event.listen(AuditEvent, "before_insert", fail_audit)
    try:
        with TestClient(create_app(settings), raise_server_exceptions=False) as client:
            response = client.patch(
                f"/v1/admin/reviews/{review_ids[0]}",
                headers=admin_headers(seed, csrf=True),
                json={
                    "version": 1,
                    "decision": "UNSURE",
                    "reason": "moderator correction",
                },
            )
    finally:
        event.remove(AuditEvent, "before_insert", fail_audit)

    _, review, _, revisions, audits, _ = asyncio.run(
        load_state(settings, review_id=review_ids[0])
    )
    assert response.status_code == 500
    assert review.decision == Decision.APPROVED
    assert review.version == 1
    assert revisions == audits == []
