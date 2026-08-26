import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.main import create_app
from app.models import (
    AuditEvent,
    Candidate,
    Decision,
    Review,
    ReviewRevision,
    Species,
    User,
)
from tests.admin_support import admin_headers, seed_admin_database


async def seed_catalog(settings):
    return await seed_admin_database(settings, candidate_count=5)


async def mutate_database(settings, callback):
    engine = create_async_engine(settings.DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        result = await callback(db)
        await db.commit()
    await engine.dispose()
    return result


async def load_catalog_state(settings, candidate_id=None, review_id=None):
    engine = create_async_engine(settings.DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        candidate = await db.get(Candidate, candidate_id) if candidate_id else None
        review = await db.get(Review, review_id) if review_id else None
        revisions = list((await db.scalars(select(ReviewRevision))).all())
        audits = list((await db.scalars(select(AuditEvent))).all())
    await engine.dispose()
    return candidate, review, revisions, audits


async def add_review(
    settings,
    seed,
    *,
    candidate_index=0,
    reviewer="Hassan",
    current=True,
    decision=Decision.APPROVED,
):
    async def add(db):
        review = Review(
            candidate_id=seed.candidate_ids[candidate_index],
            reviewer_id=seed.user_ids[reviewer],
            decision=decision,
            rejection_reason=None,
            notes="original review",
            whole_fish="YES" if decision == Decision.APPROVED else "REVIEW",
            exact_species_verified=(
                "YES" if decision == Decision.APPROVED else "REVIEW"
            ),
            is_current=current,
            version=1,
        )
        db.add(review)
        await db.flush()
        return review.id

    return await mutate_database(settings, add)


def test_species_list_filters_sorts_paginates_and_counts_candidates(settings):
    seed = asyncio.run(seed_catalog(settings))
    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/v1/admin/species",
            params={"search": "鱼", "active": True, "limit": 1, "offset": 0},
            headers=admin_headers(seed),
        )

    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert len(response.json()["items"]) == 1
    item = response.json()["items"][0]
    assert item["code"] == "SF002"
    assert item["candidate_count"] == 1
    assert set(item) == {
        "id",
        "code",
        "name_zh",
        "name_en",
        "scientific_name",
        "active",
        "sort_order",
        "candidate_count",
    }


def test_admin_sources_returns_only_distinct_deterministic_catalog_values(settings):
    seed = asyncio.run(seed_catalog(settings))
    with TestClient(create_app(settings)) as client:
        response = client.get("/v1/admin/sources", headers=admin_headers(seed))

    assert response.status_code == 200
    assert response.json() == {"sources": ["iNaturalist", "Wikimedia"]}
    assert "candidate" not in response.text.lower()


def test_species_create_and_edit_trim_fields_keep_code_immutable_and_audit(settings):
    seed = asyncio.run(seed_catalog(settings))
    headers = admin_headers(seed, csrf=True)
    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/v1/admin/species",
            headers=headers,
            json={
                "code": " SF003 ",
                "name_zh": " 新鱼 ",
                "name_en": " New fish ",
                "scientific_name": " Piscis novus ",
                "active": True,
                "sort_order": 30,
                "reason": " add missing species ",
            },
        )
        species_id = created.json()["id"]
        edited = client.patch(
            f"/v1/admin/species/{species_id}",
            headers=headers,
            json={
                "name_en": "Corrected fish",
                "active": False,
                "sort_order": 5,
                "reason": " correct catalog ",
            },
        )
        immutable = client.patch(
            f"/v1/admin/species/{species_id}",
            headers=headers,
            json={"code": "DIFFERENT", "reason": "attempt rename"},
        )

    async def load():
        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            audits = list(
                (
                    await db.scalars(
                        select(AuditEvent).order_by(AuditEvent.created_at)
                    )
                ).all()
            )
        await engine.dispose()
        return audits

    audits = asyncio.run(load())
    assert created.status_code == 201
    assert created.json()["code"] == "SF003"
    assert created.json()["name_zh"] == "新鱼"
    assert edited.status_code == 200
    assert edited.json()["code"] == "SF003"
    assert edited.json()["active"] is False
    assert immutable.status_code == 422
    assert [audit.action for audit in audits] == ["SPECIES_CREATE", "SPECIES_UPDATE"]
    assert audits[0].before_json is None
    assert audits[0].after_json["code"] == "SF003"
    assert audits[1].before_json["name_en"] == "New fish"
    assert audits[1].after_json["name_en"] == "Corrected fish"
    assert audits[1].reason == "correct catalog"


