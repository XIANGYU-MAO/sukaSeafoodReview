from __future__ import annotations

import csv
from dataclasses import FrozenInstanceError
import os
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

import pytest

from conftest import (
    BATCH_ID,
    CANDIDATE_ID,
    EXPORT_COLUMNS,
    RECEIPT_TOKEN,
    REVIEW_ID,
    valid_row,
    write_manifest,
)
from sukaseafood_sync.manifest import (
    MAX_MANIFEST_BYTES,
    ManifestError,
    load_manifest,
    resolve_inside,
    validate_relative_path,
)


def write_lexical_manifest(
    directory: Path,
    creator_cell: str,
    *,
    record_newline: str = "\r\n",
) -> Path:
    row = valid_row()
    cells = [row[column] for column in EXPORT_COLUMNS]
    cells[EXPORT_COLUMNS.index("creator")] = creator_cell
    path = directory / "lexical.csv"
    path.write_bytes(
        (
            ",".join(EXPORT_COLUMNS)
            + record_newline
            + ",".join(cells)
            + record_newline
        ).encode("utf-8")
    )
    return path


def test_loads_exact_server_csv_with_bom_rfc_quotes_and_newlines() -> None:
    path = Path(__file__).parent / "fixtures" / "export_batch.csv"

    manifest = load_manifest(path)

    assert manifest.batch_id == BATCH_ID
    assert manifest.receipt_token == RECEIPT_TOKEN
    assert isinstance(manifest.rows, tuple)
    assert len(manifest.rows) == 1
    row = manifest.rows[0]
    assert row.candidate_id == CANDIDATE_ID
    assert row.review_id == REVIEW_ID
    assert row.creator.splitlines() == ['Fish, "Quoted"', "Creator"]
    assert row.attribution.splitlines() == [
        'Fish, "Quoted"',
        "Creator / Example Catalog",
    ]
    assert row.target_relative_path == PurePosixPath(
        f"images/SF006/{CANDIDATE_ID}.jpg"
    )


def test_accepts_utf8_without_bom_and_future_windows_safe_species(tmp_path: Path) -> None:
    row = valid_row(
        species_code="SHELLFISH_A",
        target_relative_path=f"images/SHELLFISH_A/{CANDIDATE_ID}.WEBP",
    )

    manifest = load_manifest(write_manifest(tmp_path, rows=[row], bom=False))

    assert manifest.rows[0].species_code == "SHELLFISH_A"
    assert str(manifest.rows[0].target_relative_path).endswith(".WEBP")


def test_manifest_and_rows_are_immutable_and_secret_free_in_repr(tmp_path: Path) -> None:
    manifest = load_manifest(write_manifest(tmp_path))

    assert RECEIPT_TOKEN not in repr(manifest)
    assert RECEIPT_TOKEN not in repr(manifest.rows[0])
    with pytest.raises(FrozenInstanceError):
        manifest.rows[0].action = "REMOVE"  # type: ignore[misc]
    with pytest.raises(TypeError):
        manifest.rows[0] = manifest.rows[0]  # type: ignore[index]


@pytest.mark.parametrize(
    "headers",
    [
        EXPORT_COLUMNS[:-1],
        (*EXPORT_COLUMNS, "unexpected"),
        ("", *EXPORT_COLUMNS[1:]),
        (EXPORT_COLUMNS[1], EXPORT_COLUMNS[1], *EXPORT_COLUMNS[2:]),
        (EXPORT_COLUMNS[1], EXPORT_COLUMNS[0], *EXPORT_COLUMNS[2:]),
    ],
    ids=["missing", "extra", "blank", "duplicate", "wrong-order"],
)
def test_rejects_any_header_not_matching_exact_server_contract(
    tmp_path: Path, headers: tuple[str, ...]
) -> None:
    path = write_manifest(tmp_path, headers=headers)

    with pytest.raises(ManifestError, match="header"):
        load_manifest(path)


def test_rejects_short_and_extra_rows(tmp_path: Path) -> None:
    for name, cells in (
        ("short.csv", list(valid_row().values())[:-1]),
        ("extra.csv", [*valid_row().values(), "surplus"]),
    ):
        path = tmp_path / name
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(EXPORT_COLUMNS)
            writer.writerow(cells)
        with pytest.raises(ManifestError, match="column"):
            load_manifest(path)


