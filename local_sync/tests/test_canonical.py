from __future__ import annotations

import codecs
import csv
import io
import os
from pathlib import Path
from pathlib import PurePosixPath
import subprocess
import sys
from uuid import UUID

import pytest

from conftest import BATCH_ID, RECEIPT_TOKEN
from sukaseafood_sync.canonical import (
    CanonicalManifestError,
    write_canonical_manifest,
)
from sukaseafood_sync.engine import ReceiptItem
from sukaseafood_sync.manifest import ExportManifest, ManifestRow


COLUMNS = (
    "candidate_id",
    "review_id",
    "review_version",
    "species_code",
    "relative_path",
    "sha256",
    "source_url",
    "creator",
    "license",
    "license_url",
    "attribution",
)
CANDIDATE_ID = "22222222-2222-4222-8222-222222222222"
REVIEW_ID = "33333333-3333-4333-8333-333333333333"


def _valid_row(candidate_id: str = CANDIDATE_ID) -> dict[str, str]:
    return {
        "candidate_id": candidate_id,
        "review_id": REVIEW_ID,
        "review_version": "2",
        "species_code": "FUTURE_42",
        "relative_path": f"images/FUTURE_42/{candidate_id}.jpg",
        "sha256": "a" * 64,
        "source_url": "https://catalog.example.test/record/1",
        "creator": "Researcher",
        "license": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "attribution": "Researcher / Catalog",
    }


