from __future__ import annotations

import asyncio
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys

from alembic import command
from alembic.config import Config
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.main import create_app
from app.models import (
    AuditEvent,
    Candidate,
    CandidateImportPreview,
    Review,
    Session,
    Species,
)
from app.services.auth import csrf_token, session_digest
from app.services.imports import (
    ImportConflict,
    commit_candidate_csv,
    dry_run_candidate_csv,
    normalize_legacy_row,
    preview_candidate_csv,
    stage_candidate_csv,
)
from tests.admin_support import (
    admin_headers,
    load_all,
    seed_admin_database,
    update_one,
)


API_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures" / "candidates_sample.csv"
REAL_MANIFEST = Path(
    "C:/Users/86166/Desktop/SukaSeafood_CV_Dataset_Collector/output/candidates.csv"
)
REQUIRED_HEADERS = (
    "seafood_code",
    "source_dataset",
    "source_record_id",
    "source_url",
    "image_url",
    "license",
)


def fixture_bytes() -> bytes:
    return FIXTURE.read_bytes()


def fixture_rows() -> list[dict[str, str]]:
    with FIXTURE.open(encoding="utf-8", newline="") as source:
        return list(csv.DictReader(source))


def csv_bytes(rows: list[dict[str, str]], headers=REQUIRED_HEADERS) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode()


def valid_row(**changes: str) -> dict[str, str]:
    row = {
        "seafood_code": "SF001",
        "source_dataset": "INATURALIST",
        "source_record_id": "obs:100/photo:200",
        "source_url": "https://www.inaturalist.org/observations/100",
        "image_url": (
            "https://inaturalist-open-data.s3.amazonaws.com/"
            "photos/200/large.jpg"
        ),
        "license": "CC-BY-4.0",
    }
    row.update(changes)
    return row


async def import_factory(settings):
    engine = create_async_engine(settings.DATABASE_URL)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def seed_import_database(settings, *, five_species: bool = False):
    seed = asyncio.run(
        seed_admin_database(settings, candidate_count=0)
    )
    if five_species:
        async def add_species():
            engine, factory = await import_factory(settings)
            async with factory() as db:
                db.add_all(
                    [
                        Species(
                            code=f"SF00{number}",
                            name_zh=f"鱼种 {number}",
                            name_en=f"Species {number}",
                            scientific_name=f"Piscis {number}",
                            sort_order=number,
                        )
                        for number in range(3, 6)
                    ]
                )
                await db.commit()
            await engine.dispose()

        asyncio.run(add_species())
    return seed


async def run_with_session(settings, operation):
    engine, factory = await import_factory(settings)
    try:
        async with factory() as db:
            return await operation(db)
    finally:
        await engine.dispose()


async def count_rows(settings, model) -> int:
    async def operation(db):
        return int(await db.scalar(select(func.count()).select_from(model)) or 0)

    return await run_with_session(settings, operation)


async def add_session(settings, seed, name: str, suffix: str) -> tuple[str, str]:
    raw_token = f"{name.lower()}-{suffix}-test-token"
    digest = session_digest(raw_token)

    async def operation(db):
        session = Session(
            user_id=seed.user_ids[name],
            token_hash=digest,
            password_version=1,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=12),
        )
        db.add(session)
        await db.commit()
        return str(session.id)

    await run_with_session(settings, operation)
    return raw_token, csrf_token(digest, settings.CSRF_SECRET)


def cli_env(settings) -> dict[str, str]:
    return {
        **os.environ,
        "DATABASE_URL": settings.DATABASE_URL,
        "SESSION_COOKIE_NAME": settings.SESSION_COOKIE_NAME,
        "SESSION_HOURS": str(settings.SESSION_HOURS),
        "SESSION_SECRET": settings.SESSION_SECRET,
        "CSRF_SECRET": settings.CSRF_SECRET,
        "APP_ENV": settings.APP_ENV,
    }


def run_cli(settings, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "app.commands.import_candidates", *arguments],
        cwd=API_ROOT,
        env=cli_env(settings),
        capture_output=True,
        text=True,
        check=False,
    )


def candidate_from_normalized(species_id, normalized) -> Candidate:
    return Candidate(
        species_id=species_id,
        source_dataset=normalized.source_dataset,
        source_record_id=normalized.source_record_id,
        preview_url=normalized.preview_url,
        original_url=normalized.original_url,
        source_url=normalized.source_url,
        creator=normalized.creator,
        license=normalized.license,
        license_url=normalized.license_url,
        attribution=normalized.attribution,
        location=normalized.location,
        observed_on=normalized.observed_on,
        metadata_json=normalized.metadata_json,
    )