def test_rejects_empty_manifest_and_malformed_csv(tmp_path: Path) -> None:
    empty = write_manifest(tmp_path, rows=[], name="empty.csv")
    malformed = tmp_path / "malformed.csv"
    malformed.write_text(
        ",".join(EXPORT_COLUMNS) + '\n"unterminated', encoding="utf-8"
    )

    with pytest.raises(ManifestError, match="at least one"):
        load_manifest(empty)
    with pytest.raises(ManifestError, match="CSV"):
        load_manifest(malformed)


@pytest.mark.parametrize(
    "creator_cell",
    ['Naked"Quote', '"Closed quote"garbage'],
    ids=["naked-quote", "garbage-after-closing-quote"],
)
def test_rejects_non_rfc_quote_lexemes_without_exposing_token(
    tmp_path: Path, creator_cell: str
) -> None:
    path = write_lexical_manifest(tmp_path, creator_cell)

    with pytest.raises(ManifestError, match="malformed CSV") as caught:
        load_manifest(path)

    assert RECEIPT_TOKEN not in str(caught.value)


@pytest.mark.parametrize("embedded_newline", ["\r\n", "\n"])
def test_accepts_escaped_quotes_commas_and_embedded_record_newlines(
    tmp_path: Path, embedded_newline: str
) -> None:
    creator = f'"Fish, ""Quoted""{embedded_newline}Creator"'
    manifest = load_manifest(
        write_lexical_manifest(tmp_path, creator, record_newline=embedded_newline)
    )

    assert manifest.rows[0].creator == f'Fish, "Quoted"{embedded_newline}Creator'


def test_rejects_invalid_utf8_file_size_and_row_count(tmp_path: Path) -> None:
    invalid_utf8 = tmp_path / "invalid.csv"
    invalid_utf8.write_bytes(b"\xff\xfe")
    oversized = tmp_path / "oversized.csv"
    with oversized.open("wb") as stream:
        stream.truncate(20 * 1024 * 1024 + 1)
    too_many = write_manifest(
        tmp_path,
        rows=[valid_row() for _ in range(10_001)],
        name="too-many.csv",
    )

    with pytest.raises(ManifestError, match="UTF-8"):
        load_manifest(invalid_utf8)
    with pytest.raises(ManifestError, match="20 MiB"):
        load_manifest(oversized)
    with pytest.raises(ManifestError, match="10,000"):
        load_manifest(too_many)


def test_manifest_is_opened_once_in_binary_mode(tmp_path: Path, monkeypatch) -> None:
    path = write_manifest(tmp_path)
    actual_open = Path.open
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def tracked_open(self: Path, *args: object, **kwargs: object):
        calls.append((args, kwargs))
        return actual_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracked_open)

    load_manifest(path)

    assert calls == [(('rb',), {})]


def test_oversize_is_decided_from_bounded_open_handle_not_path_stat(
    tmp_path: Path, monkeypatch
) -> None:
    path = write_manifest(tmp_path)
    with path.open("ab") as stream:
        stream.truncate(MAX_MANIFEST_BYTES + 1)
    actual_stat = Path.stat

    class SizeLie:
        def __init__(self, actual) -> None:
            self._actual = actual
            self.st_size = 0

        def __getattr__(self, name: str):
            return getattr(self._actual, name)

    def misleading_stat(self: Path, *args: object, **kwargs: object):
        actual = actual_stat(self, *args, **kwargs)
        return SizeLie(actual) if self == path else actual

    monkeypatch.setattr(Path, "stat", misleading_stat)

    with pytest.raises(ManifestError, match="20 MiB"):
        load_manifest(path)


@pytest.mark.skipif(os.name == "nt", reason="/dev/null regular-file probe is POSIX-only")
def test_rejects_nonregular_manifest_handle() -> None:
    with pytest.raises(ManifestError, match="regular file"):
        load_manifest(Path("/dev/null"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("batch_id", "not-a-uuid"),
        ("candidate_id", "not-a-uuid"),
        ("review_id", "not-a-uuid"),
        ("review_version", "0"),
        ("review_version", "1.0"),
        ("review_version", str(2**63)),
        ("action", "add"),
        ("action", "UPDATE"),
        ("receipt_token", "not a token"),
        ("species_code", "sf006"),
        ("species_code", "CON"),
        ("species_code", "A" * 33),
        ("license", "   "),
        ("attribution", ""),
        ("creator", "x" * 4097),
    ],
)
def test_rejects_invalid_scalar_fields_without_echoing_secret(
    tmp_path: Path, field: str, value: str
) -> None:
    path = write_manifest(tmp_path, rows=[valid_row(**{field: value})])

    with pytest.raises(ManifestError) as caught:
        load_manifest(path)

    assert field in str(caught.value)
    assert RECEIPT_TOKEN not in str(caught.value)


