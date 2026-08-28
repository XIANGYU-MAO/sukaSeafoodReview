import csv
import json
from pathlib import Path

import pytest
import requests

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
    assert normalize_license("CC-BY-NC 4.0 (Int)") == "CC-BY-NC"
    assert normalize_license("http://creativecommons.org/licenses/by/4.0/legalcode") == "CC-BY"


def test_allowed_license_rejects_nd_and_all_rights_reserved():
    assert allowed_license("CC-BY") is True
    assert allowed_license("CC-BY-NC-SA") is True
    assert allowed_license("CC-BY-ND") is False
    assert allowed_license("CC-BY-NC-ND") is False
    assert allowed_license("CC-BY-NC-custom-restrictions") is False
    assert allowed_license("terms: CC-BY-NC-custom-restrictions") is False
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


def test_parse_ala_occurrence_keeps_exact_licensed_images():
    parser = getattr(collector_module, "parse_ala_occurrence", lambda *_args: [])
    occurrence = {
        "uuid": "ala-record-1",
        "scientificName": "Scomberomorus commerson",
        "references": "https://biocache.ala.org.au/occurrences/ala-record-1",
        "largeImageUrl": "https://images.ala.org.au/image/proxyImageThumbnailLarge?imageId=image-1",
        "imageUrl": "https://images.ala.org.au/image/proxyImage?imageId=image-1",
        "images": ["image-1"],
        "license": "CC-BY-NC 4.0 (Int)",
        "recordedBy": ["Example Observer"],
        "country": "Australia",
        "stateProvince": "Queensland",
        "eventDate": 1760313600000,
        "identificationVerificationStatus": "research",
    }

    rows = parser(
        occurrence,
        "TENGGIRI",
        "康氏马鲛",
        "Scomberomorus commerson",
    )

    assert len(rows) == 1
    assert rows[0]["source_dataset"] == "ATLAS_OF_LIVING_AUSTRALIA"
    assert rows[0]["source_record_id"] == "occ:ala-record-1/image:image-1"
    assert rows[0]["image_url"] == occurrence["imageUrl"]
    assert rows[0]["license"] == "CC-BY-NC"
    assert rows[0]["source_country"] == "Australia"


def test_parse_obis_occurrence_splits_associated_media_and_requires_a_usable_license():
    parser = getattr(collector_module, "parse_obis_occurrence", lambda *_args: [])
    occurrence = {
        "id": "obis-record-1",
        "scientificName": "Scomberomorus commerson",
        "associatedMedia": "https://media.example.org/one.jpg | https://media.example.org/two.jpg",
        "license": "https://creativecommons.org/licenses/by/4.0/",
        "recordedBy": "Marine Observer",
        "country": "Malaysia",
        "locality": "Sabah",
        "eventDate": "2024-04-05T00:00:00Z",
        "basisOfRecord": "HumanObservation",
        "dataset_id": "dataset-1",
    }

    rows = parser(
        occurrence,
        "TENGGIRI",
        "康氏马鲛",
        "Scomberomorus commerson",
    )

    assert [row["image_url"] for row in rows] == [
        "https://media.example.org/one.jpg",
        "https://media.example.org/two.jpg",
    ]
    assert all(row["source_dataset"] == "OBIS" for row in rows)
    assert all(row["license"] == "CC-BY" for row in rows)
    assert rows[1]["source_record_id"] == "occ:obis-record-1/media:2"