def test_normalize_maps_all_four_sources_without_local_review_state():
    inat, gbif, commons, fish_vista = [normalize_legacy_row(row) for row in fixture_rows()]

    assert inat.preview_url.endswith("/large.jpg")
    assert inat.original_url.endswith("/original.jpg")
    assert gbif.preview_url.endswith("/large.jpg")
    assert gbif.original_url.endswith("/original.jpg")
    assert gbif.license_url == "https://creativecommons.org/licenses/by/4.0/"
    assert commons.preview_url == (
        "https://upload.wikimedia.org/wikipedia/commons/a/ab/Fish.jpg"
    )
    assert commons.original_url == commons.preview_url
    assert fish_vista.preview_url == "https://fishair.org/media/xc4.jpeg"
    assert fish_vista.original_url == fish_vista.preview_url
    assert fish_vista.source_url == "https://fishair.org/media/xc4.jpeg"
    assert fish_vista.license_url is None
    assert inat.observed_on.isoformat() == "2026-08-13"
    assert gbif.observed_on.isoformat() == "2026-01-02"
    for normalized in (inat, gbif, commons, fish_vista):
        assert normalized.active is True
        assert normalized.version == 1
        assert normalized.current_reviewer_id is None
        assert normalized.current_started_at is None
        assert {"local_path", "sha256", "perceptual_hash", "status", "split"}.isdisjoint(
            normalized.metadata_json
        )


@pytest.mark.parametrize(
    ("record_id", "source_url", "image_url"),
    [
        (
            "occ:1851316046/media:http://pictures.snsb.info/SAPM-PI-01251.jpg",
            "http://biocase.snsb.info/wrapper/querytool/details.cgi?id=1",
            "http://pictures.snsb.info/SAPM-PI-01251.jpg",
        ),
        (
            "occ:1851316227/media:http://pictures.snsb.info/SAPM-PI-01253.jpg",
            "http://biocase.snsb.info/wrapper/querytool/details.cgi?id=2",
            "http://pictures.snsb.info/SAPM-PI-01253.jpg",
        ),
    ],
)
def test_two_disclosed_legacy_http_rows_upgrade_to_https_without_losing_raw_urls(
    record_id, source_url, image_url
):
    normalized = normalize_legacy_row(
        valid_row(
            seafood_code="SF002",
            source_dataset="GBIF",
            source_record_id=record_id,
            source_url=source_url,
            image_url=image_url,
        )
    )

    assert normalized.source_url == source_url.replace("http://", "https://", 1)
    assert normalized.preview_url == image_url.replace("http://", "https://", 1)
    assert normalized.original_url == normalized.preview_url
    assert normalized.metadata_json["raw_urls"] == {
        "source_url": source_url,
        "image_url": image_url,
    }


def test_preview_accepts_utf8_bom_and_reports_stable_source_species_counts():
    preview = preview_candidate_csv(b"\xef\xbb\xbf" + fixture_bytes())

    assert preview.total == 4
    assert preview.source_counts == {
        "FISH_VISTA": 1,
        "GBIF": 1,
        "INATURALIST": 1,
        "WIKIMEDIA_COMMONS": 1,
    }


@pytest.mark.parametrize(
    "unsafe_code", ["../outside", "SF/001", "SF\\001", "CON", "sf001", "鱼001"]
)
def test_preview_rejects_non_ascii_non_windows_safe_species_codes(unsafe_code):
    preview = preview_candidate_csv(
        csv_bytes([valid_row(seafood_code=unsafe_code)])
    )

    assert preview.invalid_species == 1
    assert preview.blocking_errors == 1
    assert preview.new_rows == 0
    assert preview.issues[0].code == "INVALID_SPECIES"
    assert preview.species_counts == {unsafe_code: 1}
    assert preview.file_sha256 == hashlib.sha256(
        csv_bytes([valid_row(seafood_code=unsafe_code)])
    ).hexdigest()
    assert preview.preview_token is None


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (b"seafood_code,source_dataset\nSF001,GBIF\n", "CSV_MISSING_HEADERS"),
        (
            b"seafood_code,source_dataset,source_record_id,source_url,image_url,license,license\n",
            "CSV_DUPLICATE_HEADERS",
        ),
        (
            b"seafood_code,source_dataset,source_record_id,source_url,image_url,lice\x00nse\n",
            "CSV_INVALID_HEADER",
        ),
        (b"\xff\xfeseafood_code", "CSV_INVALID_ENCODING"),
        (
            b'seafood_code,source_dataset,source_record_id,source_url,image_url,license\nSF001,GBIF,"unterminated',
            "CSV_MALFORMED",
        ),
    ],
)
def test_preview_rejects_malformed_file_boundaries_with_stable_codes(content, code):
    preview = preview_candidate_csv(content)

    assert preview.can_commit is False
    assert preview.blocking_errors >= 1
    assert preview.issues[0].code == code


def test_preview_rejects_oversized_file_too_many_rows_and_overlong_field():
    oversized = preview_candidate_csv(b"x" * (5 * 1024 * 1024 + 1))
    rows = [valid_row(source_record_id=f"r{number}") for number in range(50_001)]
    too_many = preview_candidate_csv(csv_bytes(rows))
    overlong = preview_candidate_csv(
        csv_bytes([valid_row(source_record_id="x" * 256)])
    )

    assert oversized.issues[0].code == "CSV_TOO_LARGE"
    assert too_many.issues[0].code == "CSV_TOO_MANY_ROWS"
    assert overlong.issues[0].code == "FIELD_TOO_LONG"
    assert not oversized.can_commit and not too_many.can_commit and not overlong.can_commit


