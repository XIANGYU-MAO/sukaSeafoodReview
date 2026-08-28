import asyncio

from fastapi.testclient import TestClient

from app.main import create_app
from tests.admin_support import admin_headers, seed_admin_database


def test_system_settings_default_to_existing_login_and_team_progress_behavior(settings):
    seed = asyncio.run(seed_admin_database(settings))

    with TestClient(create_app(settings)) as client:
        login_options = client.get("/v1/auth/names")
        admin_settings = client.get(
            "/v1/admin/settings", headers=admin_headers(seed)
        )
        reviewer_progress = client.get(
            "/v1/progress", headers=admin_headers(seed, "Hassan")
        )

    assert login_options.status_code == 200
    assert login_options.json() == {
        "login_name_mode": "choices",
        "names": [
            {"name": name}
            for name in ("Hassan", "Mao", "Xinhui", "Wahid", "Sharmaa", "Yiming")
        ],
    }
    assert admin_settings.status_code == 200
    assert admin_settings.json() == {
        "login_name_mode": "choices",
        "reviewer_team_progress_visible": True,
    }
    assert reviewer_progress.status_code == 200


def test_admin_can_switch_to_manual_login_and_hide_team_progress_from_reviewers(settings):
    seed = asyncio.run(seed_admin_database(settings))

    with TestClient(create_app(settings)) as client:
        updated = client.patch(
            "/v1/admin/settings",
            headers=admin_headers(seed, csrf=True),
            json={
                "login_name_mode": "manual",
                "reviewer_team_progress_visible": False,
                "reason": "test visibility settings",
            },
        )
        login_options = client.get("/v1/auth/names")
        reviewer_progress = client.get(
            "/v1/progress", headers=admin_headers(seed, "Hassan")
        )
        admin_progress = client.get(
            "/v1/progress", headers=admin_headers(seed, "Mao")
        )

    assert updated.status_code == 200
    assert updated.json() == {
        "login_name_mode": "manual",
        "reviewer_team_progress_visible": False,
    }
    assert login_options.json() == {
        "login_name_mode": "manual",
        "names": [],
    }
    assert reviewer_progress.status_code == 403
    assert admin_progress.status_code == 200


def test_only_completed_admin_with_csrf_can_update_system_settings(settings):
    seed = asyncio.run(seed_admin_database(settings))
    payload = {
        "login_name_mode": "manual",
        "reviewer_team_progress_visible": False,
        "reason": "test permissions",
    }

    with TestClient(create_app(settings)) as client:
        anonymous = client.patch("/v1/admin/settings", json=payload)
        reviewer = client.patch(
            "/v1/admin/settings",
            headers=admin_headers(seed, "Hassan", csrf=True),
            json=payload,
        )
        missing_csrf = client.patch(
            "/v1/admin/settings", headers=admin_headers(seed), json=payload
        )

    assert anonymous.status_code == 401
    assert reviewer.status_code == 403
    assert missing_csrf.status_code == 403
