import asyncio
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import User
from tests.admin_support import admin_headers, seed_admin_database, update_one


UNKNOWN_ID = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")


def create_admin_database(settings, *, mao_must_change_password=False):
    return asyncio.run(
        seed_admin_database(
            settings, mao_must_change_password=mao_must_change_password
        )
    )


READ_PATHS = (
    "/v1/admin/users",
    "/v1/admin/species",
    "/v1/admin/candidates",
    "/v1/admin/reviews",
    "/v1/admin/current",
)


def mutation_cases(seed):
    candidate_id = seed.candidate_ids[0]
    review_id = UNKNOWN_ID
    return (
        (
            "post",
            "/v1/admin/species",
            {
                "code": "SF003",
                "name_zh": "新鱼",
                "name_en": "New fish",
                "scientific_name": "Piscis novus",
                "active": True,
                "sort_order": 30,
                "reason": "add catalog entry",
            },
        ),
        (
            "patch",
            f"/v1/admin/species/{seed.species_ids[0]}",
            {"name_en": "Updated fish", "reason": "correct name"},
        ),
        (
            "patch",
            f"/v1/admin/candidates/{candidate_id}",
            {"version": 1, "creator": "Updated", "reason": "correct source"},
        ),
        (
            "patch",
            f"/v1/admin/reviews/{review_id}",
            {"version": 1, "decision": "UNSURE", "reason": "moderation"},
        ),
        (
            "post",
            f"/v1/admin/current/{candidate_id}/release",
            {"version": 1, "reason": "reviewer unavailable"},
        ),
        (
            "post",
            f"/v1/admin/current/{candidate_id}/transfer",
            {
                "version": 1,
                "new_reviewer_id": str(seed.user_ids["Xinhui"]),
                "reason": "handoff",
            },
        ),
        (
            "post",
            f"/v1/admin/reviews/{review_id}/reopen",
            {
                "candidate_version": 1,
                "review_version": 1,
                "new_reviewer_id": str(seed.user_ids["Xinhui"]),
                "reason": "second opinion",
            },
        ),
        (
            "post",
            f"/v1/admin/users/{seed.user_ids['Hassan']}/reset-password",
            {"reason": "account recovery"},
        ),
    )


@pytest.mark.parametrize("path", READ_PATHS)
def test_admin_reads_require_authentication_and_completed_mao_password(
    settings, path
):
    seed = create_admin_database(settings, mao_must_change_password=True)
    with TestClient(create_app(settings)) as client:
        anonymous = client.get(path)
        reviewer = client.get(path, headers=admin_headers(seed, "Hassan"))
        blocked_mao = client.get(path, headers=admin_headers(seed))

    assert anonymous.status_code == 401
    assert reviewer.status_code == 403
    assert blocked_mao.status_code == 403


def test_reviewers_are_forbidden_from_every_admin_mutation_without_object_disclosure(
    settings,
):
    seed = create_admin_database(settings)
    with TestClient(create_app(settings)) as client:
        responses = [
            getattr(client, method)(
                path,
                json=payload,
                headers=admin_headers(seed, "Hassan", csrf=True),
            )
            for method, path, payload in mutation_cases(seed)
        ]

    assert {response.status_code for response in responses} == {403}


def test_every_admin_mutation_requires_authentication_csrf_and_completed_password(
    settings,
):
    seed = create_admin_database(settings)
    with TestClient(create_app(settings)) as client:
        anonymous = [
            getattr(client, method)(path, json=payload)
            for method, path, payload in mutation_cases(seed)
        ]
        missing_csrf = [
            getattr(client, method)(
                path, json=payload, headers=admin_headers(seed)
            )
            for method, path, payload in mutation_cases(seed)
        ]

    assert {response.status_code for response in anonymous} == {401}
    assert {response.status_code for response in missing_csrf} == {403}

    asyncio.run(
        update_one(
            settings,
            User,
            seed.user_ids["Mao"],
            must_change_password=True,
        )
    )
    with TestClient(create_app(settings)) as client:
        blocked = [
            getattr(client, method)(
                path,
                json=payload,
                headers=admin_headers(seed, csrf=True),
            )
            for method, path, payload in mutation_cases(seed)
        ]
    assert {response.status_code for response in blocked} == {403}


@pytest.mark.parametrize("reason", [None, "", "   ", "x" * 1001])
def test_every_admin_mutation_rejects_missing_blank_or_overlong_reason(
    settings, reason
):
    seed = create_admin_database(settings)
    cases = []
    for method, path, payload in mutation_cases(seed):
        invalid = dict(payload)
        if reason is None:
            invalid.pop("reason")
        else:
            invalid["reason"] = reason
        cases.append((method, path, invalid))

    with TestClient(create_app(settings)) as client:
        responses = [
            getattr(client, method)(
                path, json=payload, headers=admin_headers(seed, csrf=True)
            )
            for method, path, payload in cases
        ]

    assert {response.status_code for response in responses} == {422}


def test_admin_api_exposes_no_delete_routes(settings):
    seed = create_admin_database(settings)
    paths = (
        f"/v1/admin/species/{seed.species_ids[0]}",
        f"/v1/admin/candidates/{seed.candidate_ids[0]}",
        f"/v1/admin/reviews/{UNKNOWN_ID}",
        f"/v1/admin/users/{seed.user_ids['Hassan']}",
    )
    with TestClient(create_app(settings)) as client:
        responses = [
            client.delete(path, headers=admin_headers(seed, csrf=True))
            for path in paths
        ]

    assert all(response.status_code in {404, 405} for response in responses)


def test_admin_role_does_not_grant_non_mao_access(settings):
    seed = create_admin_database(settings)
    asyncio.run(
        update_one(settings, User, seed.user_ids["Hassan"], role="admin")
    )
    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/v1/admin/species", headers=admin_headers(seed, "Hassan")
        )

    assert response.status_code == 403