def test_rejects_nul_and_non_newline_controls_in_text(tmp_path: Path) -> None:
    for field, value in (("creator", "bad\x00name"), ("license", "bad\x1btext")):
        path = write_manifest(
            tmp_path, rows=[valid_row(**{field: value})], name=f"{field}.csv"
        )
        with pytest.raises(ManifestError, match=field):
            load_manifest(path)


@pytest.mark.parametrize("field", ["preview_url", "original_url", "source_url"])
@pytest.mark.parametrize(
    "value",
    [
        "",
        "http://example.test/image.jpg",
        "https://user:pass@example.test/image.jpg",
        "https://example.test/white space.jpg",
        "https://example.test/image.jpg\nnext",
        "//example.test/image.jpg",
    ],
)
def test_rejects_non_absolute_or_unsafe_https_urls(
    tmp_path: Path, field: str, value: str
) -> None:
    path = write_manifest(tmp_path, rows=[valid_row(**{field: value})])

    with pytest.raises(ManifestError, match=field):
        load_manifest(path)


def test_license_url_is_optional_but_otherwise_uses_same_https_rules(
    tmp_path: Path,
) -> None:
    blank = load_manifest(
        write_manifest(tmp_path, rows=[valid_row(license_url="")], name="blank.csv")
    )
    assert blank.rows[0].license_url is None

    with pytest.raises(ManifestError, match="license_url"):
        load_manifest(
            write_manifest(
                tmp_path,
                rows=[valid_row(license_url="https://user@example.test/license")],
                name="credentials.csv",
            )
        )


@pytest.mark.parametrize(
    "target",
    [
        "../outside.jpg",
        "C:/Windows/file.jpg",
        "//server/share/file.jpg",
        "/tmp/file.jpg",
        r"images\SF006\fish.jpg",
        "images//fish.jpg",
        "images/./fish.jpg",
        "images/SF006/../fish.jpg",
        "images/SF006/NUL.jpg",
        "images/SF006/COM1.png",
        "images/SF006/trailing. /fish.jpg",
        "images/SF006/trailing./fish.jpg",
        'images/SF006/bad<name>.jpg',
        "images/SF006/bad:name.jpg",
        "images/SF006/bad\x00name.jpg",
        "images/SF006/bad\x7fname.jpg",
    ],
)
def test_rejects_windows_unsafe_relative_paths(tmp_path: Path, target: str) -> None:
    path = write_manifest(tmp_path, rows=[valid_row(target_relative_path=target)])

    with pytest.raises(ManifestError, match="target_relative_path"):
        load_manifest(path)


def test_shared_path_validator_rejects_delete_control_character() -> None:
    with pytest.raises(ManifestError, match="control"):
        validate_relative_path("images/SF006/bad\x7fname.jpg")


