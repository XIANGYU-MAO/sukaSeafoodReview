from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path) -> Settings:
    database_path = tmp_path / "review-test.sqlite3"
    return Settings(
        DATABASE_URL=f"sqlite+aiosqlite:///{database_path.as_posix()}",
        SESSION_COOKIE_NAME="review_session",
        SESSION_HOURS=12,
        SESSION_SECRET="test-session-secret",
        CSRF_SECRET="test-csrf-secret",
        RECEIPT_SECRET="test-receipt-secret-that-is-separate-and-long-enough",
        APP_ENV="test",
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client
