from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.models import Base, Candidate, Session, Species, User
from app.services.auth import csrf_token, session_digest
from tests.review_support import candidate_record


FIXED_ACCOUNTS = (
    ("Hassan", "reviewer"),
    ("Mao", "admin"),
    ("Xinhui", "reviewer"),
    ("Wahid", "reviewer"),
    ("Sharmaa", "reviewer"),
    ("Yiming", "reviewer"),
)


@dataclass(frozen=True)
class AdminSeed:
    user_ids: dict[str, UUID]
    species_ids: tuple[UUID, UUID]
    candidate_ids: tuple[UUID, ...]
    tokens: dict[str, str]
    csrf: dict[str, str]


async def seed_admin_database(
    settings: Settings,
    *,
    mao_must_change_password: bool = False,
    candidate_count: int = 5,
) -> AdminSeed:
    engine = create_async_engine(settings.DATABASE_URL)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        users = {
            name: User(
                name=name,
                role=role,
                password_hash="test-only-password-hash",
                must_change_password=(
                    mao_must_change_password if name == "Mao" else False
                ),
            )
            for name, role in FIXED_ACCOUNTS
        }
        primary = Species(
            code="SF001",
            name_zh="测试鱼",
            name_en="Test fish",
            scientific_name="Piscis probatio",
            sort_order=20,
        )
        secondary = Species(
            code="SF002",
            name_zh="其他鱼",
            name_en="Other fish",
            scientific_name="Piscis alter",
            sort_order=10,
        )
        db.add_all([*users.values(), primary, secondary])
        await db.flush()
        candidates = [
            candidate_record(
                primary.id if number != 2 else secondary.id,
                number,
                source_dataset=("iNaturalist" if number != 2 else "Wikimedia"),
            )
            for number in range(1, candidate_count + 1)
        ]
        db.add_all(candidates)
        now = datetime.now(timezone.utc)
        tokens: dict[str, str] = {}
        csrf_values: dict[str, str] = {}
        for name, user in users.items():
            raw_token = f"{name.lower()}-admin-test-token"
            digest = session_digest(raw_token)
            tokens[name] = raw_token
            csrf_values[name] = csrf_token(digest, settings.CSRF_SECRET)
            db.add(
                Session(
                    user_id=user.id,
                    token_hash=digest,
                    password_version=user.password_version,
                    expires_at=now + timedelta(hours=12),
                )
            )
        await db.commit()
        result = AdminSeed(
            user_ids={name: user.id for name, user in users.items()},
            species_ids=(primary.id, secondary.id),
            candidate_ids=tuple(candidate.id for candidate in candidates),
            tokens=tokens,
            csrf=csrf_values,
        )
    await engine.dispose()
    return result


def admin_headers(
    seed: AdminSeed, name: str = "Mao", *, csrf: bool = False
) -> dict[str, str]:
    headers = {"Cookie": f"review_session={seed.tokens[name]}"}
    if csrf:
        headers["X-CSRF-Token"] = seed.csrf[name]
    return headers


async def load_one(settings: Settings, model, object_id: UUID):
    engine = create_async_engine(settings.DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        value = await db.get(model, object_id)
    await engine.dispose()
    return value


async def load_all(settings: Settings, model):
    engine = create_async_engine(settings.DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        values = list((await db.scalars(select(model))).all())
    await engine.dispose()
    return values


async def update_one(settings: Settings, model, object_id: UUID, **values) -> None:
    engine = create_async_engine(settings.DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        record = await db.get(model, object_id)
        assert record is not None
        for field, value in values.items():
            setattr(record, field, value)
        await db.commit()
    await engine.dispose()