@pytest.mark.parametrize(
    ("changes", "field", "code"),
    [
        ({"image_url": ""}, "missing_urls", "MISSING_URL"),
        ({"image_url": "ftp://example.test/fish.jpg"}, "missing_urls", "UNSAFE_URL"),
        ({"image_url": "https://user:pass@example.test/fish.jpg"}, "missing_urls", "UNSAFE_URL"),
        ({"image_url": "https://127.0.0.1/fish.jpg"}, "missing_urls", "UNSAFE_URL"),
        ({"image_url": "https://10.0.0.2/fish.jpg"}, "missing_urls", "UNSAFE_URL"),
        ({"image_url": "https://[::1]/fish.jpg"}, "missing_urls", "UNSAFE_URL"),
        ({"image_url": "https://localhost/fish.jpg"}, "missing_urls", "UNSAFE_URL"),
        ({"image_url": "https://private.invalid/fish.jpg"}, "missing_urls", "UNSAFE_URL"),
        ({"license": "ARR"}, "invalid_licenses", "INVALID_LICENSE"),
        ({"source_dataset": "OTHER"}, "invalid_sources", "UNSUPPORTED_SOURCE"),
    ],
)
def test_preview_blocks_unsafe_urls_invalid_licenses_and_sources(changes, field, code):
    preview = preview_candidate_csv(csv_bytes([valid_row(**changes)]))

    assert getattr(preview, field) == 1
    assert preview.blocking_errors == 1
    assert preview.can_commit is False
    assert preview.issues[0].code == code


def test_issue_details_are_bounded_while_counts_remain_complete():
    preview = preview_candidate_csv(
        csv_bytes(
            [valid_row(source_record_id=f"bad-{number}", license="ARR") for number in range(150)]
        )
    )

    assert preview.invalid_licenses == 150
    assert preview.blocking_errors == 150
    assert len(preview.issues) == 100
    assert preview.issues_truncated is True
    assert preview.omitted_issue_details == 50


def test_final_normalized_values_fit_columns_and_reject_controls():
    raw_http_url = "http://example.test/" + "a" * (2048 - len("http://example.test/"))
    overlong_after_https = preview_candidate_csv(
        csv_bytes([valid_row(source_dataset="GBIF", image_url=raw_http_url)])
    )
    nul_identifier = preview_candidate_csv(
        csv_bytes([valid_row(source_record_id="unsafe\x00record")])
    )
    metadata_nul = preview_candidate_csv(
        csv_bytes(
            [valid_row(provenance="unsafe\x00metadata")],
            headers=(*REQUIRED_HEADERS, "provenance"),
        )
    )
    url_whitespace = preview_candidate_csv(
        csv_bytes([valid_row(image_url=" https://example.test/fish.jpg")])
    )

    assert overlong_after_https.issues[0].code == "FIELD_TOO_LONG"
    assert nul_identifier.issues[0].code == "INVALID_CONTROL_CHARACTER"
    assert metadata_nul.issues[0].code == "INVALID_CONTROL_CHARACTER"
    assert url_whitespace.issues[0].code == "UNSAFE_URL"
    assert not overlong_after_https.can_commit
    assert not nul_identifier.can_commit
    assert not metadata_nul.can_commit
    assert not url_whitespace.can_commit


def test_human_text_controls_are_normalized_and_metadata_keys_are_casefolded():
    normalized = normalize_legacy_row(
        valid_row(
            creator=" Ada\r\nLovelace\t ",
            attribution=" Ada\r\nLovelace\t / CC-BY ",
            source_location=" sea\r\n bay\t ",
            **{
                " LOCAL_PATH ": "do-not-import",
                "Status": "approved",
                "SHA256": "secret-local-state",
                " Review_Notes ": "local review state",
                "public_note": "kept",
            },
        )
    )

    assert normalized.creator == "Ada Lovelace"
    assert normalized.attribution == "Ada Lovelace / CC-BY"
    assert normalized.location == "sea bay"
    assert normalized.metadata_json["public_note"] == "kept"
    assert not {
        "local_path",
        "status",
        "sha256",
        "review_notes",
    }.intersection(key.strip().casefold() for key in normalized.metadata_json)


def test_metadata_limit_is_checked_after_all_derived_fields_are_added():
    row = valid_row(
        provenance="x" * 65_470,
        source_date="y" * 64,
    )
    preview = preview_candidate_csv(
        csv_bytes([row], headers=(*REQUIRED_HEADERS, "provenance", "source_date"))
    )

    assert preview.can_commit is False
    assert preview.issues[0].code == "METADATA_TOO_LARGE"


def test_db_preview_reports_unknown_species_and_stages_nothing_on_dry_run(settings):
    seed_import_database(settings)
    content = csv_bytes([valid_row(seafood_code="SF999")])

    async def operation(db):
        report = await dry_run_candidate_csv(db, content)
        staged = await db.scalar(select(func.count()).select_from(CandidateImportPreview))
        return report, staged

    report, staged = asyncio.run(run_with_session(settings, operation))

    assert report.invalid_species == 1
    assert report.new_rows == 0
    assert report.can_commit is False
    assert staged == 0


