from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from app.models import Base, Candidate, Decision, Review, Species, User


@pytest.fixture
def db_session(tmp_path):
    database_path = tmp_path / "model-constraints.sqlite3"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    Base.metadata.create_all(engine)
    with OrmSession(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def reviewer(db_session):
    user = User(
        name="Hassan",
        role="reviewer",
        password_hash="test-password-hash",
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def species(db_session):
    record = Species(
        code="SF001",
        name_zh="测试鱼",
        name_en="Test fish",
        scientific_name="Piscis probatio",
    )
    db_session.add(record)
    db_session.commit()
    return record


def make_candidate(*, species, source_record_id="record-001"):
    return Candidate(
        species_id=species.id,
        source_dataset="test-source",
        source_record_id=source_record_id,
        preview_url="https://example.test/preview.jpg",
        original_url="https://example.test/original.jpg",
        source_url="https://example.test/record",
        license="CC-BY-4.0",
        attribution="Test Creator",
        metadata_json={"location": "test reef"},
    )


def make_review(*, candidate, reviewer, decision, is_current=True):
    return Review(
        candidate_id=candidate.id,
        reviewer_id=reviewer.id,
        decision=decision,
        whole_fish="YES",
        exact_species_verified="YES",
        is_current=is_current,
    )


def test_candidate_has_only_one_current_review(db_session, species, reviewer):
    candidate = make_candidate(species=species)
    db_session.add(candidate)
    db_session.commit()

    db_session.add(
        make_review(
            candidate=candidate,
            reviewer=reviewer,
            decision=Decision.APPROVED,
        )
    )
    db_session.commit()
    db_session.add(
        make_review(
            candidate=candidate,
            reviewer=reviewer,
            decision=Decision.REJECTED,
        )
    )

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_replacing_a_non_current_review_succeeds(db_session, species, reviewer):
    candidate = make_candidate(species=species)
    db_session.add(candidate)
    db_session.commit()
    old_review = make_review(
        candidate=candidate,
        reviewer=reviewer,
        decision=Decision.APPROVED,
    )
    db_session.add(old_review)
    db_session.commit()

    old_review.is_current = False
    db_session.commit()
    new_review = make_review(
        candidate=candidate,
        reviewer=reviewer,
        decision=Decision.UNSURE,
    )
    db_session.add(new_review)
    db_session.commit()

    assert old_review.is_current is False
    assert new_review.is_current is True


def test_candidate_source_identity_is_unique(db_session, species):
    db_session.add(make_candidate(species=species))
    db_session.commit()
    db_session.add(make_candidate(species=species))

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_candidate_current_reviewer_may_be_null(db_session, species):
    candidate = make_candidate(species=species)
    db_session.add(candidate)
    db_session.commit()

    assert candidate.current_reviewer_id is None


def test_initial_migration_upgrades_a_fresh_database(tmp_path):
    api_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "migration.sqlite3"
    config = Config(api_root / "alembic.ini")
    config.set_main_option("script_location", str(api_root / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")

    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    inspector = inspect(engine)
    expected_tables = {
        "alembic_version",
        "users",
        "sessions",
        "species",
        "candidates",
        "reviews",
        "review_revisions",
        "audit_events",
        "idempotency_commands",
        "export_batches",
        "export_items",
    }
    assert expected_tables == set(inspector.get_table_names())

    candidate_uniques = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("candidates")
    }
    assert ("source_dataset", "source_record_id") in candidate_uniques

    review_indexes = {
        index["name"]: index for index in inspector.get_indexes("reviews")
    }
    assert review_indexes["uq_reviews_current_candidate"]["unique"] == 1
    assert "is_current = 1" in str(
        review_indexes["uq_reviews_current_candidate"]["dialect_options"][
            "sqlite_where"
        ]
    )

    candidate_columns = {
        column["name"]: column for column in inspector.get_columns("candidates")
    }
    user_columns = {
        column["name"]: column for column in inspector.get_columns("users")
    }
    session_columns = {
        column["name"]: column for column in inspector.get_columns("sessions")
    }
    assert candidate_columns["current_reviewer_id"]["nullable"] is True
    assert user_columns["password_version"]["nullable"] is False
    assert session_columns["password_version"]["nullable"] is False

    candidate_foreign_keys = {
        tuple(foreign_key["constrained_columns"])
        for foreign_key in inspector.get_foreign_keys("candidates")
    }
    review_foreign_keys = {
        tuple(foreign_key["constrained_columns"])
        for foreign_key in inspector.get_foreign_keys("reviews")
    }
    assert {("species_id",), ("current_reviewer_id",)} <= candidate_foreign_keys
    assert {("candidate_id",), ("reviewer_id",)} <= review_foreign_keys
    engine.dispose()
