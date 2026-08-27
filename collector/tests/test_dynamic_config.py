import pytest
import requests

from collector.collect_fish_images import Collector, normalize_species_config


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


def test_config_accepts_any_positive_species_count_and_applies_defaults():
    parsed = normalize_species_config(dynamic_config())
    assert [item["seafood_code"] for item in parsed["species"]] == ["FISH_A", "FISH_B"]
    assert parsed["species"][0]["commons_category"] == "Category:Piscis alpha"
    assert parsed["species"][0]["fish_vista_filter"] == "Piscis alpha"
    assert parsed["species"][1]["inat_taxon_id"] == 123


@pytest.mark.parametrize("species", [[], [dynamic_config()["species"][0]] * 2])
def test_config_rejects_empty_or_duplicate_species(species):
    raw = {**dynamic_config(), "species": species}
    with pytest.raises(ValueError):
        normalize_species_config(raw)


@pytest.mark.parametrize(
    "raw",
    [
        {**dynamic_config(), "unexpected": True},
        {**dynamic_config(), "species": [{**dynamic_config()["species"][0], "inat_taxon_id": 0}]},
        {**dynamic_config(), "species": [{**dynamic_config()["species"][0], "mistyped_key": "value"}]},
    ],
)
def test_config_rejects_unknown_keys_and_non_positive_overrides(raw):
    with pytest.raises(ValueError):
        normalize_species_config(raw)


def collector_with_json_response(response):
    collector = Collector(session=requests.Session(), sleep_fn=lambda _seconds: None)
    collector.get_json = response
    return collector


def test_resolve_inat_taxon_id_uses_exact_scientific_name_match():
    species = normalize_species_config(dynamic_config())["species"][0]
    collector = collector_with_json_response(lambda _url, _params: {
        "results": [{"id": 9, "name": "Piscis alpha"}, {"id": 10, "name": "Piscis alpha subsp."}],
    })

    assert collector.resolve_inat_taxon_id(species) == 9


def test_resolve_inat_taxon_id_rejects_ambiguous_or_missing_exact_match():
    species = normalize_species_config(dynamic_config())["species"][0]
    collector = collector_with_json_response(lambda _url, _params: {
        "results": [{"id": 9, "name": "Piscis alpha"}, {"id": 10, "name": " piscis ALPHA "}],
    })

    with pytest.raises(ValueError, match="FISH_A iNaturalist exact taxon was not resolved"):
        collector.resolve_inat_taxon_id(species)


def test_resolve_gbif_key_prefers_configured_override_without_a_request():
    species = normalize_species_config(dynamic_config())["species"][1]
    collector = collector_with_json_response(lambda _url, _params: pytest.fail("network resolver should not be called"))

    assert collector.resolve_gbif_key(species) == 456


def test_collect_inat_resolves_taxon_before_requesting_observations():
    species = normalize_species_config(dynamic_config())["species"][0]
    calls = []

    def get_json(url, params):
        calls.append((url, params))
        if "taxa" in url:
            return {"results": [{"id": 88, "name": "Piscis alpha"}]}
        return {"results": [], "total_results": 0}

    collector = collector_with_json_response(get_json)
    assert collector.collect_inat(species, 1) == []
    assert calls[1][1]["taxon_id"] == 88


def test_fish_vista_uses_configured_filter_not_scientific_name():
    species = normalize_species_config(dynamic_config())["species"][1]
    csv = (
        "filename,source_filename,arkid,standardized_species,original_url,license,file_name,source\n"
        "custom.jpg,custom.jpg,custom,Custom beta,https://example.test/custom.jpg,CC BY 4.0,,Example\n"
    )
    collector = collector_with_json_response(lambda _url, _params: pytest.fail("JSON should not be requested"))
    collector.get_text = lambda _url: csv

    rows = collector.collect_fish_vista(species, 1)
    assert len(rows) == 1
    assert rows[0]["scientific_name"] == "Piscis beta"


def test_commons_uses_normalized_category_override():
    species = normalize_species_config(dynamic_config())["species"][1]
    seen_params = []
    collector = collector_with_json_response(lambda _url, params: seen_params.append(params) or {"query": {"categorymembers": []}})

    assert collector.collect_commons(species, 1) == []
    assert seen_params[0]["cmtitle"] == "Category:Custom beta"