@pytest.mark.parametrize(
    "field",
    ["name_zh", "name_en", "scientific_name", "active", "sort_order"],
)
def test_species_patch_rejects_explicit_null_without_database_work(settings, field):
    seed = asyncio.run(seed_catalog(settings))
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        response = client.patch(
            f"/v1/admin/species/{seed.species_ids[0]}",
            headers=admin_headers(seed, csrf=True),
            json={field: None, "reason": "invalid clear"},
        )

    async def load():
        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            species = await db.get(Species, seed.species_ids[0])
            audits = list((await db.scalars(select(AuditEvent))).all())
        await engine.dispose()
        return species, audits

    species, audits = asyncio.run(load())
    assert response.status_code == 422
    assert species.name_zh == "测试鱼"
    assert species.name_en == "Test fish"
    assert species.scientific_name == "Piscis probatio"
    assert species.active is True
    assert species.sort_order == 20
    assert audits == []


def test_species_duplicate_code_conflicts_without_partial_row_or_audit(settings):
    seed = asyncio.run(seed_catalog(settings))
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/v1/admin/species",
            headers=admin_headers(seed, csrf=True),
            json={
                "code": "SF001",
                "name_zh": "重复",
                "name_en": "Duplicate",
                "scientific_name": "Piscis duplicate",
                "reason": "test duplicate",
            },
        )

    async def counts():
        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            result = (
                await db.scalar(select(func.count()).select_from(Species)),
                await db.scalar(select(func.count()).select_from(AuditEvent)),
            )
        await engine.dispose()
        return result

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "SPECIES_CODE_CONFLICT"
    assert asyncio.run(counts()) == (2, 0)


def test_species_cannot_be_disabled_while_candidate_is_open(settings):
    seed = asyncio.run(seed_catalog(settings))

    async def assign(db):
        candidate = await db.get(Candidate, seed.candidate_ids[0])
        candidate.current_reviewer_id = seed.user_ids["Hassan"]
        candidate.current_started_at = datetime.now(timezone.utc)

    asyncio.run(mutate_database(settings, assign))
    with TestClient(create_app(settings)) as client:
        response = client.patch(
            f"/v1/admin/species/{seed.species_ids[0]}",
            headers=admin_headers(seed, csrf=True),
            json={"active": False, "reason": "retire species"},
        )

    async def load():
        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            species = await db.get(Species, seed.species_ids[0])
            audit_count = await db.scalar(select(func.count()).select_from(AuditEvent))
        await engine.dispose()
        return species, audit_count

    species, audit_count = asyncio.run(load())
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "SPECIES_HAS_OPEN_CANDIDATE"
    assert species.active is True
    assert audit_count == 0


def test_candidate_list_supports_admin_filters_and_safe_nested_summaries(settings):
    seed = asyncio.run(seed_catalog(settings))
    review_id = asyncio.run(
        add_review(settings, seed, candidate_index=1, reviewer="Hassan")
    )
    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/v1/admin/candidates",
            params={
                "species_code": "SF002",
                "source_dataset": "Wikimedia",
                "active": True,
                "reviewed": True,
                "decision": "APPROVED",
                "search": "record-002",
            },
            headers=admin_headers(seed),
        )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    item = response.json()["items"][0]
    assert item["id"] == str(seed.candidate_ids[1])
    assert item["species"]["code"] == "SF002"
    assert item["current_review"]["id"] == str(review_id)
    assert item["current_review"]["reviewer"]["display_name"] == "Hassan"
    assert "password_hash" not in response.text
    assert "token" not in response.text.lower()


