import asyncio
from datetime import datetime

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.main import create_app
from app.models import Species
from tests.admin_support import admin_headers, seed_admin_database


async def configure_catalog(settings) -> None:
    engine = create_async_engine(settings.DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        species = list((await db.scalars(select(Species))).all())
        by_code = {item.code: item for item in species}
        by_code["SF002"].inat_taxon_id = 123
        db.add(
            Species(
                code="SF003",
                name_zh="停用鱼",
                name_en="Inactive fish",
                scientific_name="Piscis inactivus",
                active=False,
                sort_order=0,
            )
        )
        await db.commit()
    await engine.dispose()


def test_admin_downloads_active_species_with_current_candidate_counts(settings):
    seed = asyncio.run(seed_admin_database(settings, candidate_count=5))
    asyncio.run(configure_catalog(settings))

    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/v1/admin/collector/config", headers=admin_headers(seed)
        )

    payload = response.json()
    assert response.status_code == 200
    assert response.headers["content-disposition"] == (
        'attachment; filename="species_config.json"'
    )
    assert response.headers["content-type"] == "application/json; charset=utf-8"
    assert response.headers["cache-control"] == "no-store"
    assert set(payload) == {"schema_version", "generated_at", "species"}
    assert payload["schema_version"] == 2
    assert datetime.fromisoformat(payload["generated_at"].replace("Z", "+00:00")).tzinfo
    assert payload["species"] == [
        {
            "seafood_code": "SF002",
            "name_zh": "其他鱼",
            "name_en": "Other fish",
            "scientific_name": "Piscis alter",
            "candidate_count": 1,
            "inat_taxon_id": 123,
            "gbif_taxon_key": None,
            "commons_category": None,
            "fish_vista_filter": None,
        },
        {
            "seafood_code": "SF001",
            "name_zh": "测试鱼",
            "name_en": "Test fish",
            "scientific_name": "Piscis probatio",
            "candidate_count": 4,
            "inat_taxon_id": None,
            "gbif_taxon_key": None,
            "commons_category": None,
            "fish_vista_filter": None,
        },
    ]


def test_collector_config_requires_mao_access(settings):
    seed = asyncio.run(seed_admin_database(settings, candidate_count=0))

    with TestClient(create_app(settings)) as client:
        anonymous = client.get("/v1/admin/collector/config")
        reviewer = client.get(
            "/v1/admin/collector/config", headers=admin_headers(seed, "Hassan")
        )

    assert anonymous.status_code == 401
    assert reviewer.status_code == 403


def test_collector_config_rejects_an_empty_active_catalog(settings):
    seed = asyncio.run(seed_admin_database(settings, candidate_count=0))

    async def deactivate_all() -> None:
        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            species = list((await db.scalars(select(Species))).all())
            for item in species:
                item.active = False
            await db.commit()
        await engine.dispose()

    asyncio.run(deactivate_all())
    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/v1/admin/collector/config", headers=admin_headers(seed)
        )

    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "NO_ACTIVE_SPECIES"}}
