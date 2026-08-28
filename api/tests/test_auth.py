import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.main import create_app
from app.models import Base, Session, User


TEST_PASSWORD = "test-only-current-password"
NEW_TEST_PASSWORD = "test-only-new-password"
FIXED_NAMES = ["Hassan", "Mao", "Xinhui", "Wahid", "Sharmaa", "Yiming"]


async def create_schema(database_url: str) -> None:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()


async def insert_fixed_users(database_url: str, password_hash: str) -> None:
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add_all(
            [
                User(
                    name=name,
                    role=role,
                    password_hash=password_hash,
                    must_change_password=True,
                )
                for name, role in (
                    ("Hassan", "reviewer"),
                    ("Mao", "admin"),
                    ("Xinhui", "reviewer"),
                    ("Wahid", "reviewer"),
                    ("Sharmaa", "reviewer"),
                    ("Yiming", "reviewer"),
                )
            ]
        )
        await session.commit()
    await engine.dispose()


async def update_user(database_url: str, name: str, **values) -> None:
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.name == name))
        assert user is not None
        for field, value in values.items():
            setattr(user, field, value)
        await session.commit()
    await engine.dispose()


async def update_session(database_url: str, token_hash: str, **values) -> None:
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        record = await session.scalar(
            select(Session).where(Session.token_hash == token_hash)
        )
        assert record is not None
        for field, value in values.items():
            setattr(record, field, value)
        await session.commit()
    await engine.dispose()


async def load_auth_rows(database_url: str) -> tuple[list[User], list[Session]]:
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        users = list((await session.scalars(select(User).order_by(User.name))).all())
        sessions = list((await session.scalars(select(Session))).all())
    await engine.dispose()
    return users, sessions


@pytest.fixture(scope="session")
def test_password_hash() -> str:
    from app.services.auth import hash_password

    return hash_password(TEST_PASSWORD)


@pytest.fixture
def auth_settings(settings):
    values = vars(settings).copy()
    values["secure_cookie"] = False
    return SimpleNamespace(**values)


@pytest.fixture
def auth_client(auth_settings) -> TestClient:
    asyncio.run(create_schema(auth_settings.DATABASE_URL))
    with TestClient(
        create_app(auth_settings), base_url="https://testserver"
    ) as test_client:
        yield test_client


@pytest.fixture
def seeded_users(auth_settings, auth_client, test_password_hash):
    asyncio.run(insert_fixed_users(auth_settings.DATABASE_URL, test_password_hash))
    return {name: TEST_PASSWORD for name in FIXED_NAMES}


def login(client: TestClient, name: str = "Hassan", password: str = TEST_PASSWORD):
    return client.post("/v1/auth/login", json={"name": name, "password": password})


def session_cookie(client: TestClient) -> str:
    return client.cookies.get("review_session")


def session_headers(token: str, csrf: str | None = None) -> dict[str, str]:
    headers = {"cookie": f"review_session={token}"}
    if csrf is not None:
        headers["X-CSRF-Token"] = csrf
    return headers


def settings_with(settings, **changes):
    values = vars(settings).copy()
    values.update(changes)
    return SimpleNamespace(**values)


def unknown_login(client: TestClient, forwarded_for: str | None = None):
    headers = {}
    if forwarded_for is not None:
        headers["X-Forwarded-For"] = forwarded_for
    return client.post(
        "/v1/auth/login",
        json={"name": "NotAUser", "password": "wrong-password"},
        headers=headers,
    )


def test_login_names_are_fixed_and_ordered(auth_client):
    response = auth_client.get("/v1/auth/names")

    assert response.status_code == 200
    assert response.json() == {
        "login_name_mode": "choices",
        "names": [{"name": name} for name in FIXED_NAMES],
    }


def test_registration_route_does_not_exist(auth_client):
    response = auth_client.post(
        "/v1/auth/register", json={"name": "Anyone", "password": "irrelevant"}
    )

    assert response.status_code == 404


def test_password_hash_is_argon2id_and_malformed_hashes_fail_closed():
    from app.services.auth import hash_password, verify_password

    encoded = hash_password(TEST_PASSWORD)

    assert encoded.startswith("$argon2id$")
    assert TEST_PASSWORD not in encoded
    assert verify_password(TEST_PASSWORD, encoded) is True
    assert verify_password("wrong-password", encoded) is False
    assert verify_password(TEST_PASSWORD, "not-an-argon2-hash") is False