def test_db_preview_classifies_internal_exact_conflict_and_url_duplicates(settings):
    seed_import_database(settings)
    original = valid_row()
    exact = dict(original)
    conflicting = valid_row(source_url="https://example.test/changed")
    url_duplicate = valid_row(
        source_record_id="obs:101/photo:201",
        image_url=original["image_url"],
        source_url="https://www.inaturalist.org/observations/101",
    )

    async def classify(content):
        async def operation(db):
            return await dry_run_candidate_csv(db, content)

        return await run_with_session(settings, operation)

    exact_report = asyncio.run(classify(csv_bytes([original, exact])))
    conflict_report = asyncio.run(classify(csv_bytes([original, conflicting])))
    url_report = asyncio.run(classify(csv_bytes([original, url_duplicate])))

    assert (exact_report.total, exact_report.new_rows, exact_report.exact_duplicates) == (2, 1, 1)
    assert exact_report.can_commit is True
    assert conflict_report.conflicting_identities == 1
    assert conflict_report.can_commit is False
    assert url_report.new_rows == 2
    assert url_report.possible_url_duplicates == 1
    assert url_report.can_commit is True


def test_db_preview_classifies_existing_exact_and_url_duplicates(settings):
    seed = seed_import_database(settings)
    first = normalize_legacy_row(valid_row())

    async def seed_existing(db):
        db.add(candidate_from_normalized(seed.species_ids[0], first))
        await db.commit()

    asyncio.run(run_with_session(settings, seed_existing))

    exact_content = csv_bytes([valid_row()])
    url_content = csv_bytes(
        [
            valid_row(
                source_record_id="obs:999/photo:200",
                source_url="https://www.inaturalist.org/observations/999",
            )
        ]
    )

    async def reports(db):
        return (
            await dry_run_candidate_csv(db, exact_content),
            await dry_run_candidate_csv(db, url_content),
        )

    exact, possible = asyncio.run(run_with_session(settings, reports))

    assert (exact.new_rows, exact.exact_duplicates, exact.can_commit) == (0, 1, True)
    assert (possible.new_rows, possible.possible_url_duplicates, possible.can_commit) == (1, 1, True)


def test_staged_preview_binds_digest_actor_expiry_and_mutates_no_candidates_or_audit(settings):
    seed = seed_import_database(settings)

    async def operation(db):
        report = await stage_candidate_csv(
            db,
            fixture_bytes(),
            actor_id=seed.user_ids["Mao"],
            actor_session_id=seed.session_ids["Mao"],
            filename="../candidate secret.csv",
        )
        stage = (await db.scalars(select(CandidateImportPreview))).one()
        return report, stage

    report, stage = asyncio.run(run_with_session(settings, operation))

    assert report.preview_token
    assert len(report.preview_token) >= 43
    assert stage.token_digest == hashlib.sha256(report.preview_token.encode()).hexdigest()
    assert report.preview_token not in json.dumps(stage.report_json)
    assert stage.actor_id == seed.user_ids["Mao"]
    assert stage.actor_session_id == seed.session_ids["Mao"]
    assert stage.filename == "candidate_secret.csv"
    assert stage.content == fixture_bytes()
    assert stage.expires_at > datetime.now(timezone.utc)
    assert asyncio.run(count_rows(settings, Candidate)) == 0
    assert asyncio.run(count_rows(settings, AuditEvent)) == 0


@pytest.mark.parametrize("case", ["tampered", "other_actor", "expired"])
def test_commit_rejects_tampered_actor_bound_or_expired_token(settings, case):
    seed = seed_import_database(settings)

    async def stage(db):
        return await stage_candidate_csv(
            db,
            fixture_bytes(),
            actor_id=seed.user_ids["Mao"],
            actor_session_id=seed.session_ids["Mao"],
            filename="candidates.csv",
        )

    report = asyncio.run(run_with_session(settings, stage))
    token = report.preview_token
    actor_id = seed.user_ids["Mao"]
    actor_session_id = seed.session_ids["Mao"]
    expected = "IMPORT_PREVIEW_NOT_FOUND"
    if case == "tampered":
        token += "x"
    elif case == "other_actor":
        actor_id = seed.user_ids["Hassan"]
        actor_session_id = seed.session_ids["Hassan"]
    else:
        stages = asyncio.run(load_all(settings, CandidateImportPreview))
        asyncio.run(
            update_one(
                settings,
                CandidateImportPreview,
                stages[0].id,
                expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            )
        )
        expected = "IMPORT_PREVIEW_EXPIRED"

    async def commit(db):
        with pytest.raises(ImportConflict) as caught:
            await commit_candidate_csv(
                db,
                token,
                actor_id,
                actor_session_id=actor_session_id,
            )
        return caught.value.code

    assert asyncio.run(run_with_session(settings, commit)) == expected
    assert asyncio.run(count_rows(settings, Candidate)) == 0
    assert asyncio.run(count_rows(settings, AuditEvent)) == 0


