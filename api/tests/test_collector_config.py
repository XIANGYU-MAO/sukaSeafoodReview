import asyncio
import csv
from datetime import datetime
import io

from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.main import create_app
from app.models import AuditEvent, Candidate, ExportBatch, Species
from app.database import get_db
from app.api.routes import collector as collector_routes
from app.services import collector as collector_service
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


ARCHIVE_MANIFEST_COLUMNS = [
    "image_id",
    "seafood_code",
    "app_label",
    "scientific_name",
    "source_dataset",
    "source_record_id",
    "source_taxon_match",
    "source_url",
    "image_url",
    "creator",
    "license",
    "license_url",
    "attribution",
    "source_observation_quality",
    "source_country",
    "source_location",
    "source_date",
    "source_split",
    "image_context",
    "whole_fish",
    "exact_species_verified",
    "verified_by",
    "verification_notes",
    "original_group_id",
    "sha256",
    "perceptual_hash",
    "local_path",
    "split",
    "status",
    "rejection_reason",
]


async def deactivate_candidate_and_count_archive_side_effects(settings, candidate_id):
    engine = create_async_engine(settings.DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        candidate = await db.get(Candidate, candidate_id)
        candidate.active = False
        await db.commit()
        counts = (
            await db.scalar(select(func.count(AuditEvent.id))),
            await db.scalar(select(func.count(ExportBatch.id))),
        )
    await engine.dispose()
    return counts


async def archive_side_effect_counts(settings):
    engine = create_async_engine(settings.DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        counts = (
            await db.scalar(select(func.count(AuditEvent.id))),
            await db.scalar(select(func.count(ExportBatch.id))),
        )
    await engine.dispose()
    return counts


async def candidate_manifest_chunks(settings, chunk_rows):
    engine = create_async_engine(settings.DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        chunks = [
            chunk
            async for chunk in collector_service.stream_candidate_manifest(
                db, chunk_rows=chunk_rows
            )
        ]
    await engine.dispose()
    return chunks


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


def test_admin_downloads_all_candidates_as_read_only_archive_manifest(settings):
    seed = asyncio.run(seed_admin_database(settings, candidate_count=5))
    before = asyncio.run(
        deactivate_candidate_and_count_archive_side_effects(
            settings, seed.candidate_ids[0]
        )
    )

    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/v1/admin/collector/candidates.csv", headers=admin_headers(seed)
        )

    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    after = asyncio.run(archive_side_effect_counts(settings))
    assert response.status_code == 200
    assert response.headers["content-disposition"] == (
        'attachment; filename="sukaseafood-all-candidates.csv"'
    )
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["cache-control"] == "no-store"
    assert rows and list(rows[0]) == ARCHIVE_MANIFEST_COLUMNS
    assert len(rows) == 5
    assert {row["image_id"] for row in rows} == {
        str(candidate_id) for candidate_id in seed.candidate_ids
    }
    assert [row["seafood_code"] for row in rows] == [
        "SF002",
        "SF001",
        "SF001",
        "SF001",
        "SF001",
    ]
    assert all(row["image_url"].endswith("/original.jpg") for row in rows)
    assert all(row["status"] == "CANDIDATE" for row in rows)
    assert all(row["whole_fish"] == "REVIEW" for row in rows)
    assert all(row["exact_species_verified"] == "REVIEW" for row in rows)
    assert before == (0, 0)
    assert after == before


def test_candidate_archive_manifest_requires_admin_access(settings):
    seed = asyncio.run(seed_admin_database(settings, candidate_count=1))

    with TestClient(create_app(settings)) as client:
        anonymous = client.get("/v1/admin/collector/candidates.csv")
        reviewer = client.get(
            "/v1/admin/collector/candidates.csv",
            headers=admin_headers(seed, "Hassan"),
        )

    assert anonymous.status_code == 401
    assert reviewer.status_code == 403


def test_candidate_archive_manifest_streams_bounded_row_chunks(settings):
    asyncio.run(seed_admin_database(settings, candidate_count=5))

    chunks = asyncio.run(candidate_manifest_chunks(settings, chunk_rows=2))
    rows = list(
        csv.DictReader(
            io.StringIO(b"".join(chunks).decode("utf-8-sig"))
        )
    )

    assert len(chunks) == 4
    assert len(rows) == 5
    assert all(len(chunk) < 10_000 for chunk in chunks)


def test_candidate_archive_stream_owns_a_session_beyond_request_dependencies(
    settings, monkeypatch
):
    seed = asyncio.run(seed_admin_database(settings, candidate_count=1))
    dependency_sessions = []
    stream_sessions = []
    app = create_app(settings)

    async def tracked_dependency(request: Request):
        async with request.app.state.session_factory() as session:
            dependency_sessions.append(session)
            yield session

    async def tracked_stream(session):
        stream_sessions.append(session)
        yield b"\xef\xbb\xbfimage_id\r\n"

    app.dependency_overrides[get_db] = tracked_dependency
    monkeypatch.setattr(
        collector_routes, "stream_candidate_manifest", tracked_stream
    )

    with TestClient(app) as client:
        response = client.get(
            "/v1/admin/collector/candidates.csv", headers=admin_headers(seed)
        )

    assert response.status_code == 200
    assert len(dependency_sessions) == 1
    assert len(stream_sessions) == 1
    assert stream_sessions[0] is not dependency_sessions[0]


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