def test_temporary_password_login_returns_public_session_state(
    auth_client, seeded_users
):
    response = login(auth_client, password=seeded_users["Hassan"])

    assert response.status_code == 200
    assert response.json().keys() == {
        "id",
        "name",
        "role",
        "must_change_password",
        "csrf_token",
        "team_progress_visible",
    }
    assert response.json()["name"] == "Hassan"
    assert response.json()["role"] == "reviewer"
    assert response.json()["must_change_password"] is True
    assert response.json()["csrf_token"]
    assert response.json()["team_progress_visible"] is True
    assert "password_hash" not in response.text.lower()
    assert TEST_PASSWORD not in response.text


def test_mao_is_the_only_admin(auth_client, seeded_users):
    roles = {}
    for name in FIXED_NAMES:
        response = login(auth_client, name=name, password=seeded_users[name])
        assert response.status_code == 200
        roles[name] = response.json()["role"]

    assert roles == {
        "Hassan": "reviewer",
        "Mao": "admin",
        "Xinhui": "reviewer",
        "Wahid": "reviewer",
        "Sharmaa": "reviewer",
        "Yiming": "reviewer",
    }


def test_login_sets_production_cookie_attributes_and_test_override(
    auth_client, auth_settings, seeded_users
):
    secure_settings = SimpleNamespace(**vars(auth_settings))
    secure_settings.secure_cookie = True
    with TestClient(
        create_app(secure_settings), base_url="https://testserver"
    ) as secure_client:
        secure_response = login(secure_client)
    insecure_response = login(auth_client)

    secure_cookie = secure_response.headers["set-cookie"].lower()
    insecure_cookie = insecure_response.headers["set-cookie"].lower()
    assert "httponly" in secure_cookie
    assert "samesite=lax" in secure_cookie
    assert "path=/sukaseafood" in secure_cookie
    assert "secure" in secure_cookie
    assert "httponly" in insecure_cookie
    assert "samesite=lax" in insecure_cookie
    assert "path=/sukaseafood" in insecure_cookie
    assert "secure" not in insecure_cookie


def test_trusted_proxy_uses_forwarded_client_for_independent_rate_limits(
    auth_settings,
):
    asyncio.run(create_schema(auth_settings.DATABASE_URL))
    proxy_settings = settings_with(
        auth_settings, TRUSTED_PROXY_CIDRS=("10.20.0.0/24",)
    )
    with TestClient(
        create_app(proxy_settings), client=("10.20.0.5", 50000)
    ) as proxy_client:
        statuses = [
            unknown_login(proxy_client, "198.51.100.10").status_code
            for _ in range(5)
        ]
        other_client = unknown_login(proxy_client, "198.51.100.11")

    assert statuses == [401, 401, 401, 401, 429]
    assert other_client.status_code == 401


def test_untrusted_peer_cannot_spoof_forwarded_addresses_to_evade_limit(
    auth_settings,
):
    asyncio.run(create_schema(auth_settings.DATABASE_URL))
    proxy_settings = settings_with(
        auth_settings, TRUSTED_PROXY_CIDRS=("10.20.0.0/24",)
    )
    with TestClient(
        create_app(proxy_settings), client=("203.0.113.40", 50000)
    ) as direct_client:
        statuses = [
            unknown_login(direct_client, f"198.51.100.{index}").status_code
            for index in range(1, 6)
        ]

    assert statuses == [401, 401, 401, 401, 429]


def test_trusted_proxy_rejects_malformed_forwarded_chain(auth_settings):
    asyncio.run(create_schema(auth_settings.DATABASE_URL))
    proxy_settings = settings_with(
        auth_settings, TRUSTED_PROXY_CIDRS=("10.20.0.0/24",)
    )
    malformed = [
        "not-an-ip",
        "198.51.100.1,",
        "unknown",
        "[2001:db8::1]",
        "198.51.100.2:1234",
    ]
    with TestClient(
        create_app(proxy_settings), client=("10.20.0.5", 50000)
    ) as proxy_client:
        statuses = [
            unknown_login(proxy_client, forwarded).status_code
            for forwarded in malformed
        ]

    assert statuses == [401, 401, 401, 401, 429]


def test_scoped_ipv6_forwarded_values_cannot_evade_rate_limit(auth_settings):
    asyncio.run(create_schema(auth_settings.DATABASE_URL))
    proxy_settings = settings_with(
        auth_settings, TRUSTED_PROXY_CIDRS=("10.20.0.0/24",)
    )
    with TestClient(
        create_app(proxy_settings), client=("10.20.0.5", 50000)
    ) as proxy_client:
        statuses = [
            unknown_login(
                proxy_client, f"2001:db8::1%attacker-scope-{index}"
            ).status_code
            for index in range(1, 7)
        ]

    assert statuses == [401, 401, 401, 401, 429, 429]


