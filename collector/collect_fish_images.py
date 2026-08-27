#!/usr/bin/env python3
"""Collect licensed candidate image metadata for SukaSeafood I1 fish classes.

The collector is deliberately conservative:
- metadata-only by default;
- only exact scientific-name/taxon queries;
- only photos with a recognized, usable Creative Commons/public-domain license;
- no candidate is automatically accepted for training;
- manual review fields remain REVIEW/UNASSIGNED.

Supported sources: Fish-Vista, iNaturalist, GBIF and Wikimedia Commons.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator
from urllib.parse import quote
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

import requests

try:
    from PIL import Image
except ImportError:  # pragma: no cover - handled at runtime for metadata-only users
    Image = None

try:
    import imagehash
except ImportError:  # pragma: no cover - handled at runtime for metadata-only users
    imagehash = None


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "species_config.json"
DEFAULT_OUTPUT = ROOT / "output"
USER_AGENT = "SukaSeafood-FIT5120-I1-Dataset-Collector/1.1 (course research; respectful metadata collection)"

FISH_VISTA_CSVS = {
    "train": "https://huggingface.co/datasets/imageomics/fish-vista/raw/main/classification_train.csv",
    "val": "https://huggingface.co/datasets/imageomics/fish-vista/raw/main/classification_val.csv",
    "test": "https://huggingface.co/datasets/imageomics/fish-vista/raw/main/classification_test.csv",
}
FISH_VISTA_IMAGE_BASE = "https://huggingface.co/datasets/imageomics/fish-vista/resolve/main/"
INAT_API = "https://api.inaturalist.org/v1/observations"
INAT_TAXA_API = "https://api.inaturalist.org/v1/taxa"
GBIF_MATCH_API = "https://api.gbif.org/v1/species/match"
GBIF_OCC_API = "https://api.gbif.org/v1/occurrence/search"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"

ALLOWED_LICENSES = {"CC0", "CC-BY", "CC-BY-SA", "CC-BY-NC", "CC-BY-NC-SA", "PUBLIC-DOMAIN"}
CONFIG_SCHEMA_VERSION = 1
MAX_CONFIG_TEXT_LENGTH = 500
CONFIG_TOP_LEVEL_KEYS = {"schema_version", "generated_at", "species"}
CONFIG_SPECIES_KEYS = {
    "seafood_code",
    "name_zh",
    "name_en",
    "scientific_name",
    "inat_taxon_id",
    "gbif_taxon_key",
    "commons_category",
    "fish_vista_filter",
}

MANIFEST_COLUMNS = [
    "image_id",
    "seafood_code",
    "app_label",
    "scientific_name",
    "source_dataset",
    "source_record_id",
    "source_taxon_match",
    "source_url",
    "image_url",
    "creator",
    "license",
    "license_url",
    "attribution",
    "source_observation_quality",
    "source_country",
    "source_location",
    "source_date",
    "source_split",
    "image_context",
    "whole_fish",
    "exact_species_verified",
    "verified_by",
    "verification_notes",
    "original_group_id",
    "sha256",
    "perceptual_hash",
    "local_path",
    "split",
    "status",
    "rejection_reason",
]


def normalize_license(value: Any) -> str:
    """Normalize common Creative Commons strings/URLs to a short stable code."""
    if value is None:
        return ""
    raw = html.unescape(str(value)).strip()
    if not raw:
        return ""
    low = raw.lower().replace("_", "-").replace(" ", "-")
    low = re.sub(r"-+", "-", low)

    if "publicdomain/zero" in low or low in {"cc0", "cc-0", "cc-zero"} or "cc0-1.0" in low:
        return "CC0"
    if "publicdomain/mark" in low or "public-domain" in low or low == "pd":
        return "PUBLIC-DOMAIN"

    # Remove URL scaffolding and version numbers before recognizing the license family.
    tokens = low
    tokens = tokens.replace("https://creativecommons.org/licenses/", "")
    tokens = tokens.replace("http://creativecommons.org/licenses/", "")
    tokens = tokens.replace("creativecommons.org/licenses/", "")
    tokens = tokens.strip("/")
    tokens = re.sub(r"(?:^|-)cc-?", "cc-", tokens)
    tokens = re.sub(r"(?:-\d+)+(?:/)?$", "", tokens).strip("-/")
    tokens = re.sub(r"-?\d+(?:\.\d+)?(?:/)?$", "", tokens).strip("-/")

    # iNaturalist-style codes can be simply cc-by-nc.
    if tokens.startswith("cc-"):
        code = tokens.upper()
    elif tokens.startswith("by-") or tokens == "by":
        code = "CC-" + tokens.upper()
    else:
        # Scan free text such as "CC BY-NC-SA 4.0".
        m = re.search(r"cc-?(by(?:-nc)?(?:-sa|-nd)?(?:-sa|-nd)?)", low)
        if m:
            code = "CC-" + m.group(1).upper()
        else:
            return ""

    # Canonicalize ordering for the common combinations.
    canonical = {
        "CC-BY": "CC-BY",
        "CC-BY-SA": "CC-BY-SA",
        "CC-BY-NC": "CC-BY-NC",
        "CC-BY-NC-SA": "CC-BY-NC-SA",
        "CC-BY-ND": "CC-BY-ND",
        "CC-BY-NC-ND": "CC-BY-NC-ND",
    }
    return canonical.get(code, code)


def allowed_license(value: Any) -> bool:
    return normalize_license(value) in ALLOWED_LICENSES


def clean_html(value: Any) -> str:
    if value is None:
        return ""
    text = re.sub(r"<[^>]+>", " ", str(value))
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def norm_species(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def fish_vista_exact_match(candidate: Any, scientific_name: str) -> bool:
    return norm_species(candidate) == norm_species(scientific_name)


def stable_image_id(seafood_code: str, source_dataset: str, source_record_id: str) -> str:
    digest = hashlib.sha256(f"{seafood_code}|{source_dataset}|{source_record_id}".encode("utf-8")).hexdigest()[:16]
    return f"{seafood_code}-{digest}"


def blank_review_fields() -> dict[str, str]:
    return {
        "image_context": "REVIEW",
        "whole_fish": "REVIEW",
        "exact_species_verified": "REVIEW",
        "verified_by": "",
        "verification_notes": "",
        "sha256": "",
        "perceptual_hash": "",
        "local_path": "",
        "split": "UNASSIGNED",
        "status": "CANDIDATE",
        "rejection_reason": "",
    }


def base_row(seafood_code: str, app_label: str, scientific_name: str) -> dict[str, str]:
    row = {c: "" for c in MANIFEST_COLUMNS}
    row.update(
        {
            "seafood_code": seafood_code,
            "app_label": app_label,
            "scientific_name": scientific_name,
        }
    )
    row.update(blank_review_fields())
    return row


def inat_large_url(url: str) -> str:
    # iNaturalist photo URLs normally expose named sizes. Large is adequate for
    # candidate review/training while avoiding unnecessary original-size traffic.
    return re.sub(r"/(square|small|medium)\.", "/large.", url or "")


def parse_inat_photo(obs: dict[str, Any], seafood_code: str, app_label: str, scientific_name: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    obs_id = obs.get("id")
    source_url = obs.get("uri") or (f"https://www.inaturalist.org/observations/{obs_id}" if obs_id else "")
    taxon_name = (obs.get("taxon") or {}).get("name", "")
    if taxon_name and not fish_vista_exact_match(taxon_name, scientific_name):
        return rows

    for photo in obs.get("photos") or []:
        lic = normalize_license(photo.get("license_code") or photo.get("license"))
        if lic not in ALLOWED_LICENSES:
            continue
        photo_id = photo.get("id")
        source_record_id = f"obs:{obs_id}/photo:{photo_id}"
        row = base_row(seafood_code, app_label, scientific_name)
        row.update(
            {
                "image_id": stable_image_id(seafood_code, "INATURALIST", source_record_id),
                "source_dataset": "INATURALIST",
                "source_record_id": source_record_id,
                "source_taxon_match": "EXACT",
                "source_url": source_url,
                "image_url": inat_large_url(photo.get("url", "")),
                "creator": clean_html(photo.get("attribution") or (obs.get("user") or {}).get("login", "")),
                "license": lic,
                "license_url": "",
                "attribution": clean_html(photo.get("attribution")),
                "source_observation_quality": str(obs.get("quality_grade") or ""),
                "source_country": "",
                "source_location": str(obs.get("place_guess") or ""),
                "source_date": str(obs.get("observed_on") or obs.get("time_observed_at") or ""),
                "source_split": "",
                "original_group_id": f"INATURALIST-OBS-{obs_id}",
            }
        )
        rows.append(row)
    return rows


def parse_gbif_media(occurrence: dict[str, Any], seafood_code: str, app_label: str, scientific_name: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    occ_key = occurrence.get("key")
    source_url = occurrence.get("references") or (f"https://www.gbif.org/occurrence/{occ_key}" if occ_key else "")
    occ_name = occurrence.get("species") or occurrence.get("acceptedScientificName") or occurrence.get("scientificName") or ""
    taxon_match = "EXACT" if norm_species(occ_name).startswith(norm_species(scientific_name)) else "QUERY_TAXON"

    for idx, media in enumerate(occurrence.get("media") or []):
        media_type = str(media.get("type") or "").casefold()
        if media_type and media_type not in {"stillimage", "still image"}:
            continue
        image_url = media.get("identifier") or media.get("references") or ""
        lic = normalize_license(media.get("license"))
        if not image_url or lic not in ALLOWED_LICENSES:
            continue
        media_id = media.get("identifier") or media.get("title") or idx
        source_record_id = f"occ:{occ_key}/media:{media_id}"
        row = base_row(seafood_code, app_label, scientific_name)
        row.update(
            {
                "image_id": stable_image_id(seafood_code, "GBIF", source_record_id),
                "source_dataset": "GBIF",
                "source_record_id": source_record_id,
                "source_taxon_match": taxon_match,
                "source_url": source_url,
                "image_url": str(image_url),
                "creator": clean_html(media.get("creator")),
                "license": lic,
                "license_url": str(media.get("license") or ""),
                "attribution": clean_html(media.get("title") or media.get("description")),
                "source_observation_quality": str(occurrence.get("basisOfRecord") or ""),
                "source_country": str(occurrence.get("country") or occurrence.get("countryCode") or ""),
                "source_location": str(occurrence.get("locality") or occurrence.get("stateProvince") or ""),
                "source_date": str(occurrence.get("eventDate") or occurrence.get("year") or ""),
                "source_split": "",
                "original_group_id": f"GBIF-OCC-{occ_key}",
            }
        )
        rows.append(row)
    return rows


def ext_value(meta: dict[str, Any], key: str) -> str:
    item = (meta or {}).get(key) or {}
    return str(item.get("value") or "") if isinstance(item, dict) else str(item or "")


def parse_commons_page(page: dict[str, Any], seafood_code: str, app_label: str, scientific_name: str) -> dict[str, str] | None:
    infos = page.get("imageinfo") or []
    if not infos:
        return None
    info = infos[0]
    mime = str(info.get("mime") or "")
    if mime and not mime.startswith("image/"):
        return None
    meta = info.get("extmetadata") or {}
    lic_raw = ext_value(meta, "LicenseShortName") or ext_value(meta, "UsageTerms")
    lic = normalize_license(lic_raw)
    if lic not in ALLOWED_LICENSES:
        return None
    page_id = page.get("pageid")
    title = str(page.get("title") or "")
    source_record_id = f"page:{page_id}:{title}"
    row = base_row(seafood_code, app_label, scientific_name)
    row.update(
        {
            "image_id": stable_image_id(seafood_code, "WIKIMEDIA_COMMONS", source_record_id),
            "source_dataset": "WIKIMEDIA_COMMONS",
            "source_record_id": source_record_id,
            "source_taxon_match": "EXACT_CATEGORY",
            "source_url": str(info.get("descriptionurl") or f"https://commons.wikimedia.org/wiki/{quote(title.replace(' ', '_'))}"),
            "image_url": str(info.get("url") or ""),
            "creator": clean_html(ext_value(meta, "Artist")),
            "license": lic,
            "license_url": clean_html(ext_value(meta, "LicenseUrl")),
            "attribution": clean_html(ext_value(meta, "Credit") or ext_value(meta, "Attribution")),
            "source_observation_quality": "",
            "source_country": "",
            "source_location": "",
            "source_date": clean_html(ext_value(meta, "DateTimeOriginal") or ext_value(meta, "DateTime")),
            "source_split": "",
            "original_group_id": f"COMMONS-PAGE-{page_id}",
        }
    )
    return row


def parse_fish_vista_row(
    record: dict[str, str],
    source_split: str,
    seafood_code: str,
    app_label: str,
    scientific_name: str,
    fish_vista_filter: str | None = None,
) -> dict[str, str] | None:
    species = record.get("standardized_species") or record.get("species") or ""
    if not fish_vista_exact_match(species, fish_vista_filter or scientific_name):
        return None
    raw_license = record.get("license") or ""
    lic = normalize_license(raw_license)
    # Fish-Vista contains per-image licenses; metadata rows without a recognizable
    # license remain excluded from the candidate manifest by default.
    if lic not in ALLOWED_LICENSES:
        return None
    repository_path = str(record.get("file_name") or "").strip()
    original_url = str(record.get("original_url") or "").strip()
    fallback_name = str(record.get("filename") or "").strip()
    record_id = record.get("arkid") or record.get("source_filename") or repository_path or fallback_name
    if not record_id:
        return None
    row = base_row(seafood_code, app_label, scientific_name)
    row.update(
        {
            "image_id": stable_image_id(seafood_code, "FISH_VISTA", str(record_id)),
            "source_dataset": "FISH_VISTA",
            "source_record_id": str(record_id),
            "source_taxon_match": "EXACT",
            "source_url": original_url or str(record.get("arkid") or ""),
            "image_url": (
                FISH_VISTA_IMAGE_BASE + quote(repository_path, safe="/") + "?download=true"
                if repository_path
                else original_url
            ),
            "creator": str(record.get("owner") or ""),
            "license": lic,
            "license_url": str(raw_license),
            "attribution": str(record.get("owner") or record.get("source") or ""),
            "source_observation_quality": "CURATED_DATASET",
            "source_country": "",
            "source_location": "",
            "source_date": "",
            "source_split": source_split,
            "image_context": "MUSEUM",
            "original_group_id": f"FISH-VISTA-{record.get('source_filename') or record_id}",
        }
    )
    return row


@dataclass
class Collector:
    session: requests.Session
    delay_seconds: float = 1.05
    commons_delay_seconds: float = 6.5
    max_retries: int = 4
    retry_backoff_seconds: float = 10.0
    sleep_fn: Callable[[float], None] = time.sleep
    _text_cache: dict[str, str] = field(default_factory=dict)

    def _delay_for_url(self, url: str) -> float:
        return self.commons_delay_seconds if url.startswith(COMMONS_API) else self.delay_seconds

    def _retry_after_seconds(self, response: requests.Response, attempt: int) -> float:
        raw = str(response.headers.get("Retry-After") or "").strip()
        if raw:
            try:
                return max(0.0, float(raw))
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(raw)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    pass
        return self.retry_backoff_seconds * (2 ** attempt)

    def _get_with_retry(self, url: str, **kwargs: Any) -> requests.Response:
        attempt = 0
        while True:
            response = self.session.get(url, **kwargs)
            if response.status_code not in {429, 503}:
                response.raise_for_status()
                self.sleep_fn(self._delay_for_url(url))
                return response
            if attempt >= self.max_retries:
                response.raise_for_status()
            wait = self._retry_after_seconds(response, attempt)
            print(
                f"  .. HTTP {response.status_code} from {url}; retrying in {wait:.1f}s "
                f"({attempt + 1}/{self.max_retries})",
                file=sys.stderr,
            )
            self.sleep_fn(wait)
            attempt += 1

    def get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        response = self._get_with_retry(url, params=params, timeout=40)
        return response.json()

    def get_text(self, url: str) -> str:
        if url in self._text_cache:
            return self._text_cache[url]
        response = self._get_with_retry(url, timeout=60)
        self._text_cache[url] = response.text
        return response.text

    def collect_fish_vista(self, species: dict[str, Any], max_rows: int) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for split, url in FISH_VISTA_CSVS.items():
            text = self.get_text(url)
            for record in csv.DictReader(io.StringIO(text)):
                parsed = parse_fish_vista_row(
                    record,
                    split,
                    species["seafood_code"],
                    species["app_label"],
                    species["scientific_name"],
                    species["fish_vista_filter"],
                )
                if parsed:
                    out.append(parsed)
                    if len(out) >= max_rows:
                        return out
        return out

    def collect_inat(self, species: dict[str, Any], max_rows: int) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        page = 1
        per_page = min(100, max_rows)
        taxon_id = self.resolve_inat_taxon_id(species)
        while len(out) < max_rows:
            data = self.get_json(
                INAT_API,
                {
                    "taxon_id": taxon_id,
                    "quality_grade": "research",
                    "photos": "true",
                    "per_page": per_page,
                    "page": page,
                    "order_by": "id",
                    "order": "desc",
                },
            )
            results = data.get("results") or []
            if not results:
                break
            for obs in results:
                for row in parse_inat_photo(obs, species["seafood_code"], species["app_label"], species["scientific_name"]):
                    out.append(row)
                    if len(out) >= max_rows:
                        return out
            page += 1
            if page * per_page > int(data.get("total_results") or 0) + per_page:
                break
        return out

    def resolve_inat_taxon_id(self, species: dict[str, Any]) -> int:
        if species["inat_taxon_id"] is not None:
            return int(species["inat_taxon_id"])
        data = self.get_json(INAT_TAXA_API, {"q": species["scientific_name"], "rank": "species"})
        exact = [
            row
            for row in data.get("results", [])
            if norm_species(row.get("name")) == norm_species(species["scientific_name"])
        ]
        if len(exact) != 1 or not exact[0].get("id"):
            raise ValueError(
                f"{species['seafood_code']} iNaturalist exact taxon was not resolved; set inat_taxon_id"
            )
        return int(exact[0]["id"])

    def resolve_gbif_key(self, species: dict[str, Any]) -> int:
        if species["gbif_taxon_key"] is not None:
            return int(species["gbif_taxon_key"])
        data = self.get_json(GBIF_MATCH_API, {"name": species["scientific_name"], "rank": "SPECIES"})
        if data.get("usageKey") and str(data.get("matchType") or "").upper() != "NONE":
            return int(data["usageKey"])
        raise ValueError(f"{species['seafood_code']} GBIF taxon was not resolved; set gbif_taxon_key")

    def collect_gbif(self, species: dict[str, Any], max_rows: int) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        key = self.resolve_gbif_key(species)
        offset = 0
        limit = min(300, max_rows)
        while len(out) < max_rows:
            data = self.get_json(
                GBIF_OCC_API,
                {"taxon_key": key, "media_type": "StillImage", "limit": limit, "offset": offset},
            )
            results = data.get("results") or []
            if not results:
                break
            for occ in results:
                for row in parse_gbif_media(occ, species["seafood_code"], species["app_label"], species["scientific_name"]):
                    out.append(row)
                    if len(out) >= max_rows:
                        return out
            offset += len(results)
            if data.get("endOfRecords") or offset >= int(data.get("count") or 0):
                break
        return out

    def collect_commons(self, species: dict[str, Any], max_rows: int) -> list[dict[str, str]]:
        titles: list[str] = []
        cont: str | None = None
        while len(titles) < max_rows:
            params: dict[str, Any] = {
                "action": "query",
                "format": "json",
                "formatversion": 2,
                "list": "categorymembers",
                "cmtitle": species["commons_category"],
                "cmnamespace": 6,
                "cmtype": "file",
                "cmlimit": min(500, max_rows),
            }
            if cont:
                params["cmcontinue"] = cont
            data = self.get_json(COMMONS_API, params)
            members = (data.get("query") or {}).get("categorymembers") or []
            titles.extend(str(m.get("title")) for m in members if m.get("title"))
            cont = (data.get("continue") or {}).get("cmcontinue")
            if not cont or not members:
                break
        titles = titles[:max_rows]

        out: list[dict[str, str]] = []
        # extmetadata is relatively expensive; keep batches deliberately small.
        for start in range(0, len(titles), 20):
            batch = titles[start : start + 20]
            data = self.get_json(
                COMMONS_API,
                {
                    "action": "query",
                    "format": "json",
                    "formatversion": 2,
                    "titles": "|".join(batch),
                    "prop": "imageinfo",
                    "iiprop": "url|mime|extmetadata",
                    "iiextmetadatafilter": "LicenseShortName|LicenseUrl|Artist|Credit|Attribution|UsageTerms|DateTimeOriginal|DateTime",
                },
            )
            for page in (data.get("query") or {}).get("pages") or []:
                parsed = parse_commons_page(page, species["seafood_code"], species["app_label"], species["scientific_name"])
                if parsed:
                    out.append(parsed)
                    if len(out) >= max_rows:
                        return out
        return out


def _config_text(value: Any, field_name: str, *, required: bool) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    text = value.strip()
    if not text or len(text) > MAX_CONFIG_TEXT_LENGTH:
        raise ValueError(f"{field_name} must be non-empty text up to {MAX_CONFIG_TEXT_LENGTH} characters")
    return text


def _positive_integer_override(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer or null")
    return value


def normalize_species_config(raw: Any) -> dict[str, Any]:
    if (
        not isinstance(raw, dict)
        or type(raw.get("schema_version")) is not int
        or raw["schema_version"] != CONFIG_SCHEMA_VERSION
    ):
        raise ValueError("collector config schema_version must be 1")
    unknown_top_level = set(raw) - CONFIG_TOP_LEVEL_KEYS
    if unknown_top_level:
        raise ValueError(f"unknown collector config key(s): {', '.join(sorted(unknown_top_level))}")
    generated_at = _config_text(raw.get("generated_at"), "generated_at", required=False)
    items = raw.get("species")
    if not isinstance(items, list) or not items:
        raise ValueError("collector config must contain at least one active species")

    normalized = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("each species config entry must be an object")
        unknown_species_keys = set(item) - CONFIG_SPECIES_KEYS
        if unknown_species_keys:
            raise ValueError(f"unknown species config key(s): {', '.join(sorted(unknown_species_keys))}")
        code = _config_text(item.get("seafood_code"), "seafood_code", required=True)
        scientific_name = _config_text(item.get("scientific_name"), "scientific_name", required=True)
        name_zh = _config_text(item.get("name_zh"), "name_zh", required=True)
        name_en = _config_text(item.get("name_en"), "name_en", required=True)
        if code in seen:
            raise ValueError("species code and names must be non-empty and unique")
        seen.add(code)
        commons_category = _config_text(item.get("commons_category"), "commons_category", required=False)
        fish_vista_filter = _config_text(item.get("fish_vista_filter"), "fish_vista_filter", required=False)
        normalized.append(
            {
                "seafood_code": code,
                "name_zh": name_zh,
                "name_en": name_en,
                "app_label": name_en,
                "scientific_name": scientific_name,
                "inat_taxon_id": _positive_integer_override(item.get("inat_taxon_id"), "inat_taxon_id"),
                "gbif_taxon_key": _positive_integer_override(item.get("gbif_taxon_key"), "gbif_taxon_key"),
                "commons_category": commons_category or f"Category:{scientific_name}",
                "fish_vista_filter": fish_vista_filter or scientific_name,
            }
        )
    return {"schema_version": CONFIG_SCHEMA_VERSION, "generated_at": generated_at, "species": normalized}


def load_config(path: Path) -> dict[str, Any]:
    return normalize_species_config(json.loads(path.read_text(encoding="utf-8")))


def write_manifest(rows: Iterable[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=MANIFEST_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in MANIFEST_COLUMNS})


def read_manifest(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        return [{c: row.get(c, "") for c in MANIFEST_COLUMNS} for row in csv.DictReader(f)]


def merge_resume_rows(existing_rows: Iterable[dict[str, str]], new_rows: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    # Existing rows come first so any manual review/download state is preserved
    # when a re-collected candidate has the same metadata identity.
    return dedupe_metadata([*existing_rows, *new_rows])


def extension_from_url(url: str) -> str:
    clean = (url or "").split("?", 1)[0].lower()
    ext = Path(clean).suffix
    return ext if ext in {".jpg", ".jpeg", ".png", ".webp"} else ".jpg"


def download_one(session: requests.Session, row: dict[str, str], images_dir: Path) -> dict[str, str]:
    if Image is None or imagehash is None:
        raise RuntimeError("Image downloading requires Pillow and ImageHash. Run: pip install -r requirements.txt")
    url = row.get("image_url") or ""
    if not url:
        row["rejection_reason"] = "NO_IMAGE_URL"
        return row
    species_dir = images_dir / row["seafood_code"] / row["source_dataset"].lower()
    species_dir.mkdir(parents=True, exist_ok=True)
    local = species_dir / f"{row['image_id']}{extension_from_url(url)}"
    try:
        r = session.get(url, timeout=60, stream=True)
        r.raise_for_status()
        content = r.content
        with Image.open(io.BytesIO(content)) as im:
            im.verify()
        with Image.open(io.BytesIO(content)) as im:
            phash = str(imagehash.phash(im.convert("RGB")))
        local.write_bytes(content)
        row["sha256"] = hashlib.sha256(content).hexdigest()
        row["perceptual_hash"] = phash
        row["local_path"] = str(local.relative_to(images_dir.parent))
    except Exception as exc:  # network/decoding errors remain reviewable in the manifest
        row["rejection_reason"] = f"DOWNLOAD_ERROR:{type(exc).__name__}"
    return row


def dedupe_metadata(rows: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for row in rows:
        key = (row.get("seafood_code", ""), row.get("image_url", "") or row.get("source_record_id", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    p.add_argument("--source", choices=["all", "fish-vista", "inat", "gbif", "commons"], default="all")
    p.add_argument("--species", action="append", help="Seafood code, e.g. FISH_A. Repeat for multiple. Default: all configured species.")
    p.add_argument("--max-per-species", type=int, default=100, help="Maximum candidate rows per species per source (default 100).")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--download-images", action="store_true", help="Download image bytes after metadata collection. Default is metadata-only.")
    p.add_argument("--resume", action="store_true", help="Merge newly collected rows into an existing output/candidates.csv instead of overwriting it.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.max_per_species < 1:
        raise SystemExit("--max-per-species must be >= 1")
    cfg = load_config(args.config)
    selected = set(args.species or [])
    species_list = [x for x in cfg["species"] if not selected or x["seafood_code"] in selected]
    unknown = selected - {x["seafood_code"] for x in cfg["species"]}
    if unknown:
        raise SystemExit(f"Unknown seafood code(s): {', '.join(sorted(unknown))}")

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json,text/csv,*/*"})
    collector = Collector(session=session, delay_seconds=1.05, commons_delay_seconds=6.5)
    sources = [args.source] if args.source != "all" else ["fish-vista", "inat", "gbif", "commons"]

    manifest = args.output_dir / "candidates.csv"
    existing_rows: list[dict[str, str]] = []
    if args.resume:
        existing_rows = read_manifest(manifest)
        if existing_rows:
            print(f"Resume mode: loaded {len(existing_rows)} existing candidate rows from {manifest}", file=sys.stderr)
        else:
            print(f"Resume mode: no existing manifest found at {manifest}; starting from empty", file=sys.stderr)

    rows: list[dict[str, str]] = []
    for species in species_list:
        for source in sources:
            print(f"[{species['seafood_code']}] collecting {source} metadata...", file=sys.stderr)
            try:
                if source == "fish-vista":
                    found = collector.collect_fish_vista(species, args.max_per_species)
                elif source == "inat":
                    found = collector.collect_inat(species, args.max_per_species)
                elif source == "gbif":
                    found = collector.collect_gbif(species, args.max_per_species)
                elif source == "commons":
                    found = collector.collect_commons(species, args.max_per_species)
                else:  # pragma: no cover
                    found = []
                rows.extend(found)
                print(f"  -> {len(found)} usable licensed candidate rows", file=sys.stderr)
            except (requests.RequestException, ValueError, AttributeError) as exc:
                print(f"!! {species['seafood_code']} {source} failed: {exc}", file=sys.stderr)

    rows = merge_resume_rows(existing_rows, rows) if args.resume else dedupe_metadata(rows)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.download_images:
        for i, row in enumerate(rows, start=1):
            if i % 25 == 0:
                print(f"downloading {i}/{len(rows)}...", file=sys.stderr)
            download_one(session, row, args.output_dir / "images")

    write_manifest(rows, manifest)
    print(f"Wrote {len(rows)} candidate rows to {manifest}")
    print("Important: CANDIDATE != training-approved. Manually review whole_fish and exact_species_verified before use.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
