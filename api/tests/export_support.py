from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import io
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.models import Base, Candidate, Decision, Review, Session, Species, User
from app.services.auth import csrf_token, session_digest
from tests.review_support import candidate_record


@dataclass(frozen=True)
class ExportSeed:
    mao_id: UUID
    hassan_id: UUID
    species_ids: tuple[UUID, UUID]
    candidate_ids: tuple[UUID, ...]
    review_ids: tuple[UUID, ...]
    mao_token: str
    mao_csrf: str
    hassan_token: str


async def seed_export_database(
    settings: Settings,
    *,
    decisions: tuple[Decision | None, ...] = (Decision.APPROVED,),
) -> ExportSeed:
    engine = create_async_engine(settings.DATABASE_URL)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        mao = User(
            name="Mao",
            role="admin",
            password_hash="test-only-password-hash",
            must_change_password=False,
        )
        hassan = User(
            name="Hassan",
            role="reviewer",
            password_hash="test-only-password-hash",
            must_change_password=False,
        )
        primary = Species(
            code="SF001",
            name_zh="测试鱼",
            name_en="Test fish",
            scientific_name="Piscis probatio",
            sort_order=1,
        )
        secondary = Species(
            code="SF002",
            name_zh="其他鱼",
            name_en="Other fish",
            scientific_name="Piscis alter",
            sort_order=2,
        )
        db.add_all([mao, hassan, primary, secondary])
        await db.flush()
        candidates = [
            candidate_record(primary.id, number)
            for number in range(1, len(decisions) + 1)
        ]
        db.add_all(candidates)
        await db.flush()
        reviews = []
        for candidate, decision in zip(candidates, decisions, strict=True):
            if decision is None:
                continue
            review = Review(
                candidate_id=candidate.id,
                reviewer_id=hassan.id,
                decision=decision,
                rejection_reason="DUPLICATE" if decision == Decision.REJECTED else None,
                whole_fish="YES" if decision == Decision.APPROVED else "REVIEW",
                exact_species_verified=(
                    "YES" if decision == Decision.APPROVED else "REVIEW"
                ),
                is_current=True,
                version=1,
            )
            db.add(review)
            reviews.append(review)
        mao_token = "mao-export-browser-token"
        hassan_token = "hassan-export-browser-token"
        now = datetime.now(timezone.utc)
        db.add_all(
            [
                Session(
                    user_id=mao.id,
                    token_hash=session_digest(mao_token),
                    password_version=mao.password_version,
                    expires_at=now + timedelta(hours=12),
                ),
                Session(
                    user_id=hassan.id,
                    token_hash=session_digest(hassan_token),
                    password_version=hassan.password_version,
                    expires_at=now + timedelta(hours=12),
                ),
            ]
        )
        await db.commit()
        result = ExportSeed(
            mao_id=mao.id,
            hassan_id=hassan.id,
            species_ids=(primary.id, secondary.id),
            candidate_ids=tuple(candidate.id for candidate in candidates),
            review_ids=tuple(review.id for review in reviews),
            mao_token=mao_token,
            mao_csrf=csrf_token(session_digest(mao_token), settings.CSRF_SECRET),
            hassan_token=hassan_token,
        )
    await engine.dispose()
    return result


def mao_headers(seed: ExportSeed, *, csrf: bool = False) -> dict[str, str]:
    headers = {"Cookie": f"review_session={seed.mao_token}"}
    if csrf:
        headers["X-CSRF-Token"] = seed.mao_csrf
    return headers


def reviewer_headers(seed: ExportSeed) -> dict[str, str]:
    return {"Cookie": f"review_session={seed.hassan_token}"}


def csv_rows(response) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))


def success_receipt(row: dict[str, str], *, sha256: str = "a" * 64) -> dict:
    return {
        "candidate_id": row["candidate_id"],
        "review_id": row["review_id"],
        "review_version": int(row["review_version"]),
        "status": "SUCCEEDED",
        "sha256": sha256,
        "relative_path": row["target_relative_path"],
    }


async def load_models(settings: Settings, model):
    engine = create_async_engine(settings.DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        values = list((await db.scalars(select(model))).all())
    await engine.dispose()
    return values


async def mutate(settings: Settings, model, object_id: UUID, **values) -> None:
    engine = create_async_engine(settings.DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        record = await db.get(model, object_id)
        assert record is not None
        for key, value in values.items():
            setattr(record, key, value)
        await db.commit()
    await engine.dispose()