def test_ip_normalization_unifies_mapped_addresses_and_socket_scopes(
    auth_settings,
):
    from app.services.auth import parse_trusted_proxy_networks, resolve_client_address

    networks = parse_trusted_proxy_networks(("10.20.0.0/24",))
    assert (
        resolve_client_address("fe80::1%scope-a", None, ())
        == resolve_client_address("fe80::1%scope-b", None, ())
        == "fe80::1"
    )
    assert (
        resolve_client_address("::ffff:10.20.0.5", "::ffff:192.0.2.1", networks)
        == "192.0.2.1"
    )

    asyncio.run(create_schema(auth_settings.DATABASE_URL))
    proxy_settings = settings_with(
        auth_settings, TRUSTED_PROXY_CIDRS=("10.20.0.0/24",)
    )
    with TestClient(
        create_app(proxy_settings), client=("::ffff:10.20.0.5", 50000)
    ) as proxy_client:
        statuses = [
            unknown_login(
                proxy_client,
                "::ffff:192.0.2.1" if index % 2 else "192.0.2.1",
            ).status_code
            for index in range(1, 6)
        ]

    assert statuses == [401, 401, 401, 401, 429]


def test_trusted_multi_hop_chain_stops_at_first_untrusted_address(auth_settings):
    asyncio.run(create_schema(auth_settings.DATABASE_URL))
    proxy_settings = settings_with(
        auth_settings, TRUSTED_PROXY_CIDRS=("10.20.0.0/24",)
    )
    with TestClient(
        create_app(proxy_settings), client=("10.20.0.5", 50000)
    ) as proxy_client:
        statuses = [
            unknown_login(
                proxy_client,
                f"198.51.100.{index}, 192.0.2.99, 10.20.0.9",
            ).status_code
            for index in range(1, 6)
        ]
        other_client = unknown_login(
            proxy_client, "198.51.100.1, 192.0.2.100, 10.20.0.9"
        )

    assert statuses == [401, 401, 401, 401, 429]
    assert other_client.status_code == 401


def test_trusted_proxy_configuration_is_explicit_and_rejects_wildcards(monkeypatch):
    base = {
        "DATABASE_URL": "postgresql+asyncpg://db.example/review",
        "SESSION_COOKIE_NAME": "review_session",
        "SESSION_HOURS": "12",
        "SESSION_SECRET": "test-session-secret",
        "CSRF_SECRET": "test-csrf-secret",
        "APP_ENV": "production",
    }
    for name, value in base.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "10.20.0.0/24,2001:db8::5")

    parsed = Settings.from_env()

    assert parsed.TRUSTED_PROXY_CIDRS == ("10.20.0.0/24", "2001:db8::5")
    with pytest.raises(ValueError, match="proxy|CIDR|address"):
        Settings(
            DATABASE_URL=base["DATABASE_URL"],
            SESSION_COOKIE_NAME=base["SESSION_COOKIE_NAME"],
            SESSION_HOURS=12,
            SESSION_SECRET=base["SESSION_SECRET"],
            CSRF_SECRET=base["CSRF_SECRET"],
            APP_ENV=base["APP_ENV"],
            TRUSTED_PROXY_CIDRS=("*",),
        )


def test_production_api_requires_an_explicit_trusted_proxy_network():
    settings = Settings(
        DATABASE_URL="postgresql+asyncpg://db.example/review",
        SESSION_COOKIE_NAME="review_session",
        SESSION_HOURS=12,
        SESSION_SECRET="test-session-secret",
        CSRF_SECRET="test-csrf-secret",
        APP_ENV="production",
    )
    with pytest.raises(ValueError, match="TRUSTED_PROXY_CIDRS"):
        create_app(settings)

    configured = Settings(
        DATABASE_URL="postgresql+asyncpg://db.example/review",
        SESSION_COOKIE_NAME="review_session",
        SESSION_HOURS=12,
        SESSION_SECRET="test-session-secret",
        CSRF_SECRET="test-csrf-secret",
        RECEIPT_SECRET="test-receipt-secret-that-is-independent-and-long",
        APP_ENV="production",
        TRUSTED_PROXY_CIDRS=("10.20.0.0/24",),
    )
    with TestClient(create_app(configured)) as production_client:
        assert production_client.get("/v1/health").status_code == 200

    development = Settings(
        DATABASE_URL="sqlite+aiosqlite:///review.sqlite3",
        SESSION_COOKIE_NAME="review_session",
        SESSION_HOURS=12,
        SESSION_SECRET="test-session-secret",
        CSRF_SECRET="test-csrf-secret",
        APP_ENV="test",
    )
    assert development.TRUSTED_PROXY_CIDRS == ()