def test_commit_inserts_all_candidates_with_null_assignments_one_bounded_audit(settings):
    seed = seed_import_database(settings)

    async def operation(db):
        preview = await stage_candidate_csv(
            db,
            fixture_bytes(),
            actor_id=seed.user_ids["Mao"],
            actor_session_id=seed.session_ids["Mao"],
            filename="candidates.csv",
        )
        result = await commit_candidate_csv(
            db,
            preview.preview_token,
            seed.user_ids["Mao"],
            actor_session_id=seed.session_ids["Mao"],
        )
        candidates = list((await db.scalars(select(Candidate).order_by(Candidate.source_dataset))).all())
        reviews = list((await db.scalars(select(Review))).all())
        audits = list((await db.scalars(select(AuditEvent))).all())
        stage = (await db.scalars(select(CandidateImportPreview))).one()
        return result, candidates, reviews, audits, stage

    result, candidates, reviews, audits, stage = asyncio.run(run_with_session(settings, operation))

    assert result.model_dump(mode="json") == {
        "total": 4,
        "inserted": 4,
        "skipped_exact": 0,
        "possible_url_duplicates": 0,
        "file_sha256": hashlib.sha256(fixture_bytes()).hexdigest(),
    }
    assert len(candidates) == 4
    assert reviews == []
    assert all(candidate.active and candidate.version == 1 for candidate in candidates)
    assert all(candidate.current_reviewer_id is None and candidate.current_started_at is None for candidate in candidates)
    assert len(audits) == 1
    assert audits[0].action == "CSV_IMPORT"
    audit_text = json.dumps(audits[0].after_json)
    assert "preview_token" not in audit_text and "content" not in audit_text
    assert stage.content is None
    assert stage.committed_at is not None
    assert stage.result_json == result.model_dump(mode="json")


def test_commit_skips_only_exact_duplicates_disclosed_by_preview(settings):
    seed = seed_import_database(settings)
    row = valid_row()
    content = csv_bytes([row, dict(row)])

    async def operation(db):
        preview = await stage_candidate_csv(
            db,
            content,
            actor_id=seed.user_ids["Mao"],
            actor_session_id=seed.session_ids["Mao"],
            filename="duplicates.csv",
        )
        result = await commit_candidate_csv(
            db,
            preview.preview_token,
            seed.user_ids["Mao"],
            actor_session_id=seed.session_ids["Mao"],
        )
        return preview, result

    preview, result = asyncio.run(run_with_session(settings, operation))

    assert preview.exact_duplicates == 1
    assert result.inserted == 1
    assert result.skipped_exact == 1
    assert asyncio.run(count_rows(settings, Candidate)) == 1


def test_commit_refuses_blocking_preview_and_stale_file_or_database_state(settings):
    seed = seed_import_database(settings)

    async def stage_three(db):
        blocked = await stage_candidate_csv(
            db,
            csv_bytes([valid_row(license="ARR")]),
            actor_id=seed.user_ids["Mao"],
            actor_session_id=seed.session_ids["Mao"],
            filename="blocked.csv",
        )
        stale_file = await stage_candidate_csv(
            db,
            csv_bytes([valid_row(source_record_id="file-stale")]),
            actor_id=seed.user_ids["Mao"],
            actor_session_id=seed.session_ids["Mao"],
            filename="file.csv",
        )
        stale_db = await stage_candidate_csv(
            db,
            csv_bytes([valid_row(source_record_id="db-stale")]),
            actor_id=seed.user_ids["Mao"],
            actor_session_id=seed.session_ids["Mao"],
            filename="db.csv",
        )
        return blocked, stale_file, stale_db

    blocked, stale_file, stale_db = asyncio.run(run_with_session(settings, stage_three))
    stages = asyncio.run(load_all(settings, CandidateImportPreview))
    by_name = {stage.filename: stage for stage in stages}
    asyncio.run(update_one(settings, CandidateImportPreview, by_name["file.csv"].id, content=b"changed"))

    normalized = normalize_legacy_row(valid_row(source_record_id="db-stale"))

    async def mutate_db(db):
        db.add(candidate_from_normalized(seed.species_ids[0], normalized))
        await db.commit()

    asyncio.run(run_with_session(settings, mutate_db))

    async def attempt(token, expected):
        async def operation(db):
            with pytest.raises(ImportConflict) as caught:
                await commit_candidate_csv(
                    db,
                    token,
                    seed.user_ids["Mao"],
                    actor_session_id=seed.session_ids["Mao"],
                )
            assert caught.value.code == expected

        await run_with_session(settings, operation)

    asyncio.run(attempt(blocked.preview_token, "IMPORT_PREVIEW_BLOCKED"))
    asyncio.run(attempt(stale_file.preview_token, "IMPORT_PREVIEW_STALE"))
    asyncio.run(attempt(stale_db.preview_token, "IMPORT_PREVIEW_STALE"))
    assert asyncio.run(count_rows(settings, Candidate)) == 1
    assert asyncio.run(count_rows(settings, AuditEvent)) == 0


def test_successful_commit_retry_returns_exact_stored_result_without_new_writes(settings):
    seed = seed_import_database(settings)
    content = csv_bytes([valid_row()])

    async def operation(db):
        preview = await stage_candidate_csv(
            db,
            content,
            actor_id=seed.user_ids["Mao"],
            actor_session_id=seed.session_ids["Mao"],
            filename="one.csv",
        )
        first = await commit_candidate_csv(
            db,
            preview.preview_token,
            seed.user_ids["Mao"],
            actor_session_id=seed.session_ids["Mao"],
        )
        second = await commit_candidate_csv(
            db,
            preview.preview_token,
            seed.user_ids["Mao"],
            actor_session_id=seed.session_ids["Mao"],
        )
        return first, second

    first, second = asyncio.run(run_with_session(settings, operation))

    assert second.model_dump(mode="json") == first.model_dump(mode="json")
    assert asyncio.run(count_rows(settings, Candidate)) == 1
    assert asyncio.run(count_rows(settings, AuditEvent)) == 1


