import pytest

from app.config import Settings


ENVIRONMENT = {
    "DATABASE_URL": "sqlite+aiosqlite:///review-dev.sqlite3",
    "SESSION_COOKIE_NAME": "review_session",
    "SESSION_HOURS": "12",
    "SESSION_SECRET": "test-session-secret",
    "CSRF_SECRET": "test-csrf-secret",
    "RECEIPT_SECRET": "test-receipt-secret-that-is-separate-and-long-enough",
    "APP_ENV": "development",
}


def configure_environment(monkeypatch, **overrides):
    for name, value in {**ENVIRONMENT, **overrides}.items():
        monkeypatch.setenv(name, value)


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


def test_secure_cookie_defaults_to_prior_secure_behavior_when_absent(monkeypatch):
    configure_environment(monkeypatch)
    monkeypatch.delenv("SECURE_COOKIE", raising=False)

    assert Settings.from_env().secure_cookie is True


@pytest.mark.parametrize(
    ("spelling", "expected"),
    [
        ("true", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("false", False),
        ("FALSE", False),
        ("0", False),
        ("no", False),
        ("off", False),
    ],
)
def test_secure_cookie_accepts_only_explicit_safe_boolean_spellings(
    monkeypatch, spelling, expected
):
    configure_environment(monkeypatch, SECURE_COOKIE=spelling)

    assert Settings.from_env().secure_cookie is expected


@pytest.mark.parametrize("spelling", ["", "enabled", "2", "yes please"])
def test_secure_cookie_rejects_ambiguous_values(monkeypatch, spelling):
    configure_environment(monkeypatch, SECURE_COOKIE=spelling)

    with pytest.raises(ValueError, match="SECURE_COOKIE"):
        Settings.from_env()


def test_production_rejects_insecure_cookie_from_environment(monkeypatch):
    configure_environment(
        monkeypatch,
        APP_ENV="production",
        DATABASE_URL="postgresql+asyncpg://review:password@db/review",
        SECURE_COOKIE="false",
    )

    with pytest.raises(ValueError, match="SECURE_COOKIE"):
        Settings.from_env()