def test_production_commands_complete_database_semantics_without_proxy_env(
    monkeypatch, settings
):
    from app.commands import reset_password as reset_command
    from app.commands import seed_users as seed_command
    from app.services.auth import verify_password

    environment = {
        "DATABASE_URL": "postgresql+asyncpg://db.example/review",
        "SESSION_COOKIE_NAME": "review_session",
        "SESSION_HOURS": "12",
        "SESSION_SECRET": "test-session-secret",
        "CSRF_SECRET": "test-csrf-secret",
        "APP_ENV": "production",
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("TRUSTED_PROXY_CIDRS", raising=False)
    production = Settings.from_env()
    assert production.TRUSTED_PROXY_CIDRS == ()

    asyncio.run(create_schema(settings.DATABASE_URL))
    monkeypatch.setattr(seed_command, "get_settings", lambda: production)
    monkeypatch.setattr(reset_command, "get_settings", lambda: production)
    monkeypatch.setattr(
        seed_command,
        "create_database_engine",
        lambda _: create_async_engine(settings.DATABASE_URL),
    )
    monkeypatch.setattr(
        reset_command,
        "create_database_engine",
        lambda _: create_async_engine(settings.DATABASE_URL),
    )

    assert asyncio.run(seed_command.seed_users(print_once=False)) == []
    temporary_password = asyncio.run(reset_command.reset_password("Mao"))
    users, _ = asyncio.run(load_auth_rows(settings.DATABASE_URL))
    mao = next(user for user in users if user.name == "Mao")

    assert [user.name for user in users] == sorted(FIXED_NAMES)
    assert verify_password(temporary_password, mao.password_hash)
    assert mao.must_change_password is True
    assert mao.password_version == 2


@pytest.mark.parametrize("wildcard", ["0.0.0.0/0", "::/0"])
def test_trusted_proxy_configuration_rejects_full_network_wildcards(wildcard):
    with pytest.raises(ValueError, match="proxy|CIDR|address"):
        Settings(
            DATABASE_URL="postgresql+asyncpg://db.example/review",
            SESSION_COOKIE_NAME="review_session",
            SESSION_HOURS=12,
            SESSION_SECRET="test-session-secret",
            CSRF_SECRET="test-csrf-secret",
            APP_ENV="production",
            TRUSTED_PROXY_CIDRS=(wildcard,),
        )


@pytest.mark.parametrize(
    "wildcard",
    [
        "::ffff:0:0/96",
        "::ffff:0.0.0.0/96",
        "0:0:0:0:0:ffff:0:0/96",
    ],
)
def test_trusted_proxy_configuration_rejects_mapped_ipv4_wildcards(wildcard):
    with pytest.raises(ValueError, match="proxy|CIDR|network"):
        Settings(
            DATABASE_URL="postgresql+asyncpg://db.example/review",
            SESSION_COOKIE_NAME="review_session",
            SESSION_HOURS=12,
            SESSION_SECRET="test-session-secret",
            CSRF_SECRET="test-csrf-secret",
            APP_ENV="production",
            TRUSTED_PROXY_CIDRS=(wildcard,),
        )


def test_narrow_mapped_proxy_network_is_normalized_and_bounded():
    from app.services.auth import parse_trusted_proxy_networks, resolve_client_address

    networks = parse_trusted_proxy_networks(("::ffff:192.0.2.0/120",))

    assert [str(network) for network in networks] == ["192.0.2.0/24"]
    assert (
        resolve_client_address(
            "::ffff:192.0.2.10", "198.51.100.10", networks
        )
        == "198.51.100.10"
    )
    assert (
        resolve_client_address(
            "::ffff:192.0.3.10", "198.51.100.10", networks
        )
        == "192.0.3.10"
    )


def test_database_stores_only_password_hash_and_session_token_digest(
    auth_client, auth_settings, seeded_users
):
    from app.services.auth import session_digest

    response = login(auth_client)
    raw_token = session_cookie(auth_client)
    users, sessions = asyncio.run(load_auth_rows(auth_settings.DATABASE_URL))
    hassan = next(user for user in users if user.name == "Hassan")

    assert response.status_code == 200
    assert raw_token
    assert hassan.password_hash != TEST_PASSWORD
    assert TEST_PASSWORD not in hassan.password_hash
    assert len(sessions) == 1
    assert sessions[0].token_hash == session_digest(raw_token)
    assert sessions[0].token_hash != raw_token
    created_at = sessions[0].created_at.replace(tzinfo=timezone.utc)
    expires_at = sessions[0].expires_at.replace(tzinfo=timezone.utc)
    assert timedelta(hours=11, minutes=59) < expires_at - created_at
    assert expires_at - created_at < timedelta(hours=12, minutes=1)
    assert sessions[0].password_version == hassan.password_version == 1


def test_cookie_session_survives_me_and_me_returns_session_bound_csrf(
    auth_client, seeded_users
):
    first_login = login(auth_client)
    first_token = session_cookie(auth_client)
    first_csrf = first_login.json()["csrf_token"]

    me = auth_client.get("/v1/auth/me", headers=session_headers(first_token))

    assert me.status_code == 200
    assert me.json()["name"] == "Hassan"
    assert me.json()["csrf_token"] == first_csrf


@pytest.mark.parametrize("state", ["expired", "revoked"])
def test_me_rejects_expired_or_revoked_database_session(
    state, auth_client, auth_settings, seeded_users
):
    from app.services.auth import session_digest

    assert login(auth_client).status_code == 200
    raw_token = session_cookie(auth_client)
    if state == "expired":
        values = {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
    else:
        values = {"revoked_at": datetime.now(timezone.utc)}
    asyncio.run(
        update_session(auth_settings.DATABASE_URL, session_digest(raw_token), **values)
    )

    response = auth_client.get(
        "/v1/auth/me", headers=session_headers(raw_token)
    )

    assert response.status_code == 401


def test_me_rejects_an_inactive_user_even_with_a_live_session(
    auth_client, auth_settings, seeded_users
):
    assert login(auth_client).status_code == 200
    raw_token = session_cookie(auth_client)
    asyncio.run(update_user(auth_settings.DATABASE_URL, "Hassan", active=False))

    response = auth_client.get(
        "/v1/auth/me", headers=session_headers(raw_token)
    )

    assert response.status_code == 401


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/v1/auth/logout", None),
        (
            "/v1/auth/change-password",
            {"current_password": TEST_PASSWORD, "new_password": NEW_TEST_PASSWORD},
        ),
    ],
)
@pytest.mark.parametrize("csrf", [None, "invalid-csrf-token"])
def test_authenticated_post_routes_reject_missing_or_bad_csrf(
    path, body, csrf, auth_client, seeded_users
):
    login_response = login(auth_client)
    raw_token = session_cookie(auth_client)
    headers = session_headers(raw_token, csrf)

    response = auth_client.post(path, json=body, headers=headers)

    assert login_response.status_code == 200
    assert response.status_code == 403


