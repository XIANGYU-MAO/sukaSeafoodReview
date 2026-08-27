from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[2]
ZIP = ROOT / "web" / "public" / "downloads" / "sukaseafood-collector.zip"
PACKAGE_ROOT = "sukaseafood-collector"
SOURCES = (
    "collect_fish_images.py",
    "species_config.example.json",
    "requirements.txt",
    "README_ZH.md",
    "README.md",
)


def test_published_collector_zip_contains_only_current_allowlisted_sources():
    assert ZIP.is_file()

    with ZipFile(ZIP) as archive:
        assert set(archive.namelist()) == {f"{PACKAGE_ROOT}/{name}" for name in SOURCES}
        for name in SOURCES:
            assert archive.read(f"{PACKAGE_ROOT}/{name}") == (
                ROOT / "collector" / name
            ).read_bytes()
