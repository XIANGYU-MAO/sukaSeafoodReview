from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from uuid import UUID

import pytest


SRC = Path(__file__).parents[1] / "src"
sys.path.insert(0, str(SRC))

EXPORT_COLUMNS = (
    "batch_id",
    "receipt_token",
    "action",
    "candidate_id",
    "review_id",
    "review_version",
    "species_code",
    "target_relative_path",
    "previous_relative_path",
    "preview_url",
    "original_url",
    "source_url",
    "creator",
    "license",
    "license_url",
    "attribution",
    "image_origin_allowlist",
)

BATCH_ID = UUID("11111111-1111-4111-8111-111111111111")
CANDIDATE_ID = UUID("22222222-2222-4222-8222-222222222222")
REVIEW_ID = UUID("33333333-3333-4333-8333-333333333333")
RECEIPT_TOKEN = "test-only_batch-token_1234567890ABCDE"


def valid_row(**overrides: object) -> dict[str, str]:
    row = {
        "batch_id": str(BATCH_ID),
        "receipt_token": RECEIPT_TOKEN,
        "action": "ADD",
        "candidate_id": str(CANDIDATE_ID),
        "review_id": str(REVIEW_ID),
        "review_version": "1",
        "species_code": "SF006",
        "target_relative_path": f"images/SF006/{CANDIDATE_ID}.jpg",
        "previous_relative_path": "",
        "preview_url": "https://images.example.test/fish/preview.jpg",
        "original_url": "https://images.example.test/fish/original.jpg",
        "source_url": "https://catalog.example.test/records/1",
        "creator": "A. Researcher",
        "license": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "attribution": "A. Researcher / Example Catalog",
        "image_origin_allowlist": json.dumps(["images.example.test"]),
    }
    row.update({key: str(value) for key, value in overrides.items()})
    return row


def write_manifest(
    directory: Path,
    *,
    rows: list[dict[str, str]] | None = None,
    headers: tuple[str, ...] | list[str] = EXPORT_COLUMNS,
    bom: bool = True,
    name: str = "batch.csv",
) -> Path:
    path = directory / name
    with path.open("w", encoding="utf-8-sig" if bom else "utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\r\n")
        writer.writerow(headers)
        for row in rows if rows is not None else [valid_row()]:
            writer.writerow([row.get(header, "") for header in headers])
    return path


@pytest.fixture
def sync_root(tmp_path: Path) -> Path:
    return tmp_path / "training-root"