def test_csrf_token_from_another_session_is_rejected(
    auth_client, seeded_users
):
    first = login(auth_client)
    first_token = session_cookie(auth_client)
    second = login(auth_client)

    response = auth_client.post(
        "/v1/auth/logout",
        headers=session_headers(first_token, second.json()["csrf_token"]),
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert response.status_code == 403


def test_logout_with_valid_csrf_revokes_immediately_and_clears_cookie(
    auth_client, seeded_users
):
    login_response = login(auth_client)
    raw_token = session_cookie(auth_client)

    logout = auth_client.post(
        "/v1/auth/logout",
        headers=session_headers(raw_token, login_response.json()["csrf_token"]),
    )
    me = auth_client.get("/v1/auth/me", headers=session_headers(raw_token))

    deletion_cookie = logout.headers["set-cookie"].lower()
    assert logout.status_code == 204
    assert "path=/sukaseafood" in deletion_cookie
    assert "max-age=0" in deletion_cookie
    assert me.status_code == 401


def test_change_password_revokes_every_session_and_old_password(
    auth_client, seeded_users
):
    first = login(auth_client)
    first_token = session_cookie(auth_client)
    second = login(auth_client)
    second_token = session_cookie(auth_client)

    changed = auth_client.post(
        "/v1/auth/change-password",
        json={
            "current_password": TEST_PASSWORD,
            "new_password": NEW_TEST_PASSWORD,
        },
        headers=session_headers(first_token, first.json()["csrf_token"]),
    )

    first_me = auth_client.get(
        "/v1/auth/me", headers=session_headers(first_token)
    )
    second_me = auth_client.get(
        "/v1/auth/me", headers=session_headers(second_token)
    )
    old_login = login(auth_client, password=TEST_PASSWORD)
    new_login = login(auth_client, password=NEW_TEST_PASSWORD)
    users, sessions = asyncio.run(
        load_auth_rows(auth_client.app.state.settings.DATABASE_URL)
    )
    hassan = next(user for user in users if user.name == "Hassan")

    deletion_cookie = changed.headers["set-cookie"].lower()
    assert second.status_code == 200
    assert changed.status_code == 204
    assert "path=/sukaseafood" in deletion_cookie
    assert "max-age=0" in deletion_cookie
    assert first_me.status_code == 401
    assert second_me.status_code == 401
    assert old_login.status_code == 401
    assert new_login.status_code == 200
    assert new_login.json()["must_change_password"] is False
    assert hassan.password_version == 2
    assert all(
        session.password_version == 1 and session.revoked_at is not None
        for session in sessions
        if session.password_version == 1
    )
    current_sessions = [
        session for session in sessions if session.password_version == 2
    ]
    assert len(current_sessions) == 1
    assert current_sessions[0].revoked_at is None


def test_change_password_requires_the_current_password(auth_client, seeded_users):
    login_response = login(auth_client)
    raw_token = session_cookie(auth_client)

    response = auth_client.post(
        "/v1/auth/change-password",
        json={
            "current_password": "wrong-current-password",
            "new_password": NEW_TEST_PASSWORD,
        },
        headers=session_headers(raw_token, login_response.json()["csrf_token"]),
    )

    assert response.status_code == 400
    assert login(auth_client, password=TEST_PASSWORD).status_code == 200
    assert login(auth_client, password=NEW_TEST_PASSWORD).status_code == 401


@pytest.mark.parametrize("new_password", ["x", "x" * 11, "x" * 129])
def test_change_password_rejects_out_of_policy_lengths_without_mutation(
    auth_client, auth_settings, seeded_users, new_password
):
    login_response = login(auth_client)
    raw_token = session_cookie(auth_client)
    before_users, before_sessions = asyncio.run(
        load_auth_rows(auth_settings.DATABASE_URL)
    )

    response = auth_client.post(
        "/v1/auth/change-password",
        json={"current_password": TEST_PASSWORD, "new_password": new_password},
        headers=session_headers(raw_token, login_response.json()["csrf_token"]),
    )
    after_users, after_sessions = asyncio.run(load_auth_rows(auth_settings.DATABASE_URL))

    assert response.status_code == 422
    assert [
        (user.password_hash, user.password_version, user.must_change_password)
        for user in after_users
    ] == [
        (user.password_hash, user.password_version, user.must_change_password)
        for user in before_users
    ]
    assert [
        (session.token_hash, session.password_version, session.revoked_at)
        for session in after_sessions
    ] == [
        (session.token_hash, session.password_version, session.revoked_at)
        for session in before_sessions
    ]
    assert auth_client.get(
        "/v1/auth/me", headers=session_headers(raw_token)
    ).status_code == 200


@pytest.mark.parametrize("new_password", ["x" * 11, "x" * 129])
def test_password_service_enforces_the_same_length_contract(new_password):
    from app.services.auth import PasswordPolicyError, require_valid_new_password

    with pytest.raises(PasswordPolicyError):
        require_valid_new_password(new_password)


def test_change_password_rejects_same_password_without_changing_state(
    auth_client, auth_settings, seeded_users
):
    login_response = login(auth_client)
    raw_token = session_cookie(auth_client)

    response = auth_client.post(
        "/v1/auth/change-password",
        json={
            "current_password": TEST_PASSWORD,
            "new_password": TEST_PASSWORD,
        },
        headers=session_headers(raw_token, login_response.json()["csrf_token"]),
    )
    me = auth_client.get("/v1/auth/me", headers=session_headers(raw_token))
    users, sessions = asyncio.run(load_auth_rows(auth_settings.DATABASE_URL))
    hassan = next(user for user in users if user.name == "Hassan")

    assert response.status_code == 400
    assert me.status_code == 200
    assert hassan.must_change_password is True
    assert hassan.password_version == 1
    assert sessions[0].revoked_at is None


def test_wrong_unknown_and_disabled_logins_have_the_same_generic_failure(
    auth_client, auth_settings, seeded_users
):
    wrong = login(auth_client, password="wrong-password")
    unknown = login(auth_client, name="NotAUser", password="wrong-password")
    asyncio.run(update_user(auth_settings.DATABASE_URL, "Hassan", active=False))
    disabled = login(auth_client)

    assert wrong.status_code == unknown.status_code == disabled.status_code == 401
    assert wrong.json() == unknown.json() == disabled.json()
    assert "Hassan" not in wrong.text
    assert "disabled" not in disabled.text.lower()


def test_repeated_failures_are_limited_and_success_resets_failure_state(
    auth_client, seeded_users
):
    for _ in range(2):
        assert login(auth_client, password="wrong-password").status_code == 401
    assert login(auth_client).status_code == 200
    for _ in range(4):
        assert login(auth_client, password="wrong-password").status_code == 401

    limited = login(auth_client, password="wrong-password")

    assert limited.status_code == 429
    assert "password" not in limited.text.lower()


def test_expired_account_lock_resets_count_before_a_new_wrong_password(
    auth_client, auth_settings, seeded_users
):
    asyncio.run(
        update_user(
            auth_settings.DATABASE_URL,
            "Hassan",
            failed_login_count=5,
            locked_until=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
    )

    response = login(auth_client, password="wrong-password")
    users, _ = asyncio.run(load_auth_rows(auth_settings.DATABASE_URL))
    hassan = next(user for user in users if user.name == "Hassan")

    assert response.status_code == 401
    assert hassan.failed_login_count == 1
    assert hassan.locked_until is None


def test_expired_account_lock_resets_count_on_success(
    auth_client, auth_settings, seeded_users
):
    asyncio.run(
        update_user(
            auth_settings.DATABASE_URL,
            "Hassan",
            failed_login_count=5,
            locked_until=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
    )

    response = login(auth_client)
    users, _ = asyncio.run(load_auth_rows(auth_settings.DATABASE_URL))
    hassan = next(user for user in users if user.name == "Hassan")

    assert response.status_code == 200
    assert hassan.failed_login_count == 0
    assert hassan.locked_until is None


def test_login_limiter_expires_low_count_entries_and_evicts_to_capacity():
    from app.services.auth import LoginLimiter

    start = datetime(2026, 8, 26, tzinfo=timezone.utc)
    limiter = LoginLimiter(window=timedelta(seconds=10), max_entries=2)

    limiter.record_failure("198.51.100.1", start)
    limiter.record_failure("198.51.100.2", start + timedelta(seconds=1))
    limiter.record_failure("198.51.100.3", start + timedelta(seconds=2))
    assert limiter.tracked_count == 2
    assert limiter.is_tracked("198.51.100.1") is False

    limiter.record_failure("198.51.100.4", start + timedelta(seconds=12))
    assert limiter.tracked_count == 1
    assert limiter.is_tracked("198.51.100.4") is True


def test_user_lock_queries_compile_to_postgresql_for_update():
    from app.services.auth import user_by_id_for_update, user_by_name_for_update

    statements = [
        user_by_name_for_update("Hassan"),
        user_by_id_for_update(User().id),
    ]

    for statement in statements:
        compiled = str(statement.compile(dialect=postgresql.dialect()))
        assert "FOR UPDATE" in compiled


async def insert_stale_session_after_password_change(database_url: str) -> str:
    from app.services.auth import session_digest

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    stale_token = "test-only-stale-browser-token"
    async with session_factory() as db:
        hassan = await db.scalar(select(User).where(User.name == "Hassan"))
        assert hassan is not None
        old_version = hassan.password_version
        hassan.password_hash = "new-password-hash-after-reset"
        hassan.password_version += 1
        await db.commit()

        db.add(
            Session(
                user_id=hassan.id,
                token_hash=session_digest(stale_token),
                password_version=old_version,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=12),
            )
        )
        await db.commit()
    await engine.dispose()
    return stale_token


def test_stale_old_password_login_committing_after_change_is_unusable(
    auth_client, auth_settings, seeded_users
):
    stale_token = asyncio.run(
        insert_stale_session_after_password_change(auth_settings.DATABASE_URL)
    )

    response = auth_client.get(
        "/v1/auth/me", headers=session_headers(stale_token)
    )

    assert response.status_code == 401


class PrefixStrippingProxy:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith(
            "/sukaseafood/api"
        ):
            scope = dict(scope)
            scope["path"] = scope["path"][len("/sukaseafood/api") :]
            scope["raw_path"] = scope["path"].encode("ascii")
        await self.app(scope, receive, send)


def test_cookie_is_replayed_on_external_sukaseafood_path(
    auth_settings, test_password_hash
):
    asyncio.run(create_schema(auth_settings.DATABASE_URL))
    asyncio.run(insert_fixed_users(auth_settings.DATABASE_URL, test_password_hash))
    app = create_app(auth_settings)
    with TestClient(
        PrefixStrippingProxy(app), base_url="https://testserver"
    ) as external_client:
        logged_in = external_client.post(
            "/sukaseafood/api/v1/auth/login",
            json={"name": "Hassan", "password": TEST_PASSWORD},
        )
        me = external_client.get("/sukaseafood/api/v1/auth/me")

    assert logged_in.status_code == 200
    assert me.status_code == 200
    assert me.json()["name"] == "Hassan"


def command_environment(settings) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "DATABASE_URL": settings.DATABASE_URL,
            "SESSION_COOKIE_NAME": settings.SESSION_COOKIE_NAME,
            "SESSION_HOURS": str(settings.SESSION_HOURS),
            "SESSION_SECRET": settings.SESSION_SECRET,
            "CSRF_SECRET": settings.CSRF_SECRET,
            "APP_ENV": settings.APP_ENV,
        }
    )
    return environment