def test_parse_smithsonian_record_only_keeps_cc0_media_for_the_exact_species():
    parser = getattr(collector_module, "parse_smithsonian_record", lambda *_args: [])
    record = {
        "id": "edan-record-1",
        "title": "Scomberomorus commerson",
        "content": {
            "indexedStructured": {"scientific_name": ["Scomberomorus commerson"]},
            "descriptiveNonRepeating": {
                "record_link": "https://n2t.net/ark:/65665/example",
                "data_source": "NMNH - Fishes Division",
                "online_media": {
                    "media": [
                        {
                            "type": "Images",
                            "content": "https://ids.si.edu/ids/deliveryService?id=NMNH-example",
                            "thumbnail": "https://ids.si.edu/ids/deliveryService?id=NMNH-example&max=640",
                            "guid": "media-1",
                            "usage": {"access": "CC0"},
                        },
                        {
                            "type": "Images",
                            "content": "https://ids.si.edu/ids/deliveryService?id=restricted",
                            "guid": "media-2",
                            "usage": {"access": "Usage conditions apply"},
                        },
                    ]
                },
            },
        },
    }

    rows = parser(
        record,
        "TENGGIRI",
        "康氏马鲛",
        "Scomberomorus commerson",
    )

    assert len(rows) == 1
    assert rows[0]["source_dataset"] == "SMITHSONIAN_OPEN_ACCESS"
    assert rows[0]["license"] == "CC0"
    assert rows[0]["image_url"].endswith("NMNH-example")
    assert rows[0]["source_record_id"] == "record:edan-record-1/media:media-1"


def test_parse_noaa_search_page_keeps_exact_species_images_credited_to_noaa():
    parser = getattr(collector_module, "parse_noaa_search_page", lambda *_args: [])
    page = """
    <div class='ngdl-photo ngdl-photo--digital-library-list' id="photo-1" data-photo-modal-id="83945">
      <a href="/noaa-collections/search/photo-library/item?search_api_fulltext=Pristigenys%20alta&amp;page=0">
        <div class="c-field c-field--name-field-media-ngdl-photo c-field--type-image">
        <img title="A short bigeye (Pristigenys alta) (Image credit: NOAA/NMFS/SEFSC)"
             src="/sites/default/files/styles/max_650x650/public/fish.jpg" alt="A short bigeye (Pristigenys alta)" />
        </div>
      </a>
      <a class="media-download-link" href="/media/ngdl/download-photo/photo-1">Download</a>
    </div>
    <div class='ngdl-photo ngdl-photo--digital-library-list' id="photo-2">
      <img title="Another fish (Pristigenys alta) (Image credit: Courtesy of Private Photographer)" src="/private.jpg" />
      <a class="media-download-link" href="/media/ngdl/download-photo/photo-2">Download</a>
    </div>
    <div class='ngdl-photo ngdl-photo--digital-library-list' id="photo-3">
      <img title="A different fish (Pristigenys altae) (Image credit: NOAA/NMFS/SEFSC)" src="/different.jpg" />
      <a class="media-download-link" href="/media/ngdl/download-photo/photo-3">Download</a>
    </div>
    """

    rows = parser(page, "BIGEYE", "短大眼鲷", "Pristigenys alta")

    assert len(rows) == 1
    assert rows[0]["source_dataset"] == "NOAA_PHOTO_LIBRARY"
    assert rows[0]["source_record_id"] == "photo:photo-1"
    assert rows[0]["image_url"] == "https://www.noaa.gov/media/ngdl/download-photo/photo-1"
    assert rows[0]["license"] == "PUBLIC-DOMAIN"
    assert rows[0]["creator"] == "NOAA/NMFS/SEFSC"


@pytest.mark.parametrize("source", ["ala", "obis", "smithsonian", "noaa"])
def test_cli_accepts_each_new_source(source):
    parsed = collector_module.parse_args(["--source", source])
    assert parsed.sources == [source]


def test_cli_accepts_repeated_sources_without_collecting_unselected_sources():
    parsed = collector_module.parse_args(
        ["--source", "inat", "--source", "ala", "--source", "noaa"]
    )
    assert parsed.sources == ["inat", "ala", "noaa"]


def test_local_dedupe_reports_only_unique_rows_and_resume_preserves_existing_state():
    existing = {
        "seafood_code": "TENGGIRI",
        "image_url": "https://images.example.test/fish.jpg",
        "source_record_id": "old-record",
        "status": "APPROVED",
        "verified_by": "Mao",
    }
    recollected = {
        "seafood_code": "TENGGIRI",
        "image_url": "https://images.example.test/fish.jpg",
        "source_record_id": "new-record",
        "status": "CANDIDATE",
        "verified_by": "",
    }
    unique = {
        "seafood_code": "TENGGIRI",
        "image_url": "https://images.example.test/other.jpg",
        "source_record_id": "other-record",
        "status": "CANDIDATE",
    }

    assert collector_module.dedupe_metadata([recollected, recollected, unique]) == [
        recollected,
        unique,
    ]
    assert collector_module.merge_resume_rows([existing], [recollected, unique]) == [
        existing,
        unique,
    ]


