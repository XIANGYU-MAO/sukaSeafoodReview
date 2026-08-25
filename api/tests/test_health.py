import pytest

from app.config import Settings


def test_health(client):
    response = client.get("/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_test_environment_allows_an_isolated_sqlite_database(tmp_path):
    settings = Settings(
        DATABASE_URL=f"sqlite+aiosqlite:///{(tmp_path / 'review-test.sqlite3').as_posix()}",
        SESSION_COOKIE_NAME="review_session",
        SESSION_HOURS=12,
        SESSION_SECRET="test-session-secret",
        CSRF_SECRET="test-csrf-secret",
        APP_ENV="test",
    )

    assert settings.DATABASE_URL.startswith("sqlite+")


def test_production_rejects_sqlite_database():
    with pytest.raises(ValueError, match="SQLite"):
        Settings(
            DATABASE_URL="sqlite+aiosqlite:///review.sqlite3",
            SESSION_COOKIE_NAME="review_session",
            SESSION_HOURS=12,
            SESSION_SECRET="production-session-secret",
            CSRF_SECRET="production-csrf-secret",
            APP_ENV="production",
        )
