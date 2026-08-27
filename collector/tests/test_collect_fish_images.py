import json
from pathlib import Path

import pytest

import collector.collect_fish_images as collector_module
from collector.collect_fish_images import (
    allowed_license,
    clean_html,
    fish_vista_exact_match,
    normalize_license,
    parse_commons_page,
    parse_fish_vista_row,
    parse_gbif_media,
    parse_inat_photo,
    stable_image_id,
)


def test_normalize_license_maps_common_cc_variants():
    assert normalize_license("CC BY-NC-SA 4.0") == "CC-BY-NC-SA"
    assert normalize_license("cc-by 4.0") == "CC-BY"
    assert normalize_license("https://creativecommons.org/publicdomain/zero/1.0/") == "CC0"


def test_allowed_license_rejects_nd_and_all_rights_reserved():
    assert allowed_license("CC-BY") is True
    assert allowed_license("CC-BY-NC-SA") is True
    assert allowed_license("CC-BY-ND") is False
    assert allowed_license("CC-BY-NC-ND") is False
    assert allowed_license(None) is False
    assert allowed_license("") is False


def test_clean_html_removes_markup_and_entities():
    assert clean_html('<a href="x">Jane &amp; John</a><br/>Fish') == "Jane & John Fish"


def test_fish_vista_exact_match_is_case_and_space_tolerant_only():
    assert fish_vista_exact_match(" Lutjanus sebae ", "lutjanus sebae") is True
    assert fish_vista_exact_match("Lutjanus sp.", "Lutjanus sebae") is False


def test_fish_vista_row_uses_original_url_when_repository_path_is_missing():
    original_url = "https://fishair.org/hdr-share/ftp/ark/89609/morphbank/xc11h209.jpeg"
    record = {
        "filename": "mb133404.jpeg",
        "source_filename": "133404.jpeg",
        "arkid": "xc11h209",
        "standardized_species": "rastrelliger kanagurta",
        "original_url": original_url,
        "license": "CC BY-NC-SA 3.0",
        "file_name": "",
        "source": "Morphbank",
    }

    row = parse_fish_vista_row(
        record,
        "train",
        "SF001",
        "Kembung / Pelaling",
        "Rastrelliger kanagurta",
    )

    assert row is not None
    assert row["image_url"] == original_url


def test_fish_vista_row_preserves_complete_repository_path():
    record = {
        "filename": "fish example.jpg",
        "source_filename": "fish example.jpg",
        "arkid": "example-ark",
        "standardized_species": "rastrelliger kanagurta",
        "original_url": "https://example.org/original.jpg",
        "license": "CC BY 4.0",
        "file_name": "Images/chunk_1/fish example.jpg",
    }

    row = parse_fish_vista_row(
        record,
        "train",
        "SF001",
        "Kembung / Pelaling",
        "Rastrelliger kanagurta",
    )

    assert row is not None
    assert row["image_url"] == (
        "https://huggingface.co/datasets/imageomics/fish-vista/resolve/main/"
        "Images/chunk_1/fish%20example.jpg?download=true"
    )


def test_parse_inat_photo_preserves_observation_and_license_metadata():
    obs = {
        "id": 123,
        "uri": "https://www.inaturalist.org/observations/123",
        "quality_grade": "research",
        "observed_on": "2026-01-02",
        "place_guess": "Selangor, Malaysia",
        "taxon": {"id": 133530, "name": "Rastrelliger kanagurta"},
        "photos": [
            {
                "id": 456,
                "url": "https://static.inaturalist.org/photos/456/square.jpg",
                "license_code": "cc-by-nc",
                "attribution": "(c) Example, some rights reserved",
            }
        ],
        "user": {"login": "observer"},
    }
    rows = parse_inat_photo(obs, "SF001", "Kembung / Pelaling", "Rastrelliger kanagurta")
    assert len(rows) == 1
    row = rows[0]
    assert row["source_dataset"] == "INATURALIST"
    assert row["source_record_id"] == "obs:123/photo:456"
    assert row["license"] == "CC-BY-NC"
    assert row["source_observation_quality"] == "research"
    assert row["source_location"] == "Selangor, Malaysia"
    assert row["source_country"] == ""
    assert row["source_taxon_match"] == "EXACT"
    assert row["exact_species_verified"] == "REVIEW"


def test_parse_gbif_media_only_keeps_still_images_with_usable_license():
    occurrence = {
        "key": 9001,
        "scientificName": "Oreochromis niloticus",
        "country": "Malaysia",
        "eventDate": "2024-07-02",
        "references": "https://www.gbif.org/occurrence/9001",
        "media": [
            {
                "type": "StillImage",
                "identifier": "https://example.org/fish.jpg",
                "license": "CC_BY_4_0",
                "creator": "A. Person",
            },
            {
                "type": "StillImage",
                "identifier": "https://example.org/no-license.jpg",
                "license": "",
            },
        ],
    }
    rows = parse_gbif_media(occurrence, "SF004", "Tilapia", "Oreochromis niloticus")
    assert len(rows) == 1
    assert rows[0]["image_url"] == "https://example.org/fish.jpg"
    assert rows[0]["license"] == "CC-BY"
    assert rows[0]["source_country"] == "Malaysia"