def run_command(settings, *arguments: str) -> subprocess.CompletedProcess[str]:
    api_root = Path(__file__).resolve().parents[1]
    return subprocess.run(
        [sys.executable, "-m", *arguments],
        cwd=api_root,
        env=command_environment(settings),
        capture_output=True,
        text=True,
        check=False,
    )


def test_seed_command_prints_random_passwords_once_and_is_idempotent(settings):
    from app.services.auth import verify_password

    asyncio.run(create_schema(settings.DATABASE_URL))

    first = run_command(settings, "app.commands.seed_users", "--print-once")
    users_after_first, _ = asyncio.run(load_auth_rows(settings.DATABASE_URL))
    hashes_after_first = {user.name: user.password_hash for user in users_after_first}
    lines = [line for line in first.stdout.splitlines() if line.strip()]
    generated = dict(line.split(": ", 1) for line in lines)
    second = run_command(settings, "app.commands.seed_users", "--print-once")
    users_after_second, _ = asyncio.run(load_auth_rows(settings.DATABASE_URL))

    assert first.returncode == 0
    assert list(generated) == FIXED_NAMES
    assert len(set(generated.values())) == 6
    assert all(len(password) >= 20 for password in generated.values())
    assert all(
        verify_password(generated[user.name], user.password_hash)
        for user in users_after_first
    )
    assert all(user.must_change_password for user in users_after_first)
    assert hashes_after_first == {
        user.name: user.password_hash for user in users_after_second
    }
    assert second.returncode == 0
    assert second.stdout == ""