def test_resume_scans_past_existing_source_rows_to_find_new_candidates(monkeypatch, tmp_path):
    config = tmp_path / "species_config.json"
    config.write_text(json.dumps(dynamic_config()), encoding="utf-8")
    output = tmp_path / "output"
    old = {
        "seafood_code": "FISH_A",
        "source_dataset": "INATURALIST",
        "source_record_id": "old",
        "image_url": "https://images.example.test/old.jpg",
    }
    collector_module.write_manifest([old], output / "candidates.csv")
    limits = []

    class FakeCollector:
        def __init__(self, **_kwargs):
            pass

        def collect_inat(self, species, max_rows):
            limits.append((species["seafood_code"], max_rows))
            if species["seafood_code"] != "FISH_A":
                return []
            return [
                old,
                {
                    "seafood_code": "FISH_A",
                    "source_dataset": "INATURALIST",
                    "source_record_id": "new",
                    "image_url": "https://images.example.test/new.jpg",
                },
            ][:max_rows]

    monkeypatch.setattr(collector_module, "Collector", FakeCollector)
    result = collector_module.main([
        "--config", str(config),
        "--source", "inat",
        "--species", "FISH_A",
        "--max-per-species", "1",
        "--resume",
        "--output-dir", str(output),
    ])

    assert result == 0
    assert limits == [("FISH_A", 2)]
    assert [row["source_record_id"] for row in collector_module.read_manifest(output / "candidates.csv")] == ["old", "new"]


def test_stable_image_id_is_repeatable_and_species_scoped():
    a = stable_image_id("SF001", "INATURALIST", "obs:1/photo:2")
    b = stable_image_id("SF001", "INATURALIST", "obs:1/photo:2")
    c = stable_image_id("SF002", "INATURALIST", "obs:1/photo:2")
    assert a == b
    assert a != c
    assert a.startswith("SF001-")


def dynamic_config():
    return {
        "schema_version": 2,
        "generated_at": "2026-08-27T10:00:00Z",
        "species": [
            {
                "seafood_code": "FISH_A",
                "name_zh": "测试鱼甲",
                "name_en": "Test fish A",
                "scientific_name": "Piscis alpha",
                "candidate_count": 10,
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
                "candidate_count": 3,
                "inat_taxon_id": 123,
                "gbif_taxon_key": 456,
                "commons_category": "Category:Custom beta",
                "fish_vista_filter": "Custom beta",
            },
        ],
    }


def test_main_collects_only_each_species_shortfall_until_minimum(monkeypatch, tmp_path):
    config = tmp_path / "species_config.json"
    config.write_text(json.dumps(dynamic_config()), encoding="utf-8")
    calls = []

    def rows(species, source, amount):
        return [
            {
                "seafood_code": species["seafood_code"],
                "source": source,
                "source_record_id": f"{source}-{index}",
                "image_url": f"https://example.test/{species['seafood_code']}/{source}-{index}.jpg",
            }
            for index in range(amount)
        ]

    class FakeCollector:
        def __init__(self, **_kwargs):
            pass

        def collect_fish_vista(self, species, max_rows):
            calls.append((species["seafood_code"], "fish-vista", max_rows))
            return rows(species, "fish-vista", min(2, max_rows))

        def collect_inat(self, species, max_rows):
            calls.append((species["seafood_code"], "inat", max_rows))
            return rows(species, "inat", max_rows)

        def collect_gbif(self, species, max_rows):
            calls.append((species["seafood_code"], "gbif", max_rows))
            return rows(species, "gbif", max_rows)

        def collect_commons(self, species, max_rows):
            calls.append((species["seafood_code"], "commons", max_rows))
            return rows(species, "commons", max_rows)

    monkeypatch.setattr(collector_module, "Collector", FakeCollector)
    result = collector_module.main([
        "--config", str(config),
        "--source", "all",
        "--max-per-species", "5",
        "--minimum-total-per-species", "10",
        "--output-dir", str(tmp_path / "output"),
    ])

    assert result == 0
    assert calls == [("FISH_B", "fish-vista", 5), ("FISH_B", "inat", 5)]
    with (tmp_path / "output" / "candidates.csv").open(encoding="utf-8-sig", newline="") as handle:
        assert len(list(csv.DictReader(handle))) == 7


