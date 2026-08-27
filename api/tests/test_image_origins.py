import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.image_origins import (
    DEFAULT_IMAGE_ORIGIN_ALLOWLIST,
    ImageOriginError,
    normalize_exact_image_hostname,
)
from app.config import Settings
from app.models import ImageOriginApproval
from app.services.origins import effective_image_origin_allowlist
from tests.admin_support import seed_admin_database


def test_verified_nhm_origin_is_built_in_for_server_and_local_tool():
    from sukaseafood_sync.image_origins import (
        DEFAULT_IMAGE_ORIGIN_ALLOWLIST as LOCAL_DEFAULTS,
    )

    assert "data.nhm.ac.uk" in DEFAULT_IMAGE_ORIGIN_ALLOWLIST
    assert "data.nhm.ac.uk" in LOCAL_DEFAULTS


def test_environment_origin_extension_cannot_remove_built_in_origins():
    settings = Settings(
        DATABASE_URL="sqlite+aiosqlite:///review.sqlite3",
        SESSION_COOKIE_NAME="review_session",
        SESSION_HOURS=12,
        SESSION_SECRET="test-session-secret",
        CSRF_SECRET="test-csrf-secret",
        APP_ENV="test",
        IMAGE_ORIGIN_ALLOWLIST=("images.example.org",),
    )

    assert "data.nhm.ac.uk" in settings.IMAGE_ORIGIN_ALLOWLIST
    assert "images.example.org" in settings.IMAGE_ORIGIN_ALLOWLIST


@pytest.mark.parametrize(
    "value",
    [".example.org", "*.example.org", "127.0.0.1", "localhost", "bad host.test"],
)
def test_exact_origin_rejects_suffix_wildcard_literal_and_malformed_hosts(value):
    with pytest.raises(ImageOriginError):
        normalize_exact_image_hostname(value)


def test_exact_origin_normalizes_case_and_trailing_dot():
    assert normalize_exact_image_hostname("Data.Example.ORG.") == "data.example.org"


def test_persisted_origin_is_combined_once_with_configured_policy(settings):
    seed = seed_admin_database(settings, candidate_count=0)
    seeded = asyncio.run(seed)

    async def operation():
        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            session.add_all(
                [
                    ImageOriginApproval(
                        hostname="z.example.org", approved_by_id=seeded.user_ids["Mao"]
                    ),
                    ImageOriginApproval(
                        hostname="a.example.org", approved_by_id=seeded.user_ids["Mao"]
                    ),
                ]
            )
            await session.commit()
            effective = await effective_image_origin_allowlist(
                session, ("data.nhm.ac.uk", "a.example.org")
            )
        await engine.dispose()
        return effective

    assert asyncio.run(operation()) == (
        "data.nhm.ac.uk",
        "a.example.org",
        "z.example.org",
    )
