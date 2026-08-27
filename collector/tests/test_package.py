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


def test_published_collector_zip_does_not_force_a_regional_package_mirror():
    with ZipFile(ZIP) as archive:
        packaged_text = "\n".join(
            archive.read(f"{PACKAGE_ROOT}/{name}").decode("utf-8")
            for name in ("collect_fish_images.py", "README_ZH.md", "README.md")
        )

    assert "pypi.tuna.tsinghua.edu.cn" not in packaged_text
    assert "pip install -r requirements.txt" in packaged_text