def _encoded(columns: tuple[str, ...], rows: list[dict[str, str]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    return codecs.BOM_UTF8 + output.getvalue().encode("utf-8")


@pytest.mark.parametrize(
    ("case", "updates", "extra_column", "duplicate"),
    (
        ("logs path", {"relative_path": f"logs/{CANDIDATE_ID}.jpg"}, None, False),
        (
            "species mismatch",
            {"relative_path": f"images/OTHER/{CANDIDATE_ID}.jpg"},
            None,
            False,
        ),
        (
            "candidate mismatch",
            {
                "relative_path": (
                    "images/FUTURE_42/44444444-4444-4444-8444-444444444444.jpg"
                )
            },
            None,
            False,
        ),
        ("uppercase sha", {"sha256": "A" * 64}, None, False),
        ("short sha", {"sha256": "a" * 63}, None, False),
        (
            "unsupported suffix",
            {"relative_path": f"images/FUTURE_42/{CANDIDATE_ID}.exe"},
            None,
            False,
        ),
        ("unsafe species", {"species_code": "CON"}, None, False),
        ("bad version", {"review_version": "0"}, None, False),
        ("non-ASCII version", {"review_version": "１"}, None, False),
        ("extra status", {}, "status", False),
        ("extra format", {}, "format", False),
        ("extra phash", {}, "perceptual_hash", False),
        ("duplicate candidate", {}, None, True),
    ),
)
def test_corrupt_existing_canonical_row_aborts_without_changing_original_bytes(
    tmp_path: Path,
    case: str,
    updates: dict[str, str],
    extra_column: str | None,
    duplicate: bool,
) -> None:
    """Every malformed stored row must fail closed before canonical replacement."""

    root = tmp_path / case
    root.mkdir()
    row = _valid_row()
    row.update(updates)
    columns = COLUMNS
    if extra_column is not None:
        columns = (*columns, extra_column)
        row[extra_column] = "SUCCEEDED" if extra_column == "status" else "value"
    rows = [row, dict(row)] if duplicate else [row]
    original = _encoded(columns, rows)
    target = root / "canonical_manifest.csv"
    target.write_bytes(original)
    empty = ExportManifest(rows=(), batch_id=BATCH_ID, receipt_token=RECEIPT_TOKEN)

    with pytest.raises(CanonicalManifestError):
        write_canonical_manifest(root, empty, ())

    assert target.read_bytes() == original


@pytest.mark.parametrize("suffix", (".WEBP", ".jpeg"))
def test_existing_canonical_row_preserves_server_suffix_spelling(
    tmp_path: Path, suffix: str
) -> None:
    root = tmp_path / suffix.removeprefix(".")
    root.mkdir()
    row = _valid_row()
    row["relative_path"] = f"images/FUTURE_42/{CANDIDATE_ID}{suffix}"
    original = _encoded(COLUMNS, [row])
    target = root / "canonical_manifest.csv"
    target.write_bytes(original)
    empty = ExportManifest(rows=(), batch_id=BATCH_ID, receipt_token=RECEIPT_TOKEN)

    write_canonical_manifest(root, empty, ())

    assert target.read_bytes() == original


@pytest.mark.parametrize("action", ["ADD", "REMOVE"])
def test_older_direct_merge_cannot_replace_or_remove_newer_canonical_state(
    tmp_path: Path, action: str
) -> None:
    root = tmp_path / action
    root.mkdir()
    current = _valid_row()
    current["review_version"] = "3"
    target = root / "canonical_manifest.csv"
    original = _encoded(COLUMNS, [current])
    target.write_bytes(original)
    candidate_id = UUID(CANDIDATE_ID)
    review_id = UUID(REVIEW_ID)
    relative = (
        PurePosixPath(f"_removed/{BATCH_ID}/{candidate_id}.jpg")
        if action == "REMOVE"
        else PurePosixPath(f"images/OLDER/{candidate_id}.jpg")
    )
    row = ManifestRow(
        batch_id=BATCH_ID,
        action=action,  # type: ignore[arg-type]
        candidate_id=candidate_id,
        review_id=review_id,
        review_version=1,
        species_code="OLDER",
        target_relative_path=relative,
        previous_relative_path=(
            PurePosixPath(current["relative_path"]) if action == "REMOVE" else None
        ),
        preview_url="https://images.example.test/preview.jpg",
        original_url="https://images.example.test/original.jpg",
        source_url="https://catalog.example.test/record/1",
        creator="Researcher",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Researcher / Catalog",
    )
    receipt = ReceiptItem(
        CANDIDATE_ID,
        REVIEW_ID,
        1,
        "SUCCEEDED",
        "b" * 64,
        relative.as_posix(),
        None,
    )

    write_canonical_manifest(
        root,
        ExportManifest((row,), BATCH_ID, RECEIPT_TOKEN),
        (receipt,),
    )

    assert target.read_bytes() == original


def test_same_path_replacement_canonical_cannot_regress_on_stale_replay(
    tmp_path: Path,
) -> None:
    """Dropping the generation guard would let a pre-epoch replay restore old bytes."""

    root = tmp_path / "same-path-replacement"
    root.mkdir()
    candidate_id = UUID(CANDIDATE_ID)
    relative = PurePosixPath(f"images/FUTURE_42/{candidate_id}.jpg")
    generation_5 = ManifestRow(
        batch_id=BATCH_ID,
        action="ADD",
        candidate_id=candidate_id,
        review_id=UUID("55555555-5555-4555-8555-555555555555"),
        review_version=5,
        species_code="FUTURE_42",
        target_relative_path=relative,
        previous_relative_path=None,
        preview_url="https://images.example.test/preview-5.jpg",
        original_url="https://images.example.test/original-5.jpg",
        source_url="https://catalog.example.test/record/1",
        creator="Researcher",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Researcher / Catalog",
    )
    generation_9 = ManifestRow(
        batch_id=BATCH_ID,
        action="ADD",
        candidate_id=candidate_id,
        review_id=UUID("99999999-9999-4999-8999-999999999999"),
        review_version=9,
        species_code="FUTURE_42",
        target_relative_path=relative,
        previous_relative_path=relative,
        preview_url="https://images.example.test/preview-9.jpg",
        original_url="https://images.example.test/original-9.jpg",
        source_url="https://catalog.example.test/record/1",
        creator="Researcher",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Researcher / Catalog",
    )
    old_receipt = ReceiptItem(
        str(candidate_id), str(generation_5.review_id), 5, "SUCCEEDED", "5" * 64,
        relative.as_posix(), None,
    )
    new_receipt = ReceiptItem(
        str(candidate_id), str(generation_9.review_id), 9, "SUCCEEDED", "9" * 64,
        relative.as_posix(), None,
    )

    write_canonical_manifest(
        root, ExportManifest((generation_5,), BATCH_ID, RECEIPT_TOKEN), (old_receipt,)
    )
    write_canonical_manifest(
        root, ExportManifest((generation_9,), BATCH_ID, RECEIPT_TOKEN), (new_receipt,)
    )
    generation_9_bytes = (root / "canonical_manifest.csv").read_bytes()
    write_canonical_manifest(
        root, ExportManifest((generation_5,), BATCH_ID, RECEIPT_TOKEN), (old_receipt,)
    )

    assert (root / "canonical_manifest.csv").read_bytes() == generation_9_bytes
    with (root / "canonical_manifest.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as stream:
        row = next(csv.DictReader(stream))
    assert row["review_version"] == "9"
    assert row["sha256"] == "9" * 64


def test_two_real_process_writers_preserve_both_successful_rows(tmp_path: Path) -> None:
    """The complete read/merge/replace sequence must be serialized across processes."""

    root = tmp_path / "training"
    root.mkdir()
    seed_rows = [_valid_row(str(UUID(int=number))) for number in range(1, 4001)]
    (root / "canonical_manifest.csv").write_bytes(_encoded(COLUMNS, seed_rows))
    gate = tmp_path / "start"
    worker = r"""
import sys
import time
from pathlib import Path, PurePosixPath
from uuid import UUID
from sukaseafood_sync.canonical import write_canonical_manifest
from sukaseafood_sync.engine import ReceiptItem
from sukaseafood_sync.manifest import ExportManifest, ManifestRow

root = Path(sys.argv[1])
candidate = UUID(sys.argv[2])
gate = Path(sys.argv[3])
while not gate.exists():
    time.sleep(0.01)
batch = UUID('11111111-1111-4111-8111-111111111111')
review = UUID(int=candidate.int + 10000)
relative = PurePosixPath(f'images/FUTURE_42/{candidate}.jpg')
row = ManifestRow(
    batch_id=batch,
    action='ADD',
    candidate_id=candidate,
    review_id=review,
    review_version=1,
    species_code='FUTURE_42',
    target_relative_path=relative,
    previous_relative_path=None,
    preview_url='https://images.example.test/preview.jpg',
    original_url='https://images.example.test/original.jpg',
    source_url='https://catalog.example.test/record/1',
    creator='Researcher',
    license='CC BY 4.0',
    license_url='https://creativecommons.org/licenses/by/4.0/',
    attribution='Researcher / Catalog',
)
manifest = ExportManifest(rows=(row,), batch_id=batch, receipt_token='A' * 20)
receipt = ReceiptItem(str(candidate), str(review), 1, 'SUCCEEDED', 'b' * 64, relative.as_posix(), None)
write_canonical_manifest(root, manifest, (receipt,))
"""
    candidates = (
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", worker, str(root), candidate, str(gate)],
            cwd=Path(__file__).parents[1],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for candidate in candidates
    ]
    gate.write_text("go", encoding="ascii")
    outputs = [process.communicate(timeout=30) for process in processes]
    assert [(process.returncode, stdout, stderr) for process, (stdout, stderr) in zip(processes, outputs, strict=True)] == [
        (0, "", ""),
        (0, "", ""),
    ]

    with (root / "canonical_manifest.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as stream:
        rows = list(csv.DictReader(stream))
    by_candidate = {row["candidate_id"]: row for row in rows}
    assert len(rows) == 4002
    assert set(candidates) <= set(by_candidate)
