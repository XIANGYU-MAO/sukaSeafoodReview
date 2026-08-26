from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import AuditEvent, Candidate, CandidateImportPreview, Species


API_ROOT = Path(__file__).resolve().parents[1]
REAL_MANIFEST = Path(
    "C:/Users/86166/Desktop/SukaSeafood_CV_Dataset_Collector/output/candidates.csv"
)
EXPECTED_SPECIES = (
    ("SF001", "Kembung / Pelaling", "Rastrelliger kanagurta", 1),
    ("SF002", "Bawal Hitam", "Parastromateus niger", 2),
    ("SF003", "Ikan Merah", "Lutjanus sebae", 3),
    ("SF004", "Tilapia", "Oreochromis niloticus", 4),
    ("SF005", "Kerapu Bintik", "Epinephelus coioides", 5),
)


def cli_env(settings) -> dict[str, str]:
    return {
        **os.environ,
        "DATABASE_URL": settings.DATABASE_URL,
        "SESSION_COOKIE_NAME": settings.SESSION_COOKIE_NAME,
        "SESSION_HOURS": str(settings.SESSION_HOURS),
        "SESSION_SECRET": settings.SESSION_SECRET,
        "CSRF_SECRET": settings.CSRF_SECRET,
        "RECEIPT_SECRET": settings.RECEIPT_SECRET,
        "APP_ENV": settings.APP_ENV,
    }


def run(settings, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *arguments],
        cwd=API_ROOT,
        env=cli_env(settings),
        capture_output=True,
        text=True,
        check=False,
    )


async def database_snapshot(settings):
    engine = create_async_engine(settings.DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            species = list((await db.scalars(select(Species).order_by(Species.code))).all())
            counts = {
                model.__tablename__: int(
                    await db.scalar(select(func.count()).select_from(model)) or 0
                )
                for model in (Candidate, CandidateImportPreview, AuditEvent)
            }
            return species, counts
    finally:
        await engine.dispose()


async def customize_catalog(settings) -> None:
    engine = create_async_engine(settings.DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            first = await db.scalar(select(Species).where(Species.code == "SF001"))
            third = await db.scalar(select(Species).where(Species.code == "SF003"))
            assert first is not None and third is not None
            first.name_zh = "Mao 自定义名称"
            first.name_en = "Mao custom name"
            first.active = False
            first.sort_order = 99
            await db.delete(third)
            db.add(
                Species(
                    code="SF006",
                    name_zh="未来鱼种",
                    name_en="Future species",
                    scientific_name="Piscis futurus",
                    active=True,
                    sort_order=6,
                )
            )
            await db.commit()
    finally:
        await engine.dispose()


def migrate_fresh_database(settings) -> None:
    migrated = run(settings, "-m", "alembic", "upgrade", "head")
    assert migrated.returncode == 0, migrated.stderr


@pytest.mark.skipif(not REAL_MANIFEST.exists(), reason="collector manifest is not present")
def test_fresh_migration_seeds_catalog_and_users_before_real_read_only_dry_run(
    settings, tmp_path
):
    migrate_fresh_database(settings)

    species_seed = run(settings, "-m", "app.commands.seed_species")
    assert species_seed.returncode == 0, species_seed.stderr
    assert species_seed.stdout.splitlines() == [
        f"{code}: {label} ({scientific_name})"
        for code, label, scientific_name, _ in EXPECTED_SPECIES
    ]

    user_seed = run(settings, "-m", "app.commands.seed_users", "--print-once")
    assert user_seed.returncode == 0, user_seed.stderr
    assert len(user_seed.stdout.splitlines()) == 6

    report_path = tmp_path / "fresh-real-report.json"
    dry_run = run(
        settings,
        "-m",
        "app.commands.import_candidates",
        str(REAL_MANIFEST),
        "--dry-run",
        "--json-report",
        str(report_path),
    )
    assert dry_run.returncode == 0, dry_run.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["total"] == report["new_rows"] == 1221
    assert report["possible_url_duplicates"] == 247
    assert report["warnings"] == 262
    assert report["blocking_errors"] == 0
    assert report["can_commit"] is True
    assert "preview_token" not in report

    species, counts = asyncio.run(database_snapshot(settings))
    assert [
        (
            row.code,
            row.name_zh,
            row.name_en,
            row.scientific_name,
            row.active,
            row.sort_order,
        )
        for row in species
    ] == [
        (code, label, label, scientific_name, True, sort_order)
        for code, label, scientific_name, sort_order in EXPECTED_SPECIES
    ]
    assert counts == {
        "candidates": 0,
        "candidate_import_previews": 0,
        "audit_events": 0,
    }

    second_seed = run(settings, "-m", "app.commands.seed_species")
    assert second_seed.returncode == 0, second_seed.stderr
    assert second_seed.stdout == ""
    assert len(asyncio.run(database_snapshot(settings))[0]) == 5


def test_seed_only_fills_missing_defaults_and_preserves_edits_and_future_species(settings):
    migrate_fresh_database(settings)
    first_seed = run(settings, "-m", "app.commands.seed_species")
    assert first_seed.returncode == 0, first_seed.stderr
    asyncio.run(customize_catalog(settings))

    refill = run(settings, "-m", "app.commands.seed_species")
    assert refill.returncode == 0, refill.stderr
    assert refill.stdout.splitlines() == ["SF003: Ikan Merah (Lutjanus sebae)"]

    species, _ = asyncio.run(database_snapshot(settings))
    by_code = {row.code: row for row in species}
    assert set(by_code) == {"SF001", "SF002", "SF003", "SF004", "SF005", "SF006"}
    assert (
        by_code["SF001"].name_zh,
        by_code["SF001"].name_en,
        by_code["SF001"].active,
        by_code["SF001"].sort_order,
    ) == ("Mao 自定义名称", "Mao custom name", False, 99)
    assert by_code["SF006"].scientific_name == "Piscis futurus"
    assert by_code["SF003"].name_zh == by_code["SF003"].name_en == "Ikan Merah"