def test_candidate_safe_patch_validates_https_increments_version_once_and_audits(
    settings,
):
    seed = asyncio.run(seed_catalog(settings))
    candidate_id = seed.candidate_ids[0]
    headers = admin_headers(seed, csrf=True)
    with TestClient(create_app(settings)) as client:
        invalid = client.patch(
            f"/v1/admin/candidates/{candidate_id}",
            headers=headers,
            json={
                "version": 1,
                "preview_url": "http://images.example.test/insecure.jpg",
                "reason": "repair image",
            },
        )
        response = client.patch(
            f"/v1/admin/candidates/{candidate_id}",
            headers=headers,
            json={
                "version": 1,
                "source_dataset": "GBIF",
                "source_record_id": "gbif-100",
                "preview_url": "https://images.example.test/new-preview.jpg",
                "original_url": "https://images.example.test/new-original.jpg",
                "source_url": "https://source.example.test/new",
                "creator": " Corrected creator ",
                "license": "CC0",
                "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
                "attribution": " Corrected creator / CC0 ",
                "location": " Ningbo ",
                "observed_on": "2026-08-01",
                "metadata": {"catalog_number": "gbif-100"},
                "active": True,
                "reason": " correct source metadata ",
            },
        )

    candidate, _, _, audits = asyncio.run(
        load_catalog_state(settings, candidate_id=candidate_id)
    )
    assert invalid.status_code == 422
    assert response.status_code == 200
    assert response.json()["version"] == candidate.version == 2
    assert candidate.source_dataset == "GBIF"
    assert candidate.creator == "Corrected creator"
    assert candidate.metadata_json == {"catalog_number": "gbif-100"}
    assert len(audits) == 1
    assert audits[0].action == "CANDIDATE_UPDATE"
    assert audits[0].before_json["source_dataset"] == "iNaturalist"
    assert audits[0].after_json["source_dataset"] == "GBIF"
    assert audits[0].reason == "correct source metadata"


@pytest.mark.parametrize(
    "invalid_url",
    [
        "https://example.com:bad/path",
        "https://user:pass@example.com/path",
        "https:///missing-host",
        "http://example.com/path",
        "https://example.com/path\nheader",
        "https://example.com:65536/path",
        "https://127.0.0.1/fish.jpg",
        "https://[::1]/fish.jpg",
        "https://localhost/fish.jpg",
    ],
)
def test_candidate_patch_strictly_rejects_malformed_https_urls(
    settings, invalid_url
):
    seed = asyncio.run(seed_catalog(settings))
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        response = client.patch(
            f"/v1/admin/candidates/{seed.candidate_ids[0]}",
            headers=admin_headers(seed, csrf=True),
            json={
                "version": 1,
                "preview_url": invalid_url,
                "reason": "validate URL",
            },
        )

    candidate, _, revisions, audits = asyncio.run(
        load_catalog_state(settings, candidate_id=seed.candidate_ids[0])
    )
    assert response.status_code == 422
    assert candidate.version == 1
    assert revisions == audits == []