def test_concurrent_retry_converges_on_one_result(settings):
    seed = seed_import_database(settings)
    content = csv_bytes([valid_row()])

    async def stage(db):
        return await stage_candidate_csv(
            db,
            content,
            actor_id=seed.user_ids["Mao"],
            actor_session_id=seed.session_ids["Mao"],
            filename="race.csv",
        )

    preview = asyncio.run(run_with_session(settings, stage))

    async def race():
        engine, factory = await import_factory(settings)

        async def one():
            async with factory() as db:
                return await commit_candidate_csv(
                    db,
                    preview.preview_token,
                    seed.user_ids["Mao"],
                    actor_session_id=seed.session_ids["Mao"],
                )

        try:
            return await asyncio.gather(one(), one())
        finally:
            await engine.dispose()

    first, second = asyncio.run(race())

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert asyncio.run(count_rows(settings, Candidate)) == 1
    assert asyncio.run(count_rows(settings, AuditEvent)) == 1


def test_injected_database_failure_rolls_back_candidates_audit_and_commit_state(settings):
    seed = seed_import_database(settings)
    content = csv_bytes([valid_row()])

    async def stage_and_trigger(db):
        preview = await stage_candidate_csv(
            db,
            content,
            actor_id=seed.user_ids["Mao"],
            actor_session_id=seed.session_ids["Mao"],
            filename="rollback.csv",
        )
        await db.execute(
            text(
                "CREATE TRIGGER fail_candidate_import BEFORE INSERT ON candidates "
                "BEGIN SELECT RAISE(ABORT, 'injected import failure'); END"
            )
        )
        await db.commit()
        return preview

    preview = asyncio.run(run_with_session(settings, stage_and_trigger))

    async def commit(db):
        with pytest.raises(Exception, match="injected import failure"):
            await commit_candidate_csv(
                db,
                preview.preview_token,
                seed.user_ids["Mao"],
                actor_session_id=seed.session_ids["Mao"],
            )

    asyncio.run(run_with_session(settings, commit))

    assert asyncio.run(count_rows(settings, Candidate)) == 0
    assert asyncio.run(count_rows(settings, AuditEvent)) == 0
    stages = asyncio.run(load_all(settings, CandidateImportPreview))
    assert stages[0].committed_at is None
    assert stages[0].content == content


def test_preview_api_requires_initialized_mao_and_csrf_and_never_mutates_candidates(settings):
    seed = seed_import_database(settings)
    cases = []
    with TestClient(create_app(settings)) as client:
        cases.append(client.post("/v1/admin/imports/preview", files={"file": ("c.csv", fixture_bytes(), "text/csv")}))
        cases.append(
            client.post(
                "/v1/admin/imports/preview",
                files={"file": ("c.csv", fixture_bytes(), "text/csv")},
                headers=admin_headers(seed, "Hassan", csrf=True),
            )
        )
        cases.append(
            client.post(
                "/v1/admin/imports/preview",
                files={"file": ("c.csv", fixture_bytes(), "text/csv")},
                headers=admin_headers(seed),
            )
        )
        success = client.post(
            "/v1/admin/imports/preview",
            files={"file": ("c.csv", fixture_bytes(), "text/csv")},
            headers=admin_headers(seed, csrf=True),
        )

    assert [response.status_code for response in cases] == [401, 403, 403]
    assert success.status_code == 200
    assert success.json()["preview_token"]
    assert success.json()["new_rows"] == 4
    assert asyncio.run(count_rows(settings, Candidate)) == 0
    assert asyncio.run(count_rows(settings, AuditEvent)) == 0


@pytest.mark.parametrize(
    ("content", "expected_status", "expected_code"),
    [
        (b"x" * (5 * 1024 * 1024 + 1), 413, "CSV_TOO_LARGE"),
        (b"\xff\xfeseafood_code", 422, "CSV_INVALID_ENCODING"),
        (b"seafood_code,source_dataset\nSF001,GBIF\n", 422, "CSV_MISSING_HEADERS"),
        (
            b"seafood_code,source_dataset,source_record_id,source_url,image_url,license,license\n",
            422,
            "CSV_DUPLICATE_HEADERS",
        ),
        (
            b'seafood_code,source_dataset,source_record_id,source_url,image_url,license\nSF001,GBIF,"unterminated',
            422,
            "CSV_MALFORMED",
        ),
    ],
    ids=["too-large", "encoding", "missing-headers", "duplicate-headers", "malformed"],
)
def test_preview_api_maps_fatal_file_errors_without_staging_or_writes(
    settings, content, expected_status, expected_code
):
    seed = seed_import_database(settings)

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/v1/admin/imports/preview",
            files={"file": ("fatal.csv", content, "text/csv")},
            headers=admin_headers(seed, csrf=True),
        )

    assert response.status_code == expected_status
    assert response.json()["detail"]["code"] == expected_code
    report = response.json()["detail"]["report"]
    assert report["can_commit"] is False
    assert "preview_token" not in report
    assert settings.SESSION_SECRET not in response.text
    assert settings.CSRF_SECRET not in response.text
    assert asyncio.run(count_rows(settings, CandidateImportPreview)) == 0
    assert asyncio.run(count_rows(settings, Candidate)) == 0
    assert asyncio.run(count_rows(settings, AuditEvent)) == 0


