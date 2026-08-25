from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.models import Candidate, Session, Species, User
from app.services.auth import csrf_token, session_digest


@dataclass(frozen=True)
class ReviewSeed:
    hassan_id: UUID
    mao_id: UUID
    species_id: UUID
    other_species_id: UUID
    candidate_ids: tuple[UUID, ...]
    hassan_token: str
    hassan_csrf: str
    mao_token: str
    mao_csrf: str


def candidate_record(
    species_id: UUID,
    number: int,
    *,
    source_dataset: str = "iNaturalist",
    active: bool = True,
) -> Candidate:
    return Candidate(
        species_id=species_id,
        source_dataset=source_dataset,
        source_record_id=f"record-{number:03d}",
        preview_url=f"https://images.example.test/{number}/preview.jpg",
        original_url=f"https://images.example.test/{number}/original.jpg",
        source_url=f"https://source.example.test/{number}",
        creator="Test Creator",
        license="CC-BY-4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution=f"Test Creator / record-{number:03d}",
        metadata_json={"catalog_number": number},
        active=active,
    )


async def seed_review_database(
    database_url: str,
    settings: Settings,
    *,
    candidate_count: int = 3,
    must_change_password: bool = False,
) -> ReviewSeed:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        hassan = User(
            name="Hassan",
            role="reviewer",
            password_hash="test-only-password-hash",
            must_change_password=must_change_password,
        )
        mao = User(
            name="Mao",
            role="admin",
            password_hash="test-only-password-hash",
            must_change_password=False,
        )
        species = Species(
            code="SF001",
            name_zh="测试鱼",
            name_en="Test fish",
            scientific_name="Piscis probatio",
        )
        other_species = Species(
            code="SF002",
            name_zh="其他鱼",
            name_en="Other fish",
            scientific_name="Piscis alter",
        )
        db.add_all([hassan, mao, species, other_species])
        await db.flush()
        candidates = [
            candidate_record(species.id, number)
            for number in range(1, candidate_count + 1)
        ]
        db.add_all(candidates)

        hassan_token = "hassan-test-browser-token"
        mao_token = "mao-test-browser-token"
        now = datetime.now(timezone.utc)
        db.add_all(
            [
                Session(
                    user_id=hassan.id,
                    token_hash=session_digest(hassan_token),
                    password_version=hassan.password_version,
                    expires_at=now + timedelta(hours=12),
                ),
                Session(
                    user_id=mao.id,
                    token_hash=session_digest(mao_token),
                    password_version=mao.password_version,
                    expires_at=now + timedelta(hours=12),
                ),
            ]
        )
        await db.commit()
        result = ReviewSeed(
            hassan_id=hassan.id,
            mao_id=mao.id,
            species_id=species.id,
            other_species_id=other_species.id,
            candidate_ids=tuple(candidate.id for candidate in candidates),
            hassan_token=hassan_token,
            hassan_csrf=csrf_token(
                session_digest(hassan_token), settings.CSRF_SECRET
            ),
            mao_token=mao_token,
            mao_csrf=csrf_token(session_digest(mao_token), settings.CSRF_SECRET),
        )
    await engine.dispose()
    return result


def review_headers(token: str, csrf: str | None = None) -> dict[str, str]:
    headers = {"Cookie": f"review_session={token}"}
    if csrf is not None:
        headers["X-CSRF-Token"] = csrf
    return headers


async def open_session(database_url: str) -> tuple[object, AsyncSession]:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, factory()