def test_candidate_patch_rejects_unapproved_public_hostname_without_mutation(
    settings,
):
    seed = asyncio.run(seed_catalog(settings))
    with TestClient(create_app(settings)) as client:
        response = client.patch(
            f"/v1/admin/candidates/{seed.candidate_ids[0]}",
            headers=admin_headers(seed, csrf=True),
            json={
                "version": 1,
                "preview_url": "https://private.invalid/fish.jpg",
                "reason": "validate origin",
            },
        )

    candidate, _, revisions, audits = asyncio.run(
        load_catalog_state(settings, candidate_id=seed.candidate_ids[0])
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "IMAGE_ORIGIN_NOT_ALLOWED"
    assert candidate.version == 1
    assert revisions == audits == []


@pytest.mark.parametrize(
    "valid_url",
    [
        "https://cdn.images.example.test/fish.jpg",
        "https://images.example.test:443/fish.jpg",
    ],
)
def test_candidate_patch_accepts_only_permitted_image_cdn_origins(
    settings, valid_url
):
    seed = asyncio.run(seed_catalog(settings))
    with TestClient(create_app(settings)) as client:
        response = client.patch(
            f"/v1/admin/candidates/{seed.candidate_ids[0]}",
            headers=admin_headers(seed, csrf=True),
            json={
                "version": 1,
                "preview_url": valid_url,
                "reason": "validate URL",
            },
        )

    candidate, _, _, audits = asyncio.run(
        load_catalog_state(settings, candidate_id=seed.candidate_ids[0])
    )
    assert response.status_code == 200
    assert candidate.preview_url.startswith("https://")
    assert candidate.version == 2
    assert [audit.action for audit in audits] == ["CANDIDATE_UPDATE"]


def test_candidate_stale_and_duplicate_source_conflicts_roll_back(settings):
    seed = asyncio.run(seed_catalog(settings))
    candidate_id = seed.candidate_ids[0]
    headers = admin_headers(seed, csrf=True)
    with TestClient(create_app(settings)) as client:
        stale = client.patch(
            f"/v1/admin/candidates/{candidate_id}",
            headers=headers,
            json={"version": 99, "creator": "stale", "reason": "fix"},
        )
        duplicate = client.patch(
            f"/v1/admin/candidates/{candidate_id}",
            headers=headers,
            json={
                "version": 1,
                "source_dataset": "Wikimedia",
                "source_record_id": "record-002",
                "reason": "merge source identity",
            },
        )

    candidate, _, _, audits = asyncio.run(
        load_catalog_state(settings, candidate_id=candidate_id)
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "STALE_CANDIDATE_VERSION"
    assert stale.json()["detail"]["latest"]["version"] == 1
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "CANDIDATE_SOURCE_CONFLICT"
    assert candidate.source_dataset == "iNaturalist"
    assert candidate.source_record_id == "record-001"
    assert candidate.version == 1
    assert audits == []


@pytest.mark.parametrize(
    "invalid_change",
    [
        {"species_id": None},
        {"source_dataset": "   "},
        {"source_record_id": "   "},
        {"preview_url": None},
        {"original_url": None},
        {"source_url": None},
        {"license": "   "},
        {"attribution": "   "},
        {"metadata": None},
    ],
)
def test_candidate_patch_rejects_clearing_required_database_fields(
    settings, invalid_change
):
    seed = asyncio.run(seed_catalog(settings))
    candidate_id = seed.candidate_ids[0]
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        response = client.patch(
            f"/v1/admin/candidates/{candidate_id}",
            headers=admin_headers(seed, csrf=True),
            json={"version": 1, "reason": "invalid clear", **invalid_change},
        )

    candidate, _, revisions, audits = asyncio.run(
        load_catalog_state(settings, candidate_id=candidate_id)
    )
    assert response.status_code == 422
    assert candidate.version == 1
    assert revisions == audits == []


def test_candidate_patch_rejects_assignment_controls_without_invalidation(settings):
    seed = asyncio.run(seed_catalog(settings))
    candidate_id = seed.candidate_ids[0]
    with TestClient(create_app(settings)) as client:
        response = client.patch(
            f"/v1/admin/candidates/{candidate_id}",
            headers=admin_headers(seed, csrf=True),
            json={
                "version": 1,
                "creator": "changed",
                "new_reviewer_id": str(seed.user_ids["Xinhui"]),
                "reason": "invalid assignment",
            },
        )

    candidate, _, revisions, audits = asyncio.run(
        load_catalog_state(settings, candidate_id=candidate_id)
    )
    assert response.status_code == 422
    assert candidate.version == 1
    assert revisions == audits == []


@pytest.mark.parametrize(
    "payload",
    [
        {
            "species_id": "other",
            "confirm_review_invalidation": False,
            "new_reviewer_id": "target",
        },
        {
            "species_id": "same",
            "confirm_review_invalidation": True,
            "new_reviewer_id": "target",
        },
    ],
)
def test_candidate_invalidation_controls_reject_unconfirmed_or_same_species(
    settings, payload
):
    seed = asyncio.run(seed_catalog(settings))
    review_id = asyncio.run(add_review(settings, seed))
    species_id = (
        seed.species_ids[1]
        if payload["species_id"] == "other"
        else seed.species_ids[0]
    )
    with TestClient(create_app(settings)) as client:
        response = client.patch(
            f"/v1/admin/candidates/{seed.candidate_ids[0]}",
            headers=admin_headers(seed, csrf=True),
            json={
                "version": 1,
                "species_id": str(species_id),
                "confirm_review_invalidation": payload[
                    "confirm_review_invalidation"
                ],
                "new_reviewer_id": str(seed.user_ids["Xinhui"]),
                "reason": "invalid invalidation controls",
            },
        )

    candidate, review, revisions, audits = asyncio.run(
        load_catalog_state(
            settings,
            candidate_id=seed.candidate_ids[0],
            review_id=review_id,
        )
    )
    assert response.status_code in {409, 422}
    assert candidate.species_id == seed.species_ids[0]
    assert candidate.current_reviewer_id is None
    assert candidate.version == 1
    assert review.is_current is True
    assert review.version == 1
    assert revisions == audits == []


def test_unreviewed_species_change_rejects_invalidation_controls(settings):
    seed = asyncio.run(seed_catalog(settings))
    with TestClient(create_app(settings)) as client:
        response = client.patch(
            f"/v1/admin/candidates/{seed.candidate_ids[0]}",
            headers=admin_headers(seed, csrf=True),
            json={
                "version": 1,
                "species_id": str(seed.species_ids[1]),
                "confirm_review_invalidation": True,
                "new_reviewer_id": str(seed.user_ids["Xinhui"]),
                "reason": "controls are not needed",
            },
        )

    candidate, _, revisions, audits = asyncio.run(
        load_catalog_state(settings, candidate_id=seed.candidate_ids[0])
    )
    assert response.status_code == 409
    assert candidate.species_id == seed.species_ids[0]
    assert candidate.current_reviewer_id is None
    assert candidate.version == 1
    assert revisions == audits == []


@pytest.mark.parametrize(
    "same_value",
    [
        {"creator": "Test Creator"},
        {"species_id": "same"},
        {"active": True},
    ],
)
def test_candidate_patch_rejects_noop_without_version_or_audit(
    settings, same_value
):
    seed = asyncio.run(seed_catalog(settings))
    change = dict(same_value)
    if change.get("species_id") == "same":
        change["species_id"] = str(seed.species_ids[0])
    with TestClient(create_app(settings)) as client:
        response = client.patch(
            f"/v1/admin/candidates/{seed.candidate_ids[0]}",
            headers=admin_headers(seed, csrf=True),
            json={"version": 1, "reason": "no actual change", **change},
        )

    candidate, _, revisions, audits = asyncio.run(
        load_catalog_state(settings, candidate_id=seed.candidate_ids[0])
    )
    assert response.status_code == 409
    assert candidate.version == 1
    assert revisions == audits == []


def test_candidate_patch_rejects_oversized_metadata_without_mutation(settings):
    seed = asyncio.run(seed_catalog(settings))
    candidate_id = seed.candidate_ids[0]
    with TestClient(create_app(settings)) as client:
        response = client.patch(
            f"/v1/admin/candidates/{candidate_id}",
            headers=admin_headers(seed, csrf=True),
            json={
                "version": 1,
                "metadata": {"description": "x" * 70_000},
                "reason": "invalid metadata",
            },
        )

    candidate, _, revisions, audits = asyncio.run(
        load_catalog_state(settings, candidate_id=candidate_id)
    )
    assert response.status_code == 422
    assert candidate.version == 1
    assert revisions == audits == []


def test_candidate_open_assignment_blocks_patch_and_deactivation(settings):
    seed = asyncio.run(seed_catalog(settings))
    candidate_id = seed.candidate_ids[0]

    async def assign(db):
        candidate = await db.get(Candidate, candidate_id)
        candidate.current_reviewer_id = seed.user_ids["Hassan"]
        candidate.current_started_at = datetime.now(timezone.utc)

    asyncio.run(mutate_database(settings, assign))
    headers = admin_headers(seed, csrf=True)
    with TestClient(create_app(settings)) as client:
        metadata = client.patch(
            f"/v1/admin/candidates/{candidate_id}",
            headers=headers,
            json={"version": 1, "creator": "changed", "reason": "fix"},
        )
        inactive = client.patch(
            f"/v1/admin/candidates/{candidate_id}",
            headers=headers,
            json={"version": 1, "active": False, "reason": "disable"},
        )

    candidate, _, _, audits = asyncio.run(
        load_catalog_state(settings, candidate_id=candidate_id)
    )
    assert metadata.status_code == inactive.status_code == 409
    assert metadata.json()["detail"]["code"] == "CANDIDATE_CURRENTLY_OPEN"
    assert candidate.current_reviewer_id == seed.user_ids["Hassan"]
    assert candidate.active is True
    assert candidate.version == 1
    assert audits == []


def test_unreviewed_unassigned_candidate_species_change_is_a_normal_update(settings):
    seed = asyncio.run(seed_catalog(settings))
    candidate_id = seed.candidate_ids[0]
    with TestClient(create_app(settings)) as client:
        response = client.patch(
            f"/v1/admin/candidates/{candidate_id}",
            headers=admin_headers(seed, csrf=True),
            json={
                "version": 1,
                "species_id": str(seed.species_ids[1]),
                "reason": "correct species",
            },
        )

    candidate, _, revisions, audits = asyncio.run(
        load_catalog_state(settings, candidate_id=candidate_id)
    )
    assert response.status_code == 200
    assert candidate.species_id == seed.species_ids[1]
    assert candidate.current_reviewer_id is None
    assert candidate.version == 2
    assert revisions == []
    assert [audit.action for audit in audits] == ["CANDIDATE_UPDATE"]


def test_reviewed_species_change_requires_confirmation_and_target(settings):
    seed = asyncio.run(seed_catalog(settings))
    review_id = asyncio.run(add_review(settings, seed))
    path = f"/v1/admin/candidates/{seed.candidate_ids[0]}"
    headers = admin_headers(seed, csrf=True)
    with TestClient(create_app(settings)) as client:
        missing_confirmation = client.patch(
            path,
            headers=headers,
            json={
                "version": 1,
                "species_id": str(seed.species_ids[1]),
                "new_reviewer_id": str(seed.user_ids["Xinhui"]),
                "reason": "wrong species",
            },
        )
        missing_target = client.patch(
            path,
            headers=headers,
            json={
                "version": 1,
                "species_id": str(seed.species_ids[1]),
                "confirm_review_invalidation": True,
                "reason": "wrong species",
            },
        )

    candidate, review, revisions, audits = asyncio.run(
        load_catalog_state(
            settings, candidate_id=seed.candidate_ids[0], review_id=review_id
        )
    )
    assert missing_confirmation.status_code == 422
    assert missing_target.status_code == 422
    assert candidate.species_id == seed.species_ids[0]
    assert candidate.version == 1
    assert review.is_current is True
    assert review.version == 1
    assert revisions == audits == []


def test_reviewed_candidate_metadata_change_with_same_species_preserves_review(
    settings,
):
    seed = asyncio.run(seed_catalog(settings))
    review_id = asyncio.run(add_review(settings, seed))
    with TestClient(create_app(settings)) as client:
        response = client.patch(
            f"/v1/admin/candidates/{seed.candidate_ids[0]}",
            headers=admin_headers(seed, csrf=True),
            json={
                "version": 1,
                "species_id": str(seed.species_ids[0]),
                "metadata": {
                    "catalog_number": "updated-after-review",
                    "quality": "verified",
                },
                "reason": "correct metadata only",
            },
        )

    candidate, review, revisions, audits = asyncio.run(
        load_catalog_state(
            settings,
            candidate_id=seed.candidate_ids[0],
            review_id=review_id,
        )
    )
    assert response.status_code == 200
    assert candidate.species_id == seed.species_ids[0]
    assert candidate.metadata_json == {
        "catalog_number": "updated-after-review",
        "quality": "verified",
    }
    assert candidate.current_reviewer_id is None
    assert candidate.version == 2
    assert review.is_current is True
    assert review.version == 1
    assert revisions == []
    assert [audit.action for audit in audits] == ["CANDIDATE_UPDATE"]


def test_reviewed_species_change_invalidates_full_review_and_assigns_new_reviewer(
    settings,
):
    seed = asyncio.run(seed_catalog(settings))
    review_id = asyncio.run(add_review(settings, seed))
    with TestClient(create_app(settings)) as client:
        response = client.patch(
            f"/v1/admin/candidates/{seed.candidate_ids[0]}",
            headers=admin_headers(seed, csrf=True),
            json={
                "version": 1,
                "species_id": str(seed.species_ids[1]),
                "confirm_review_invalidation": True,
                "new_reviewer_id": str(seed.user_ids["Xinhui"]),
                "reason": " wrong species assignment ",
            },
        )

    candidate, review, revisions, audits = asyncio.run(
        load_catalog_state(
            settings, candidate_id=seed.candidate_ids[0], review_id=review_id
        )
    )
    assert response.status_code == 200
    assert candidate.species_id == seed.species_ids[1]
    assert candidate.current_reviewer_id == seed.user_ids["Xinhui"]
    assert candidate.current_started_at is not None
    assert candidate.version == 2
    assert review.is_current is False
    assert review.version == 2
    assert len(revisions) == 1
    assert revisions[0].reason == "wrong species assignment"
    assert revisions[0].snapshot_json["before"]["is_current"] is True
    assert revisions[0].snapshot_json["after"]["is_current"] is False
    assert revisions[0].snapshot_json["after"]["version"] == 2
    assert len(audits) == 1
    assert audits[0].action == "CANDIDATE_UPDATE"
    assert audits[0].after_json["candidate"]["species_id"] == str(
        seed.species_ids[1]
    )
    assert audits[0].after_json["invalidated_review"]["is_current"] is False


def test_reviewed_species_change_cannot_assign_into_inactive_species(settings):
    seed = asyncio.run(seed_catalog(settings))
    review_id = asyncio.run(add_review(settings, seed))

    async def deactivate(db):
        species = await db.get(Species, seed.species_ids[1])
        species.active = False

    asyncio.run(mutate_database(settings, deactivate))
    with TestClient(create_app(settings)) as client:
        response = client.patch(
            f"/v1/admin/candidates/{seed.candidate_ids[0]}",
            headers=admin_headers(seed, csrf=True),
            json={
                "version": 1,
                "species_id": str(seed.species_ids[1]),
                "confirm_review_invalidation": True,
                "new_reviewer_id": str(seed.user_ids["Xinhui"]),
                "reason": "wrong species",
            },
        )

    candidate, review, revisions, audits = asyncio.run(
        load_catalog_state(
            settings,
            candidate_id=seed.candidate_ids[0],
            review_id=review_id,
        )
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "SPECIES_NOT_ACTIVE"
    assert candidate.species_id == seed.species_ids[0]
    assert candidate.current_reviewer_id is None
    assert review.is_current is True
    assert revisions == audits == []


@pytest.mark.parametrize("target_state", ["inactive", "busy", "prior_review"])
def test_reviewed_species_change_rejects_ineligible_or_repeat_target(
    settings, target_state
):
    seed = asyncio.run(seed_catalog(settings))
    review_id = asyncio.run(add_review(settings, seed))

    async def arrange(db):
        target = await db.get(User, seed.user_ids["Xinhui"])
        if target_state == "inactive":
            target.active = False
        elif target_state == "busy":
            other = await db.get(Candidate, seed.candidate_ids[1])
            other.current_reviewer_id = target.id
            other.current_started_at = datetime.now(timezone.utc)
        else:
            db.add(
                Review(
                    candidate_id=seed.candidate_ids[0],
                    reviewer_id=target.id,
                    decision=Decision.UNSURE,
                    whole_fish="REVIEW",
                    exact_species_verified="REVIEW",
                    is_current=False,
                )
            )

    asyncio.run(mutate_database(settings, arrange))
    with TestClient(create_app(settings)) as client:
        response = client.patch(
            f"/v1/admin/candidates/{seed.candidate_ids[0]}",
            headers=admin_headers(seed, csrf=True),
            json={
                "version": 1,
                "species_id": str(seed.species_ids[1]),
                "confirm_review_invalidation": True,
                "new_reviewer_id": str(seed.user_ids["Xinhui"]),
                "reason": "wrong species",
            },
        )

    candidate, review, revisions, audits = asyncio.run(
        load_catalog_state(
            settings, candidate_id=seed.candidate_ids[0], review_id=review_id
        )
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "REVIEWER_NOT_ELIGIBLE"
    assert candidate.species_id == seed.species_ids[0]
    assert candidate.version == 1
    assert review.is_current is True
    assert review.version == 1
    assert revisions == audits == []


def test_candidate_audit_failure_rolls_back_the_whole_update(settings):
    seed = asyncio.run(seed_catalog(settings))

    def fail_audit(*_):
        raise RuntimeError("forced audit failure")

    event.listen(AuditEvent, "before_insert", fail_audit)
    try:
        with TestClient(create_app(settings), raise_server_exceptions=False) as client:
            response = client.patch(
                f"/v1/admin/candidates/{seed.candidate_ids[0]}",
                headers=admin_headers(seed, csrf=True),
                json={"version": 1, "creator": "changed", "reason": "fix"},
            )
    finally:
        event.remove(AuditEvent, "before_insert", fail_audit)

    candidate, _, revisions, audits = asyncio.run(
        load_catalog_state(settings, candidate_id=seed.candidate_ids[0])
    )
    assert response.status_code == 500
    assert candidate.creator == "Test Creator"
    assert candidate.version == 1
    assert revisions == audits == []


def test_candidate_audit_redacts_secret_like_metadata_values(settings):
    seed = asyncio.run(seed_catalog(settings))
    candidate_id = seed.candidate_ids[0]
    with TestClient(create_app(settings)) as client:
        response = client.patch(
            f"/v1/admin/candidates/{candidate_id}",
            headers=admin_headers(seed, csrf=True),
            json={
                "version": 1,
                "metadata": {
                    "catalog_number": "safe-value",
                    "session_token": "raw-secret-value",
                },
                "reason": "correct metadata",
            },
        )

    candidate, _, _, audits = asyncio.run(
        load_catalog_state(settings, candidate_id=candidate_id)
    )
    serialized_audit = str(audits[0].after_json)
    assert response.status_code == 200
    assert candidate.metadata_json["session_token"] == "raw-secret-value"
    assert "safe-value" in serialized_audit
    assert "raw-secret-value" not in serialized_audit


def test_review_invalidation_revision_failure_rolls_back_candidate_review_and_audit(
    settings,
):
    seed = asyncio.run(seed_catalog(settings))
    review_id = asyncio.run(add_review(settings, seed))

    def fail_revision(*_):
        raise RuntimeError("forced revision failure")

    event.listen(ReviewRevision, "before_insert", fail_revision)
    try:
        with TestClient(create_app(settings), raise_server_exceptions=False) as client:
            response = client.patch(
                f"/v1/admin/candidates/{seed.candidate_ids[0]}",
                headers=admin_headers(seed, csrf=True),
                json={
                    "version": 1,
                    "species_id": str(seed.species_ids[1]),
                    "confirm_review_invalidation": True,
                    "new_reviewer_id": str(seed.user_ids["Xinhui"]),
                    "reason": "wrong species",
                },
            )
    finally:
        event.remove(ReviewRevision, "before_insert", fail_revision)

    candidate, review, revisions, audits = asyncio.run(
        load_catalog_state(
            settings, candidate_id=seed.candidate_ids[0], review_id=review_id
        )
    )
    assert response.status_code == 500
    assert candidate.species_id == seed.species_ids[0]
    assert candidate.current_reviewer_id is None
    assert candidate.version == 1
    assert review.is_current is True
    assert review.version == 1
    assert revisions == audits == []