async def add_live_mao_session(database_url: str) -> None:
    from app.services.auth import session_digest

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        mao = await session.scalar(select(User).where(User.name == "Mao"))
        assert mao is not None
        session.add(
            Session(
                user_id=mao.id,
                token_hash=session_digest("test-only-browser-token"),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=12),
            )
        )
        await session.commit()
    await engine.dispose()


def test_reset_password_command_is_random_and_revokes_sessions(settings):
    from app.services.auth import verify_password

    asyncio.run(create_schema(settings.DATABASE_URL))
    assert run_command(settings, "app.commands.seed_users").returncode == 0
    asyncio.run(add_live_mao_session(settings.DATABASE_URL))

    first_reset = run_command(settings, "app.commands.reset_password", "Mao")
    first_reset_lines = [
        line for line in first_reset.stdout.splitlines() if line.strip()
    ]
    assert first_reset.returncode == 0
    assert len(first_reset_lines) == 1
    _, first_temporary_password = first_reset_lines[0].split(": ", 1)
    reset = run_command(settings, "app.commands.reset_password", "Mao")
    users, sessions = asyncio.run(load_auth_rows(settings.DATABASE_URL))
    mao = next(user for user in users if user.name == "Mao")
    reset_lines = [line for line in reset.stdout.splitlines() if line.strip()]
    _, temporary_password = reset_lines[0].split(": ", 1)
    rejected = run_command(settings, "app.commands.reset_password", "NotAUser")

    assert reset.returncode == 0
    assert len(reset_lines) == 1
    assert reset_lines[0].startswith("Mao: ")
    assert len(temporary_password) >= 20
    assert first_temporary_password != temporary_password
    assert verify_password(temporary_password, mao.password_hash)
    assert mao.must_change_password is True
    assert mao.password_version == 3
    assert sessions[0].revoked_at is not None
    assert rejected.returncode != 0
    assert rejected.stdout == ""


@pytest.mark.parametrize(
    ("module_name", "arguments"),
    [
        ("app.commands.seed_users", []),
        ("app.commands.reset_password", ["Mao"]),
    ],
)
def test_command_failures_do_not_echo_exception_secrets(
    module_name, arguments, monkeypatch, capsys
):
    if module_name.endswith("seed_users"):
        from app.commands import seed_users as command

        async def fail(*_):
            raise RuntimeError("test-only-database-password")

        monkeypatch.setattr(command, "seed_users", fail)
    else:
        from app.commands import reset_password as command

        async def fail(*_):
            raise RuntimeError("test-only-database-password")

        monkeypatch.setattr(command, "reset_password", fail)

    result = command.main(arguments)
    captured = capsys.readouterr()

    assert result == 1
    assert captured.out == ""
    assert "test-only-database-password" not in captured.err
    assert "failed" in captured.err.lower() or "unable" in captured.err.lower()