def test_preview_api_maps_row_limit_to_413_without_staging(settings):
    seed = seed_import_database(settings)
    content = csv_bytes(
        [
            valid_row(
                source_dataset="FISH_VISTA",
                source_record_id=str(number),
                source_url="https://x.co/a",
                image_url="https://x.co/a",
                license="CC0",
            )
            for number in range(50_001)
        ]
    )
    assert len(content) < 5 * 1024 * 1024

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/v1/admin/imports/preview",
            files={"file": ("too-many.csv", content, "text/csv")},
            headers=admin_headers(seed, csrf=True),
        )

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "CSV_TOO_MANY_ROWS"
    assert asyncio.run(count_rows(settings, CandidateImportPreview)) == 0
    assert asyncio.run(count_rows(settings, Candidate)) == 0
    assert asyncio.run(count_rows(settings, AuditEvent)) == 0


def test_commit_api_requires_csrf_and_returns_stable_conflicts(settings):
    seed = seed_import_database(settings)
    with TestClient(create_app(settings)) as client:
        preview = client.post(
            "/v1/admin/imports/preview",
            files={"file": ("c.csv", fixture_bytes(), "text/csv")},
            headers=admin_headers(seed, csrf=True),
        ).json()
        no_csrf = client.post(
            "/v1/admin/imports/commit",
            json={"preview_token": preview["preview_token"]},
            headers=admin_headers(seed),
        )
        tampered = client.post(
            "/v1/admin/imports/commit",
            json={"preview_token": preview["preview_token"] + "x"},
            headers=admin_headers(seed, csrf=True),
        )
        success = client.post(
            "/v1/admin/imports/commit",
            json={"preview_token": preview["preview_token"]},
            headers=admin_headers(seed, csrf=True),
        )
        retry = client.post(
            "/v1/admin/imports/commit",
            json={"preview_token": preview["preview_token"]},
            headers=admin_headers(seed, csrf=True),
        )

    assert no_csrf.status_code == 403
    assert tampered.status_code == 409
    assert tampered.json()["detail"] == {"code": "IMPORT_PREVIEW_NOT_FOUND"}
    assert success.status_code == 200
    assert retry.json() == success.json()


def test_preview_token_is_bound_to_exact_mao_session_across_commit_and_retry(settings):
    seed = seed_import_database(settings)
    session_b_token, session_b_csrf = asyncio.run(
        add_session(settings, seed, "Mao", "second-session")
    )
    session_b_headers = {
        "Cookie": f"review_session={session_b_token}",
        "X-CSRF-Token": session_b_csrf,
    }

    with TestClient(create_app(settings)) as client:
        preview = client.post(
            "/v1/admin/imports/preview",
            files={"file": ("c.csv", csv_bytes([valid_row()]), "text/csv")},
            headers=admin_headers(seed, csrf=True),
        ).json()
        rejected_before = client.post(
            "/v1/admin/imports/commit",
            json={"preview_token": preview["preview_token"]},
            headers=session_b_headers,
        )
        committed = client.post(
            "/v1/admin/imports/commit",
            json={"preview_token": preview["preview_token"]},
            headers=admin_headers(seed, csrf=True),
        )
        rejected_retry = client.post(
            "/v1/admin/imports/commit",
            json={"preview_token": preview["preview_token"]},
            headers=session_b_headers,
        )
        original_retry = client.post(
            "/v1/admin/imports/commit",
            json={"preview_token": preview["preview_token"]},
            headers=admin_headers(seed, csrf=True),
        )

    assert rejected_before.status_code == rejected_retry.status_code == 409
    assert rejected_before.json()["detail"] == rejected_retry.json()["detail"] == {
        "code": "IMPORT_PREVIEW_NOT_FOUND"
    }
    assert committed.status_code == 200
    assert original_retry.json() == committed.json()
    assert asyncio.run(count_rows(settings, Candidate)) == 1
    assert asyncio.run(count_rows(settings, AuditEvent)) == 1


def test_import_migration_upgrade_downgrade_reupgrade_and_postgres_offline_ddl(tmp_path):
    database_path = tmp_path / "imports-migration.sqlite3"
    config = Config(API_ROOT / "alembic.ini")
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")

    command.upgrade(config, "head")
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path.as_posix()}")

    async def migration_shape():
        async with engine.connect() as connection:
            return await connection.run_sync(
                lambda sync: {
                    "tables": set(inspect(sync).get_table_names()),
                    "columns": {
                        item["name"]: item
                        for item in inspect(sync).get_columns("candidate_import_previews")
                    }
                    if "candidate_import_previews" in inspect(sync).get_table_names()
                    else {},
                    "foreign_keys": inspect(sync).get_foreign_keys(
                        "candidate_import_previews"
                    )
                    if "candidate_import_previews" in inspect(sync).get_table_names()
                    else [],
                }
            )

    shape = asyncio.run(migration_shape())
    assert "candidate_import_previews" in shape["tables"]
    assert shape["columns"]["actor_session_id"]["nullable"] is False
    assert any(
        foreign_key["constrained_columns"] == ["actor_session_id"]
        and foreign_key["referred_table"] == "sessions"
        for foreign_key in shape["foreign_keys"]
    )
    command.downgrade(config, "20260826_03")
    assert "candidate_import_previews" not in asyncio.run(migration_shape())["tables"]
    command.upgrade(config, "head")
    assert "candidate_import_previews" in asyncio.run(migration_shape())["tables"]
    asyncio.run(engine.dispose())

    output = io.StringIO()
    pg_config = Config(API_ROOT / "alembic.ini", output_buffer=output)
    pg_config.set_main_option("script_location", str(API_ROOT / "alembic"))
    pg_config.set_main_option("sqlalchemy.url", "postgresql://user:password@localhost/review")
    command.upgrade(pg_config, "20260826_03:20260826_04", sql=True)
    ddl = output.getvalue()
    assert "CREATE TABLE candidate_import_previews" in ddl
    assert "BYTEA" in ddl
    assert "actor_session_id UUID NOT NULL" in ddl
    assert "FOREIGN KEY(actor_session_id) REFERENCES sessions" in ddl
    assert "CREATE UNIQUE INDEX" in ddl