@pytest.mark.parametrize(
    ("action", "target", "previous"),
    [
        (
            "ADD",
            f"images/SF006/{CANDIDATE_ID}.exe",
            "",
        ),
        (
            "ADD",
            f"images/SF007/{CANDIDATE_ID}.jpg",
            "",
        ),
        (
            "ADD",
            f"images/SF006/{uuid4()}.jpg",
            "",
        ),
        (
            "ADD",
            f"images/SF006/{CANDIDATE_ID}.jpg",
            f"images/SF006/{CANDIDATE_ID}.jpg",
        ),
        (
            "MOVE",
            f"images/SF006/{CANDIDATE_ID}.png",
            "",
        ),
        (
            "MOVE",
            f"images/SF006/{CANDIDATE_ID}.png",
            f"images/SF006/{CANDIDATE_ID}.png",
        ),
        (
            "REMOVE",
            f"images/SF006/{CANDIDATE_ID}.png",
            f"images/SF006/{CANDIDATE_ID}.jpg",
        ),
        (
            "REMOVE",
            f"_removed/{uuid4()}/{CANDIDATE_ID}.png",
            f"images/SF006/{CANDIDATE_ID}.jpg",
        ),
        (
            "REMOVE",
            f"_removed/{BATCH_ID}/{uuid4()}.png",
            f"images/SF006/{CANDIDATE_ID}.jpg",
        ),
        (
            "REMOVE",
            f"_removed/{BATCH_ID}/{CANDIDATE_ID}.png",
            "archive/old.jpg",
        ),
    ],
)
def test_rejects_invalid_action_path_shapes(
    tmp_path: Path, action: str, target: str, previous: str
) -> None:
    row = valid_row(
        action=action,
        target_relative_path=target,
        previous_relative_path=previous,
    )

    with pytest.raises(ManifestError, match="relative_path"):
        load_manifest(write_manifest(tmp_path, rows=[row]))


@pytest.mark.parametrize("suffix", ["jpg", "JPEG", "png", "webp", "gif", "tif", "tiff", "bmp", "image"])
def test_accepts_server_supported_suffixes(tmp_path: Path, suffix: str) -> None:
    target = f"images/SF006/{CANDIDATE_ID}.{suffix}"

    manifest = load_manifest(
        write_manifest(tmp_path, rows=[valid_row(target_relative_path=target)])
    )

    assert str(manifest.rows[0].target_relative_path) == target


def test_accepts_move_remove_and_composite_add_exact_shapes(tmp_path: Path) -> None:
    candidate_two = UUID("44444444-4444-4444-8444-444444444444")
    candidate_three = UUID("55555555-5555-4555-8555-555555555555")
    review_two = UUID("66666666-6666-4666-8666-666666666666")
    review_three = UUID("77777777-7777-4777-8777-777777777777")
    rows = [
        valid_row(previous_relative_path=f"images/SF005/{CANDIDATE_ID}.jpg"),
        valid_row(
            action="MOVE",
            candidate_id=candidate_two,
            review_id=review_two,
            target_relative_path=f"images/SHELLFISH_A/{candidate_two}.png",
            previous_relative_path=f"images/SF005/{candidate_two}.png",
            species_code="SHELLFISH_A",
        ),
        valid_row(
            action="REMOVE",
            candidate_id=candidate_three,
            review_id=review_three,
            target_relative_path=f"_removed/{BATCH_ID}/{candidate_three}.image",
            previous_relative_path=f"images/SF005/{candidate_three}.image",
        ),
    ]

    manifest = load_manifest(write_manifest(tmp_path, rows=rows))

    assert [row.action for row in manifest.rows] == ["ADD", "MOVE", "REMOVE"]


def test_rejects_mixed_batches_tokens_duplicate_operations_and_target_collisions(
    tmp_path: Path,
) -> None:
    second_candidate = UUID("44444444-4444-4444-8444-444444444444")
    second_review = UUID("55555555-5555-4555-8555-555555555555")
    base_second = valid_row(
        candidate_id=second_candidate,
        review_id=second_review,
        target_relative_path=f"images/SF006/{second_candidate}.jpg",
    )
    cases = {
        "batch": [valid_row(), {**base_second, "batch_id": str(uuid4())}],
        "token": [valid_row(), {**base_second, "receipt_token": "another_safe-token_1234567890"}],
        "duplicate": [valid_row(), valid_row()],
        "collision": [
            valid_row(),
            {
                **base_second,
                "target_relative_path": f"IMAGES/sf006/{CANDIDATE_ID}.JPG",
            },
        ],
    }
    for name, rows in cases.items():
        path = write_manifest(tmp_path, rows=rows, name=f"{name}.csv")
        with pytest.raises(ManifestError, match=name):
            load_manifest(path)


def test_resolve_inside_preserves_safe_path_and_rejects_symlink_escape(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    relative = PurePosixPath(f"images/SF006/{CANDIDATE_ID}.jpg")
    assert resolve_inside(root, relative) == (
        root / "images" / "SF006" / f"{CANDIDATE_ID}.jpg"
    ).resolve()

    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "images"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    with pytest.raises(ManifestError, match="root"):
        resolve_inside(root, relative)