def test_parse_commons_page_extracts_license_and_artist():
    page = {
        "pageid": 88,
        "title": "File:Fish.jpg",
        "imageinfo": [
            {
                "url": "https://upload.wikimedia.org/fish.jpg",
                "descriptionurl": "https://commons.wikimedia.org/wiki/File:Fish.jpg",
                "mime": "image/jpeg",
                "extmetadata": {
                    "LicenseShortName": {"value": "CC BY-SA 4.0"},
                    "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0/"},
                    "Artist": {"value": "<b>Jane Doe</b>"},
                    "Credit": {"value": "Own work"},
                },
            }
        ],
    }
    row = parse_commons_page(page, "SF003", "Ikan Merah", "Lutjanus sebae")
    assert row["license"] == "CC-BY-SA"
    assert row["creator"] == "Jane Doe"
    assert row["source_url"].endswith("File:Fish.jpg")


def test_stable_image_id_is_repeatable_and_species_scoped():
    a = stable_image_id("SF001", "INATURALIST", "obs:1/photo:2")
    b = stable_image_id("SF001", "INATURALIST", "obs:1/photo:2")
    c = stable_image_id("SF002", "INATURALIST", "obs:1/photo:2")
    assert a == b
    assert a != c
    assert a.startswith("SF001-")


def dynamic_config():
    return {
        "schema_version": 1,
        "generated_at": "2026-08-27T10:00:00Z",
        "species": [
            {
                "seafood_code": "FISH_A",
                "name_zh": "测试鱼甲",
                "name_en": "Test fish A",
                "scientific_name": "Piscis alpha",
                "inat_taxon_id": None,
                "gbif_taxon_key": None,
                "commons_category": None,
                "fish_vista_filter": None,
            },
            {
                "seafood_code": "FISH_B",
                "name_zh": "测试鱼乙",
                "name_en": "Test fish B",
                "scientific_name": "Piscis beta",
                "inat_taxon_id": 123,
                "gbif_taxon_key": 456,
                "commons_category": "Category:Custom beta",
                "fish_vista_filter": "Custom beta",
            },
        ],
    }


def test_main_selects_all_configured_species_without_fixed_default(monkeypatch, tmp_path):
    config = tmp_path / "species_config.json"
    config.write_text(json.dumps(dynamic_config()), encoding="utf-8")
    seen = []

    class FakeCollector:
        def __init__(self, **_kwargs):
            pass

        def collect_inat(self, species, _max_rows):
            seen.append(species["seafood_code"])
            return []

    monkeypatch.setattr(collector_module, "Collector", FakeCollector)
    result = collector_module.main([
        "--config", str(config),
        "--source", "inat",
        "--max-per-species", "1",
        "--output-dir", str(tmp_path / "output"),
    ])
    assert result == 0
    assert seen == ["FISH_A", "FISH_B"]


def test_main_reports_taxon_resolution_failures_per_species_and_source(monkeypatch, tmp_path, capsys):
    config = tmp_path / "species_config.json"
    config.write_text(json.dumps(dynamic_config()), encoding="utf-8")

    class FakeCollector:
        def __init__(self, **_kwargs):
            pass

        def collect_inat(self, species, _max_rows):
            raise ValueError(
                f"{species['seafood_code']} iNaturalist exact taxon was not resolved; set inat_taxon_id"
            )

    monkeypatch.setattr(collector_module, "Collector", FakeCollector)
    result = collector_module.main([
        "--config", str(config),
        "--source", "inat",
        "--max-per-species", "1",
        "--output-dir", str(tmp_path / "output"),
    ])

    assert result == 0
    assert "!! FISH_A inat failed: FISH_A iNaturalist exact taxon was not resolved; set inat_taxon_id" in capsys.readouterr().err


def test_main_continues_after_malformed_source_response(monkeypatch, tmp_path, capsys):
    config = tmp_path / "species_config.json"
    config.write_text(json.dumps(dynamic_config()), encoding="utf-8")
    seen = []

    class FakeCollector:
        def __init__(self, **_kwargs):
            pass

        def collect_inat(self, species, _max_rows):
            seen.append(species["seafood_code"])
            if species["seafood_code"] == "FISH_A":
                return None.get("results")
            return []

    monkeypatch.setattr(collector_module, "Collector", FakeCollector)
    result = collector_module.main([
        "--config", str(config),
        "--source", "inat",
        "--max-per-species", "1",
        "--output-dir", str(tmp_path / "output"),
    ])

    assert result == 0
    assert seen == ["FISH_A", "FISH_B"]
    assert "!! FISH_A inat failed: 'NoneType' object has no attribute 'get'" in capsys.readouterr().err