def test_main_collects_up_to_total_maximum_once_species_is_below_minimum(monkeypatch, tmp_path):
    config = tmp_path / "species_config.json"
    config.write_text(json.dumps(dynamic_config()), encoding="utf-8")
    calls = []

    def rows(species, source, amount):
        return [
            {
                "seafood_code": species["seafood_code"],
                "source": source,
                "source_record_id": f"{source}-{index}",
                "image_url": f"https://example.test/{species['seafood_code']}/{source}-{index}.jpg",
            }
            for index in range(amount)
        ]

    class FakeCollector:
        def __init__(self, **_kwargs):
            pass

        def collect_fish_vista(self, species, max_rows):
            calls.append((species["seafood_code"], "fish-vista", max_rows))
            return rows(species, "fish-vista", max_rows)

        def collect_inat(self, species, max_rows):
            calls.append((species["seafood_code"], "inat", max_rows))
            return rows(species, "inat", max_rows)

    monkeypatch.setattr(collector_module, "Collector", FakeCollector)
    result = collector_module.main([
        "--config", str(config),
        "--source", "fish-vista",
        "--source", "inat",
        "--max-per-species", "5",
        "--minimum-total-per-species", "10",
        "--maximum-total-per-species", "12",
        "--output-dir", str(tmp_path / "output"),
    ])

    assert result == 0
    assert calls == [("FISH_B", "fish-vista", 5), ("FISH_B", "inat", 4)]
    with (tmp_path / "output" / "candidates.csv").open(encoding="utf-8-sig", newline="") as handle:
        assert len(list(csv.DictReader(handle))) == 9


def test_main_rejects_total_maximum_below_minimum():
    with pytest.raises(SystemExit, match="--maximum-total-per-species must be >= --minimum-total-per-species"):
        collector_module.main([
            "--minimum-total-per-species", "10",
            "--maximum-total-per-species", "9",
        ])


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


def test_main_tells_operator_to_resume_after_source_request_failure(monkeypatch, tmp_path, capsys):
    config = tmp_path / "species_config.json"
    config.write_text(json.dumps(dynamic_config()), encoding="utf-8")

    class FakeCollector:
        def __init__(self, **_kwargs):
            pass

        def collect_inat(self, _species, _max_rows):
            raise requests.RequestException("temporary source outage")

    monkeypatch.setattr(collector_module, "Collector", FakeCollector)
    result = collector_module.main([
        "--config", str(config),
        "--source", "inat",
        "--species", "FISH_A",
        "--max-per-species", "1",
        "--output-dir", str(tmp_path / "output"),
    ])

    assert result == 0
    assert "!! FISH_A inat failed: temporary source outage; retry later with --resume" in capsys.readouterr().err


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


def test_main_continues_after_null_inat_results(monkeypatch, tmp_path, capsys):
    config = tmp_path / "species_config.json"
    config.write_text(json.dumps(dynamic_config()), encoding="utf-8")
    seen = []

    class FakeCollector:
        def __init__(self, **_kwargs):
            pass

        def collect_inat(self, species, _max_rows):
            seen.append(species["seafood_code"])
            if species["seafood_code"] == "FISH_A":
                for _row in {"results": None}.get("results", []):
                    pass
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
    assert "!! FISH_A inat failed: 'NoneType' object is not iterable" in capsys.readouterr().err
