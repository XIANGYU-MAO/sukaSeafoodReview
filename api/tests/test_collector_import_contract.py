from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Species
from app.services.imports import commit_candidate_csv, stage_candidate_csv
from tests.admin_support import seed_admin_database


FIXTURE = Path(__file__).parent / "fixtures" / "collector_dynamic_candidates.csv"


def test_dynamic_collector_csv_previews_and_commits_for_admin_created_species(
    settings,
):
    seed = asyncio.run(seed_admin_database(settings, candidate_count=0))
    content = FIXTURE.read_bytes()

    async def exercise():
        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as db:
                old_species = list((await db.scalars(select(Species))).all())
                for item in old_species:
                    await db.delete(item)
                db.add_all(
                    [
                        Species(
                            code="FISH_A",
                            name_zh="鱼甲",
                            name_en="Fish A",
                            scientific_name="Piscis alpha",
                        ),
                        Species(
                            code="FISH_B",
                            name_zh="鱼乙",
                            name_en="Fish B",
                            scientific_name="Piscis beta",
                        ),
                    ]
                )
                await db.commit()

                preview = await stage_candidate_csv(
                    db,
                    content,
                    actor_id=seed.user_ids["Mao"],
                    actor_session_id=seed.session_ids["Mao"],
                    filename=FIXTURE.name,
                )
                assert preview.preview_token is not None
                result = await commit_candidate_csv(
                    db,
                    preview.preview_token,
                    seed.user_ids["Mao"],
                    actor_session_id=seed.session_ids["Mao"],
                )
                return preview, result
        finally:
            await engine.dispose()

    preview, result = asyncio.run(exercise())

    assert preview.total == 2
    assert preview.new_rows == 2
    assert preview.blocking_errors == 0
    assert result.inserted == 2