def test_cli_dry_run_writes_json_and_no_database_rows(settings, tmp_path):
    seed_import_database(settings)
    report_path = tmp_path / "report.json"

    completed = run_cli(
        settings,
        str(FIXTURE),
        "--dry-run",
        "--json-report",
        str(report_path),
    )

    assert completed.returncode == 0, completed.stderr
    stdout = json.loads(completed.stdout)
    written = json.loads(report_path.read_text(encoding="utf-8"))
    assert stdout == written
    assert stdout["total"] == 4
    assert "preview_token" not in stdout
    assert asyncio.run(count_rows(settings, Candidate)) == 0
    assert asyncio.run(count_rows(settings, CandidateImportPreview)) == 0
    assert asyncio.run(count_rows(settings, AuditEvent)) == 0


def test_cli_commit_revalidates_in_one_transaction_and_is_idempotent(
    settings, tmp_path
):
    seed_import_database(settings, five_species=True)
    report_path = tmp_path / "commit-report.json"

    first = run_cli(
        settings,
        str(FIXTURE),
        "--commit",
        "--json-report",
        str(report_path),
    )
    second = run_cli(settings, str(FIXTURE), "--commit")

    assert first.returncode == second.returncode == 0
    assert json.loads(first.stdout)["inserted"] == 4
    assert json.loads(report_path.read_text("utf-8"))["inserted"] == 4
    assert json.loads(second.stdout)["inserted"] == 0
    assert json.loads(second.stdout)["skipped_exact"] == 4
    candidates = asyncio.run(load_all(settings, Candidate))
    assert len(candidates) == 4
    assert all(item.current_reviewer_id is None for item in candidates)
    audits = asyncio.run(load_all(settings, AuditEvent))
    assert [event.action for event in audits].count("CSV_IMPORT_CLI") == 1


def test_cli_requires_exactly_one_of_dry_run_or_commit(settings):
    seed_import_database(settings)

    neither = run_cli(settings, str(FIXTURE))
    both = run_cli(settings, str(FIXTURE), "--dry-run", "--commit")

    assert neither.returncode != 0
    assert both.returncode != 0
    assert asyncio.run(count_rows(settings, Candidate)) == 0


@pytest.mark.parametrize("kind", ["missing", "malformed", "oversized"])
def test_cli_failures_are_nonzero_useful_and_secret_free(settings, tmp_path, kind):
    seed_import_database(settings)
    path = tmp_path / f"{kind}.csv"
    if kind == "malformed":
        path.write_bytes(b"seafood_code,source_dataset\nSF001,GBIF\n")
    elif kind == "oversized":
        path.write_bytes(b"x" * (5 * 1024 * 1024 + 1))

    completed = run_cli(settings, str(path), "--dry-run")

    assert completed.returncode != 0
    assert kind in completed.stderr.lower() or "csv" in completed.stderr.lower()
    assert settings.SESSION_SECRET not in completed.stderr
    assert settings.CSRF_SECRET not in completed.stderr


@pytest.mark.skipif(not REAL_MANIFEST.exists(), reason="collector manifest is not present")
def test_real_1221_manifest_dry_run_has_exact_counts_and_no_writes(settings, tmp_path):
    seed_import_database(settings, five_species=True)
    report_path = tmp_path / "real-report.json"

    before = asyncio.run(count_rows(settings, Candidate))
    completed = run_cli(
        settings,
        str(REAL_MANIFEST),
        "--dry-run",
        "--json-report",
        str(report_path),
    )
    after = asyncio.run(count_rows(settings, Candidate))

    assert completed.returncode == 0, completed.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["total"] == 1221
    assert report["source_counts"] == {
        "FISH_VISTA": 51,
        "GBIF": 496,
        "INATURALIST": 500,
        "WIKIMEDIA_COMMONS": 174,
    }
    assert report["species_counts"] == {
        "SF001": 257,
        "SF002": 211,
        "SF003": 251,
        "SF004": 257,
        "SF005": 245,
    }
    assert "preview_token" not in report
    assert report["possible_url_duplicates"] == 247
    assert report["warnings"] == 262
    assert len(report["issues"]) == 100
    assert report["issues_truncated"] is True
    assert report["omitted_issue_details"] == 162
    assert before == after == 0
    assert asyncio.run(count_rows(settings, CandidateImportPreview)) == 0
    assert asyncio.run(count_rows(settings, AuditEvent)) == 0
