import asyncio
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.main import create_app
from app.models import Base, Candidate, Decision, Review, Session, Species, User
from app.services.auth import session_digest
from tests.review_support import candidate_record, review_headers


MEMBER_NAMES = ("Hassan", "Mao", "Xinhui", "Wahid", "Sharmaa", "Yiming")
FIXED_NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


async def seed_progress_database(settings, *, with_records=True, must_change=False):
    engine = create_async_engine(settings.DATABASE_URL)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        users = {
            name: User(
                name=name,
                role="admin" if name == "Mao" else "reviewer",
                password_hash="test",
                must_change_password=must_change and name == "Hassan",
            )
            for name in MEMBER_NAMES
        }
        active_species = Species(
            code="SF001",
            name_zh="测试鱼",
            name_en="Test fish",
            scientific_name="Piscis probatio",
        )
        inactive_species = Species(
            code="SF999",
            name_zh="停用鱼",
            name_en="Inactive fish",
            scientific_name="Piscis inactive",
            active=False,
        )
        db.add_all([*users.values(), active_species, inactive_species])
        await db.flush()
        tokens = {}
        for name, user in users.items():
            token = f"{name.lower()}-progress-token"
            tokens[name] = token
            db.add(
                Session(
                    user_id=user.id,
                    token_hash=session_digest(token),
                    password_version=user.password_version,
                    expires_at=datetime.now(timezone.utc) + timedelta(days=2),
                )
            )

        if with_records:
            candidates = [candidate_record(active_species.id, number) for number in range(1, 7)]
            candidates[3].current_reviewer_id = users["Xinhui"].id
            candidates[5].active = False
            inactive_species_candidate = candidate_record(inactive_species.id, 7)
            db.add_all([*candidates, inactive_species_candidate])
            await db.flush()
            start = FIXED_NOW.replace(hour=0, minute=0, second=0, microsecond=0)
            db.add_all(
                [
                    Review(
                        candidate_id=candidates[0].id,
                        reviewer_id=users["Hassan"].id,
                        decision=Decision.APPROVED,
                        whole_fish="YES",
                        exact_species_verified="YES",
                        created_at=start,
                    ),
                    Review(
                        candidate_id=candidates[1].id,
                        reviewer_id=users["Mao"].id,
                        decision=Decision.REJECTED,
                        rejection_reason="DUPLICATE",
                        whole_fish="REVIEW",
                        exact_species_verified="REVIEW",
                        created_at=start - timedelta(microseconds=1),
                    ),
                    Review(
                        candidate_id=candidates[2].id,
                        reviewer_id=users["Hassan"].id,
                        decision=Decision.REJECTED,
                        rejection_reason="WRONG_SPECIES",
                        whole_fish="REVIEW",
                        exact_species_verified="NO",
                        is_current=False,
                        created_at=start + timedelta(hours=23, minutes=59),
                    ),
                    Review(
                        candidate_id=candidates[2].id,
                        reviewer_id=users["Yiming"].id,
                        decision=Decision.APPROVED,
                        whole_fish="YES",
                        exact_species_verified="YES",
                        created_at=start + timedelta(days=1),
                    ),
                    Review(
                        candidate_id=candidates[5].id,
                        reviewer_id=users["Sharmaa"].id,
                        decision=Decision.REJECTED,
                        rejection_reason="TOO_OCCLUDED",
                        whole_fish="REVIEW",
                        exact_species_verified="REVIEW",
                        created_at=start - timedelta(days=1),
                    ),
                    Review(
                        candidate_id=inactive_species_candidate.id,
                        reviewer_id=users["Wahid"].id,
                        decision=Decision.UNSURE,
                        whole_fish="REVIEW",
                        exact_species_verified="REVIEW",
                        created_at=start,
                    ),
                ]
            )
        await db.commit()
    await engine.dispose()
    return tokens


def test_progress_uses_active_current_state_and_credits_all_member_attempts(settings, monkeypatch):
    tokens = asyncio.run(seed_progress_database(settings))
    monkeypatch.setattr("app.services.progress.utc_now", lambda: FIXED_NOW)

    with TestClient(create_app(settings)) as client:
        responses = [
            client.get("/v1/progress", headers=review_headers(tokens[name]))
            for name in MEMBER_NAMES
        ]

    assert [response.status_code for response in responses] == [200] * 6
    assert all(response.json() == responses[0].json() for response in responses)
    assert responses[0].json() == {
        "total": 5,
        "reviewed": 3,
        "pending": 1,
        "currently_open": 1,
        "completion_percent": 60.0,
        "decision_counts": {"APPROVED": 2, "REJECTED": 1, "UNSURE": 0},
        "today_count": 1,
        "members": [
            {"name": "Hassan", "completed": 2, "approved": 1, "rejected": 1, "unsure": 0, "today": 2},
            {"name": "Mao", "completed": 1, "approved": 0, "rejected": 1, "unsure": 0, "today": 0},
            {"name": "Xinhui", "completed": 0, "approved": 0, "rejected": 0, "unsure": 0, "today": 0},
            {"name": "Wahid", "completed": 1, "approved": 0, "rejected": 0, "unsure": 1, "today": 1},
            {"name": "Sharmaa", "completed": 1, "approved": 0, "rejected": 1, "unsure": 0, "today": 0},
            {"name": "Yiming", "completed": 1, "approved": 1, "rejected": 0, "unsure": 0, "today": 0},
        ],
    }


def test_progress_empty_dataset_returns_stable_zero_shape(settings, monkeypatch):
    tokens = asyncio.run(seed_progress_database(settings, with_records=False))
    monkeypatch.setattr("app.services.progress.utc_now", lambda: FIXED_NOW)

    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/v1/progress", headers=review_headers(tokens["Hassan"])
        )

    assert response.status_code == 200
    assert response.json()["completion_percent"] == 0.0
    assert response.json()["decision_counts"] == {
        "APPROVED": 0,
        "REJECTED": 0,
        "UNSURE": 0,
    }
    assert response.json()["members"] == [
        {"name": name, "completed": 0, "approved": 0, "rejected": 0, "unsure": 0, "today": 0}
        for name in MEMBER_NAMES
    ]


def test_progress_exposes_only_aggregate_fields(settings, monkeypatch):
    tokens = asyncio.run(seed_progress_database(settings))
    monkeypatch.setattr("app.services.progress.utc_now", lambda: FIXED_NOW)

    with TestClient(create_app(settings)) as client:
        payload = client.get(
            "/v1/progress", headers=review_headers(tokens["Hassan"])
        ).json()

    forbidden = {
        "candidate_id",
        "review_id",
        "reviewer_id",
        "notes",
        "rejection_reason",
        "preview_url",
        "original_url",
        "source_url",
        "reviews",
        "history",
    }

    def all_keys(value):
        if isinstance(value, dict):
            return set(value).union(*(all_keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(all_keys(item) for item in value), set())
        return set()

    assert forbidden.isdisjoint(all_keys(payload))


def test_progress_requires_auth_and_completed_first_password_change(settings, monkeypatch):
    tokens = asyncio.run(seed_progress_database(settings, with_records=False, must_change=True))
    monkeypatch.setattr("app.services.progress.utc_now", lambda: FIXED_NOW)

    with TestClient(create_app(settings)) as client:
        unauthenticated = client.get("/v1/progress")
        password_change_required = client.get(
            "/v1/progress", headers=review_headers(tokens["Hassan"])
        )
        admin = client.get("/v1/progress", headers=review_headers(tokens["Mao"]))

    assert unauthenticated.status_code == 401
    assert password_change_required.status_code == 403
    assert admin.status_code == 200
