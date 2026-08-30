#!/usr/bin/env python3
"""Collect licensed candidate image metadata for SukaSeafood I1 fish classes.

The collector is deliberately conservative:
- metadata-only by default;
- only exact scientific-name/taxon queries;
- only photos with a recognized, usable Creative Commons/public-domain license;
- no candidate is automatically accepted for training;
- manual review fields remain REVIEW/UNASSIGNED.

Supported sources: Fish-Vista, iNaturalist, GBIF, Wikimedia Commons,
Atlas of Living Australia, OBIS, Smithsonian Open Access, and the NOAA Photo
Library.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import os
import re
import stat
import sys
import tempfile
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator
from urllib.parse import quote, unquote, urljoin, urlsplit
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
ALA_API = "https://api.ala.org.au/occurrences/occurrences/search"
OBIS_API = "https://api.obis.org/v3/occurrence"
SMITHSONIAN_SEARCH_API = "https://api.si.edu/openaccess/api/v1.0/search"
SMITHSONIAN_CONTENT_API = "https://api.si.edu/openaccess/api/v1.0/content"
NOAA_SEARCH_URL = "https://www.noaa.gov/noaa-collections/search/photo-library"
ALL_SOURCES = (
    "fish-vista",
    "inat",
    "gbif",
    "commons",
    "ala",
    "obis",
    "noaa",
    "smithsonian",
)
SOURCE_DATASET_CODES = {
    "fish-vista": "FISH_VISTA",
    "inat": "INATURALIST",
    "gbif": "GBIF",
    "commons": "WIKIMEDIA_COMMONS",
    "ala": "ATLAS_OF_LIVING_AUSTRALIA",
    "obis": "OBIS",
    "noaa": "NOAA_PHOTO_LIBRARY",
    "smithsonian": "SMITHSONIAN_OPEN_ACCESS",
}

ALLOWED_LICENSES = {"CC0", "CC-BY", "CC-BY-SA", "CC-BY-NC", "CC-BY-NC-SA", "PUBLIC-DOMAIN"}
CONFIG_SCHEMA_VERSION = 2
MAX_CONFIG_TEXT_LENGTH = 500
MAX_COMMONS_CATEGORY_LENGTH = 512
MAX_FISH_VISTA_FILTER_LENGTH = 255
CONFIG_TOP_LEVEL_KEYS = {"schema_version", "generated_at", "species"}
CONFIG_SPECIES_KEYS = {
    "seafood_code",
    "name_zh",
    "name_en",
    "scientific_name",
    "candidate_count",
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
    tokens = re.sub(r"/(?:legalcode|deed(?:\.[a-z-]+)?)$", "", tokens)
    tokens = re.sub(r"-\((?:int|international)\)$", "", tokens)
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
        m = re.search(
            r"(?:^|-)cc-?(by(?:-nc)?(?:-sa|-nd)?)(?:-\d+(?:\.\d+)?)?(?:-\((?:int|international)\))?$",
            low,
        )
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
    if code in canonical:
        return canonical[code]
    return code


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


def text_contains_exact_scientific_name(value: Any, scientific_name: str) -> bool:
    parts = [re.escape(part) for part in scientific_name.strip().split()]
    if not parts:
        return False
    pattern = r"\s+".join(parts)
    return re.search(rf"(?<![A-Za-z]){pattern}(?![A-Za-z])", str(value or ""), flags=re.I) is not None


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


def _source_date(value: Any) -> str:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat()
        except (OSError, OverflowError, ValueError):
            return ""
    return str(value or "")


def parse_ala_occurrence(
    occurrence: dict[str, Any], seafood_code: str, app_label: str, scientific_name: str
) -> list[dict[str, str]]:
    if norm_species(occurrence.get("scientificName")) != norm_species(scientific_name):
        return []
    lic = normalize_license(occurrence.get("license"))
    if lic not in ALLOWED_LICENSES:
        return []
    occurrence_id = str(occurrence.get("uuid") or occurrence.get("id") or "").strip()
    if not occurrence_id:
        return []
    images = [str(value).strip() for value in occurrence.get("images") or [] if str(value).strip()]
    if not images and occurrence.get("imageUrl"):
        images = ["primary"]
    source_url = str(
        occurrence.get("references")
        or f"https://biocache.ala.org.au/occurrences/{quote(occurrence_id)}"
    )
    creator = occurrence.get("recordedBy") or ""
    if isinstance(creator, list):
        creator = ", ".join(clean_html(item) for item in creator if clean_html(item))
    rows: list[dict[str, str]] = []
    for index, image_id in enumerate(images):
        image_url = str(occurrence.get("imageUrl") or "") if index == 0 else ""
        if not image_url and image_id != "primary":
            image_url = f"https://images.ala.org.au/image/proxyImage?imageId={quote(image_id)}"
        if not image_url:
            continue
        source_record_id = f"occ:{occurrence_id}/image:{image_id}"
        row = base_row(seafood_code, app_label, scientific_name)
        row.update(
            {
                "image_id": stable_image_id(
                    seafood_code, "ATLAS_OF_LIVING_AUSTRALIA", source_record_id
                ),
                "source_dataset": "ATLAS_OF_LIVING_AUSTRALIA",
                "source_record_id": source_record_id,
                "source_taxon_match": "EXACT",
                "source_url": source_url,
                "image_url": image_url,
                "creator": clean_html(creator),
                "license": lic,
                "license_url": str(occurrence.get("license") or ""),
                "attribution": clean_html(creator),
                "source_observation_quality": str(
                    occurrence.get("identificationVerificationStatus")
                    or occurrence.get("basisOfRecord")
                    or ""
                ),
                "source_country": str(occurrence.get("country") or ""),
                "source_location": str(
                    occurrence.get("locality") or occurrence.get("stateProvince") or ""
                ),
                "source_date": _source_date(occurrence.get("eventDate")),
                "original_group_id": f"ALA-OCC-{occurrence_id}",
            }
        )
        rows.append(row)
    return rows


def parse_obis_occurrence(
    occurrence: dict[str, Any], seafood_code: str, app_label: str, scientific_name: str
) -> list[dict[str, str]]:
    if norm_species(occurrence.get("scientificName")) != norm_species(scientific_name):
        return []
    lic = normalize_license(occurrence.get("license"))
    if lic not in ALLOWED_LICENSES:
        return []
    occurrence_id = str(occurrence.get("id") or occurrence.get("occurrenceID") or "").strip()
    if not occurrence_id:
        return []
    media_value = occurrence.get("associatedMedia") or ""
    media_values = media_value if isinstance(media_value, list) else re.split(
        r"\s*[|;]\s*|,\s*(?=https?://)", str(media_value)
    )
    urls = [str(value).strip() for value in media_values if str(value).strip().startswith("https://")]
    creator = occurrence.get("recordedBy") or ""
    if isinstance(creator, list):
        creator = ", ".join(clean_html(item) for item in creator if clean_html(item))
    source_url = str(
        occurrence.get("references") or f"https://obis.org/occurrence/{quote(occurrence_id)}"
    )
    rows: list[dict[str, str]] = []
    for index, image_url in enumerate(urls, start=1):
        source_record_id = f"occ:{occurrence_id}/media:{index}"
        row = base_row(seafood_code, app_label, scientific_name)
        row.update(
            {
                "image_id": stable_image_id(seafood_code, "OBIS", source_record_id),
                "source_dataset": "OBIS",
                "source_record_id": source_record_id,
                "source_taxon_match": "EXACT",
                "source_url": source_url,
                "image_url": image_url,
                "creator": clean_html(creator),
                "license": lic,
                "license_url": str(occurrence.get("license") or ""),
                "attribution": clean_html(creator),
                "source_observation_quality": str(occurrence.get("basisOfRecord") or ""),
                "source_country": str(occurrence.get("country") or ""),
                "source_location": str(occurrence.get("locality") or ""),
                "source_date": _source_date(occurrence.get("eventDate")),
                "original_group_id": f"OBIS-OCC-{occurrence_id}",
            }
        )
        rows.append(row)
    return rows


def parse_smithsonian_record(
    record: dict[str, Any], seafood_code: str, app_label: str, scientific_name: str
) -> list[dict[str, str]]:
    content = record.get("content") or {}
    indexed = content.get("indexedStructured") or {}
    scientific_names = indexed.get("scientific_name") or []
    if isinstance(scientific_names, str):
        scientific_names = [scientific_names]
    if norm_species(scientific_name) not in {norm_species(value) for value in scientific_names}:
        return []
    record_id = str(record.get("id") or "").strip()
    if not record_id:
        return []
    descriptive = content.get("descriptiveNonRepeating") or {}
    media = ((descriptive.get("online_media") or {}).get("media") or [])
    source_url = str(descriptive.get("record_link") or f"https://www.si.edu/object/{quote(record_id)}")
    if source_url.startswith("http://"):
        source_url = "https://" + source_url[len("http://") :]
    data_source = clean_html(descriptive.get("data_source") or "Smithsonian Institution")
    rows: list[dict[str, str]] = []
    for index, item in enumerate(media, start=1):
        if str(item.get("type") or "").casefold() not in {"image", "images", "stillimage"}:
            continue
        if normalize_license((item.get("usage") or {}).get("access")) != "CC0":
            continue
        image_url = str(item.get("content") or "").strip()
        if not image_url.startswith("https://"):
            continue
        media_id = str(item.get("guid") or index)
        source_record_id = f"record:{record_id}/media:{media_id}"
        row = base_row(seafood_code, app_label, scientific_name)
        row.update(
            {
                "image_id": stable_image_id(
                    seafood_code, "SMITHSONIAN_OPEN_ACCESS", source_record_id
                ),
                "source_dataset": "SMITHSONIAN_OPEN_ACCESS",
                "source_record_id": source_record_id,
                "source_taxon_match": "EXACT",
                "source_url": source_url,
                "image_url": image_url,
                "creator": data_source,
                "license": "CC0",
                "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
                "attribution": data_source,
                "source_observation_quality": "MUSEUM_COLLECTION",
                "original_group_id": f"SMITHSONIAN-RECORD-{record_id}",
                "image_context": "MUSEUM",
            }
        )
        rows.append(row)
    return rows


def _html_attribute(fragment: str, name: str) -> str:
    match = re.search(rf"\b{re.escape(name)}\s*=\s*(['\"])(.*?)\1", fragment, flags=re.I | re.S)
    return html.unescape(match.group(2)).strip() if match else ""


def parse_noaa_search_page(
    page: str, seafood_code: str, app_label: str, scientific_name: str
) -> list[dict[str, str]]:
    starts = []
    for match in re.finditer(r"<div\b[^>]*>", page, flags=re.I | re.S):
        classes = _html_attribute(match.group(0), "class").split()
        if "ngdl-photo" in classes:
            starts.append(match.start())
    chunks = [
        page[start : starts[index + 1] if index + 1 < len(starts) else len(page)]
        for index, start in enumerate(starts)
    ]
    rows: list[dict[str, str]] = []
    for chunk in chunks:
        opening = re.match(r"<div\b[^>]*>", chunk, flags=re.I | re.S)
        image = re.search(r"<img\b[^>]*>", chunk, flags=re.I | re.S)
        download = re.search(
            r"<a\b[^>]*class\s*=\s*(['\"])[^'\"]*\bmedia-download-link\b[^'\"]*\1[^>]*>",
            chunk,
            flags=re.I | re.S,
        )
        if not opening or not image or not download:
            continue
        title = _html_attribute(image.group(0), "title") or _html_attribute(image.group(0), "alt")
        if not text_contains_exact_scientific_name(title, scientific_name):
            continue
        credit_match = re.search(r"\(Image credit:\s*(.*?)\)\s*$", title, flags=re.I)
        credit = clean_html(credit_match.group(1)) if credit_match else ""
        credit_upper = credit.upper()
        if "COURTESY" in credit_upper or not any(
            marker in credit_upper
            for marker in ("NOAA", "NMFS", "SEFSC", "NEFSC", "AFSC", "NWFSC", "PIFSC")
        ):
            continue
        photo_id = _html_attribute(opening.group(0), "id")
        image_url = urljoin("https://www.noaa.gov", _html_attribute(download.group(0), "href"))
        if not photo_id or not image_url.startswith("https://"):
            continue
        source_link = re.search(r"<a\b[^>]*href\s*=\s*(['\"])(.*?)\1", chunk, flags=re.I | re.S)
        source_url = urljoin("https://www.noaa.gov", html.unescape(source_link.group(2))) if source_link else NOAA_SEARCH_URL
        source_record_id = f"photo:{photo_id}"
        row = base_row(seafood_code, app_label, scientific_name)
        row.update(
            {
                "image_id": stable_image_id(
                    seafood_code, "NOAA_PHOTO_LIBRARY", source_record_id
                ),
                "source_dataset": "NOAA_PHOTO_LIBRARY",
                "source_record_id": source_record_id,
                "source_taxon_match": "EXACT_TITLE",
                "source_url": source_url,
                "image_url": image_url,
                "creator": credit,
                "license": "PUBLIC-DOMAIN",
                "license_url": "https://www.noaa.gov/disclaimer",
                "attribution": title,
                "source_observation_quality": "NOAA_CREDITED",
                "original_group_id": f"NOAA-PHOTO-{photo_id}",
            }
        )
        rows.append(row)
    return rows


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
    smithsonian_api_key: str | None = None
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

    def collect_ala(self, species: dict[str, Any], max_rows: int) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        start = 0
        page_size = min(100, max(20, max_rows))
        while len(out) < max_rows:
            data = self.get_json(
                ALA_API,
                {
                    "q": f'scientificName:"{species["scientific_name"]}"',
                    "fq": "multimedia:Image",
                    "pageSize": page_size,
                    "start": start,
                },
            )
            occurrences = data.get("occurrences") or []
            if not occurrences:
                break
            for occurrence in occurrences:
                for row in parse_ala_occurrence(
                    occurrence,
                    species["seafood_code"],
                    species["app_label"],
                    species["scientific_name"],
                ):
                    out.append(row)
                    if len(out) >= max_rows:
                        return out
            start += len(occurrences)
            if start >= int(data.get("totalRecords") or 0):
                break
        return out

    def collect_obis(self, species: dict[str, Any], max_rows: int) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        after: str | None = None
        scanned = 0
        scan_limit = max(1000, max_rows * 20)
        page_size = min(1000, max(100, max_rows * 5))
        while len(out) < max_rows and scanned < scan_limit:
            params: dict[str, Any] = {
                "scientificname": species["scientific_name"],
                "size": page_size,
            }
            if after:
                params["after"] = after
            data = self.get_json(OBIS_API, params)
            occurrences = data.get("results") or []
            if not occurrences:
                break
            for occurrence in occurrences:
                for row in parse_obis_occurrence(
                    occurrence,
                    species["seafood_code"],
                    species["app_label"],
                    species["scientific_name"],
                ):
                    out.append(row)
                    if len(out) >= max_rows:
                        return out
            scanned += len(occurrences)
            next_after = str(occurrences[-1].get("id") or "").strip()
            if not next_after or next_after == after or len(occurrences) < page_size:
                break
            after = next_after
        return out

    def collect_smithsonian(self, species: dict[str, Any], max_rows: int) -> list[dict[str, str]]:
        if not self.smithsonian_api_key:
            raise ValueError(
                "Smithsonian requires --smithsonian-api-key (free key from api.data.gov)"
            )
        out: list[dict[str, str]] = []
        start = 0
        page_size = min(100, max(10, max_rows * 2))
        while len(out) < max_rows:
            data = self.get_json(
                SMITHSONIAN_SEARCH_API,
                {
                    "api_key": self.smithsonian_api_key,
                    "q": f'{species["scientific_name"]} online_media_type:Images media_usage:CC0',
                    "start": start,
                    "rows": page_size,
                },
            )
            response = data.get("response") or {}
            records = response.get("rows") or []
            if not records:
                break
            for summary in records:
                record = summary
                if not (((record.get("content") or {}).get("descriptiveNonRepeating") or {}).get("online_media")):
                    record_id = str(summary.get("id") or "").strip()
                    if not record_id:
                        continue
                    detail = self.get_json(
                        f"{SMITHSONIAN_CONTENT_API}/{quote(record_id, safe='')}/",
                        {"api_key": self.smithsonian_api_key},
                    )
                    record = detail.get("response") or detail
                for row in parse_smithsonian_record(
                    record,
                    species["seafood_code"],
                    species["app_label"],
                    species["scientific_name"],
                ):
                    out.append(row)
                    if len(out) >= max_rows:
                        return out
            start += len(records)
            if start >= int(response.get("rowCount") or 0):
                break
        return out

    def collect_noaa(self, species: dict[str, Any], max_rows: int) -> list[dict[str, str]]:
        url = f"{NOAA_SEARCH_URL}?search_api_fulltext={quote(species['scientific_name'])}"
        return parse_noaa_search_page(
            self.get_text(url),
            species["seafood_code"],
            species["app_label"],
            species["scientific_name"],
        )[:max_rows]


def _config_text(
    value: Any,
    field_name: str,
    *,
    required: bool,
    max_length: int = MAX_CONFIG_TEXT_LENGTH,
) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    text = value.strip()
    if not text or len(text) > max_length:
        raise ValueError(f"{field_name} must be non-empty text up to {max_length} characters")
    return text


def _positive_integer_override(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer or null")
    return value


def _nonnegative_integer(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def normalize_species_config(raw: Any) -> dict[str, Any]:
    if (
        not isinstance(raw, dict)
        or type(raw.get("schema_version")) is not int
        or raw["schema_version"] != CONFIG_SCHEMA_VERSION
    ):
        raise ValueError(f"collector config schema_version must be {CONFIG_SCHEMA_VERSION}")
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
        commons_category = _config_text(
            item.get("commons_category"),
            "commons_category",
            required=False,
            max_length=MAX_COMMONS_CATEGORY_LENGTH,
        )
        fish_vista_filter = _config_text(
            item.get("fish_vista_filter"),
            "fish_vista_filter",
            required=False,
            max_length=MAX_FISH_VISTA_FILTER_LENGTH,
        )
        normalized.append(
            {
                "seafood_code": code,
                "name_zh": name_zh,
                "name_en": name_en,
                "app_label": name_en,
                "scientific_name": scientific_name,
                "candidate_count": _nonnegative_integer(item.get("candidate_count"), "candidate_count"),
                "inat_taxon_id": _positive_integer_override(item.get("inat_taxon_id"), "inat_taxon_id"),
                "gbif_taxon_key": _positive_integer_override(item.get("gbif_taxon_key"), "gbif_taxon_key"),
                "commons_category": commons_category or f"Category:{scientific_name}",
                "fish_vista_filter": fish_vista_filter or scientific_name,
            }
        )
    return {"schema_version": CONFIG_SCHEMA_VERSION, "generated_at": generated_at, "species": normalized}


def load_config(path: Path) -> dict[str, Any]:
    return normalize_species_config(json.loads(path.read_text(encoding="utf-8")))


def _path_is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & 0x400
    )


def write_manifest(rows: Iterable[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if _path_is_link_or_reparse(path):
        raise RuntimeError(f"Refusing unsafe manifest path: {path}")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            newline="",
            encoding="utf-8-sig",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".part",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            writer = csv.DictWriter(
                handle, fieldnames=MANIFEST_COLUMNS, extrasaction="ignore"
            )
            writer.writeheader()
            for row in rows:
                writer.writerow({c: row.get(c, "") for c in MANIFEST_COLUMNS})
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


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


ARCHIVE_RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
ARCHIVE_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
ARCHIVE_ATTEMPTS = 4
ARCHIVE_MAX_REDIRECTS = 5
ARCHIVE_BACKOFF_SECONDS = (2.0, 4.0, 8.0)
ARCHIVE_MAX_RETRY_AFTER_SECONDS = 30.0
ARCHIVE_MAX_IMAGE_BYTES = 100 * 1024 * 1024
ARCHIVE_MAX_IMAGE_PIXELS = 40_000_000
ARCHIVE_MAX_IMAGE_DIMENSION = 20_000
ARCHIVE_HASH_IMAGE_DIMENSION = 1_024
ARCHIVE_PROGRESS_FIELDS = (
    "sha256",
    "perceptual_hash",
    "local_path",
    "rejection_reason",
)
ARCHIVE_PROGRESS_IDENTITY_FIELDS = (
    "image_id",
    "seafood_code",
    "source_dataset",
    "source_record_id",
    "image_url",
)


class ArchiveDownloadFailure(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass
class ArchiveAttemptBudget:
    """One shared request budget across status, connection, and body failures."""

    remaining: int = ARCHIVE_ATTEMPTS
    used: int = 0

    def take(self) -> int | None:
        if self.remaining <= 0:
            return None
        attempt = self.used
        self.used += 1
        self.remaining -= 1
        return attempt


@dataclass(frozen=True)
class ArchiveSummary:
    total: int
    downloaded: int
    skipped: int
    failed: int


@dataclass
class ArchiveHostThrottle:
    """Carry terminal host cooldowns across sequential archive rows."""

    sleep_fn: Callable[[float], None] | None = None
    pending_delays: dict[str, float] = field(default_factory=dict)

    def wait(self, url: str) -> None:
        host = (urlsplit(url).hostname or "").lower()
        delay = self.pending_delays.pop(host, 0.0)
        if delay > 0:
            (self.sleep_fn or time.sleep)(delay)

    def sleep_retry(self, url: str, delay: float) -> None:
        (self.sleep_fn or time.sleep)(delay)
        self.pending_delays.pop((urlsplit(url).hostname or "").lower(), None)

    def defer(self, url: str, delay: float) -> None:
        if delay <= 0:
            return
        host = (urlsplit(url).hostname or "").lower()
        self.pending_delays[host] = max(delay, self.pending_delays.get(host, 0.0))


def _archive_retry_delay(response: requests.Response, attempt: int) -> float:
    raw = str(response.headers.get("Retry-After") or "").strip()
    if raw:
        try:
            parsed = max(0.0, float(raw))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(raw)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                parsed = max(
                    0.0,
                    (retry_at - datetime.now(timezone.utc)).total_seconds(),
                )
            except (TypeError, ValueError, OverflowError):
                parsed = ARCHIVE_BACKOFF_SECONDS[attempt]
        return min(parsed, ARCHIVE_MAX_RETRY_AFTER_SECONDS)
    return ARCHIVE_BACKOFF_SECONDS[attempt]


def _safe_archive_url(value: str) -> str:
    parts = urlsplit(value)
    if (
        parts.scheme.lower() != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
    ):
        raise ArchiveDownloadFailure("UNSAFE_URL")
    return value


def _archive_response(
    session: requests.Session,
    url: str,
    throttle: ArchiveHostThrottle,
    *,
    params: dict[str, Any] | None = None,
    stream: bool,
    timeout: int,
    budget: ArchiveAttemptBudget | None = None,
) -> tuple[requests.Response, str]:
    budget = budget or ArchiveAttemptBudget()
    current_url = _safe_archive_url(url)
    current_params = params
    redirects = 0
    last_reason = "NETWORK"
    retry_delay = ARCHIVE_BACKOFF_SECONDS[-1]
    while True:
        attempt = budget.take()
        if attempt is None:
            throttle.defer(current_url, retry_delay)
            raise ArchiveDownloadFailure(last_reason)
        response = None
        retry_delay = ARCHIVE_BACKOFF_SECONDS[min(attempt, 2)]
        try:
            while True:
                throttle.wait(current_url)
                response = session.get(
                    current_url,
                    params=current_params,
                    timeout=timeout,
                    stream=stream,
                    allow_redirects=False,
                    headers={"Accept-Encoding": "identity"},
                )
                status = int(response.status_code)
                if status not in ARCHIVE_REDIRECT_STATUSES:
                    break
                location = str(response.headers.get("Location") or "").strip()
                response.close()
                response = None
                if not location or redirects >= ARCHIVE_MAX_REDIRECTS:
                    raise ArchiveDownloadFailure("REDIRECT_REJECTED")
                current_url = _safe_archive_url(urljoin(current_url, location))
                current_params = None
                redirects += 1
            status = int(response.status_code)
            last_reason = f"HTTP_{status}"
            if 200 <= status < 300:
                return response, current_url
            if status not in ARCHIVE_RETRY_STATUSES:
                raise ArchiveDownloadFailure(last_reason)
            retry_delay = _archive_retry_delay(response, min(attempt, 2))
        except requests.RequestException:
            last_reason = "NETWORK"
        except ArchiveDownloadFailure:
            if response is not None:
                response.close()
            raise
        if response is not None:
            response.close()
        if budget.remaining <= 0:
            throttle.defer(current_url, retry_delay)
            raise ArchiveDownloadFailure(last_reason)
        throttle.sleep_retry(current_url, retry_delay)


def _archive_image_file(
    session: requests.Session,
    url: str,
    directory: Path,
    throttle: ArchiveHostThrottle,
) -> tuple[Path, str]:
    budget = ArchiveAttemptBudget()
    while budget.remaining > 0:
        response, effective_url = _archive_response(
            session,
            url,
            throttle,
            stream=True,
            timeout=60,
            budget=budget,
        )
        temporary = None
        try:
            raw_length = str(response.headers.get("Content-Length") or "").strip()
            if raw_length:
                if not raw_length.isdigit():
                    raise ArchiveDownloadFailure("INVALID_CONTENT_LENGTH")
                if int(raw_length) > ARCHIVE_MAX_IMAGE_BYTES:
                    raise ArchiveDownloadFailure("IMAGE_TOO_LARGE")
            digest = hashlib.sha256()
            size = 0
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=directory,
                prefix=".candidate.",
                suffix=".part",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                try:
                    for chunk in response.iter_content(chunk_size=256 * 1024):
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > ARCHIVE_MAX_IMAGE_BYTES:
                            raise ArchiveDownloadFailure("IMAGE_TOO_LARGE")
                        handle.write(chunk)
                        digest.update(chunk)
                except requests.RequestException as exc:
                    raise ArchiveDownloadFailure("NETWORK") from exc
                handle.flush()
                os.fsync(handle.fileno())
            return temporary, digest.hexdigest()
        except ArchiveDownloadFailure as exc:
            if temporary is not None and temporary.exists():
                temporary.unlink()
            if exc.reason != "NETWORK":
                raise
            retry_delay = ARCHIVE_BACKOFF_SECONDS[min(budget.used - 1, 2)]
            if budget.remaining <= 0:
                throttle.defer(effective_url, retry_delay)
                raise
            throttle.sleep_retry(effective_url, retry_delay)
        except BaseException:
            if temporary is not None and temporary.exists():
                temporary.unlink()
            raise
        finally:
            response.close()
    raise ArchiveDownloadFailure("NETWORK")


def _commons_thumbnail_url(
    session: requests.Session,
    source_url: str,
    throttle: ArchiveHostThrottle,
) -> str | None:
    parts = urlsplit(source_url)
    marker = "/wiki/"
    if parts.hostname not in {"commons.wikimedia.org", "www.commons.wikimedia.org"}:
        return None
    if marker not in parts.path:
        return None
    title = unquote(parts.path.split(marker, 1)[1])
    if not title.startswith("File:"):
        return None
    response = None
    try:
        response, _effective_url = _archive_response(
            session,
            COMMONS_API,
            throttle,
            params={
                "action": "query",
                "format": "json",
                "prop": "imageinfo",
                "iiprop": "url",
                "iiurlwidth": 1600,
                "titles": title,
            },
            stream=False,
            timeout=40,
        )
        payload = response.json()
        pages = ((payload or {}).get("query") or {}).get("pages") or {}
        for page in pages.values():
            info = (page or {}).get("imageinfo") or []
            if info and info[0].get("thumburl"):
                return str(info[0]["thumburl"])
    except (TypeError, ValueError, AttributeError):
        return None
    finally:
        if response is not None:
            response.close()
    return None


def _archive_component(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value or "").strip("._")
    return cleaned[:128] or fallback


def _safe_archive_directory(root: Path, *components: str) -> Path:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if _path_is_link_or_reparse(root) or not root.is_dir():
        raise ArchiveDownloadFailure("UNSAFE_PATH")
    resolved_root = root.resolve()
    current = root
    for component in components:
        current = current / component
        if current.exists():
            if _path_is_link_or_reparse(current) or not current.is_dir():
                raise ArchiveDownloadFailure("UNSAFE_PATH")
        else:
            current.mkdir()
        try:
            current.resolve().relative_to(resolved_root)
        except ValueError as exc:
            raise ArchiveDownloadFailure("UNSAFE_PATH") from exc
    return current


def _validate_archive_image(path: Path) -> str:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                width, height = image.size
                if (
                    width < 1
                    or height < 1
                    or width > ARCHIVE_MAX_IMAGE_DIMENSION
                    or height > ARCHIVE_MAX_IMAGE_DIMENSION
                    or width * height > ARCHIVE_MAX_IMAGE_PIXELS
                ):
                    raise ArchiveDownloadFailure("INVALID_IMAGE")
                image.verify()
            with Image.open(path) as image:
                image.draft(
                    "RGB",
                    (ARCHIVE_HASH_IMAGE_DIMENSION, ARCHIVE_HASH_IMAGE_DIMENSION),
                )
                image.thumbnail(
                    (ARCHIVE_HASH_IMAGE_DIMENSION, ARCHIVE_HASH_IMAGE_DIMENSION),
                    Image.Resampling.LANCZOS,
                )
                return str(imagehash.phash(image))
    except ArchiveDownloadFailure:
        raise
    except Exception as exc:
        raise ArchiveDownloadFailure("INVALID_IMAGE") from exc


def _download_valid_archive_image(
    session: requests.Session,
    url: str,
    directory: Path,
    throttle: ArchiveHostThrottle,
) -> tuple[Path, str, str]:
    temporary, sha256 = _archive_image_file(session, url, directory, throttle)
    try:
        phash = _validate_archive_image(temporary)
        return temporary, sha256, phash
    except BaseException:
        if temporary.exists():
            temporary.unlink()
        raise


def download_one(
    session: requests.Session,
    row: dict[str, str],
    images_dir: Path,
    *,
    archive_mode: bool = False,
    throttle: ArchiveHostThrottle | None = None,
) -> dict[str, str]:
    if Image is None or imagehash is None:
        raise RuntimeError(
            "Image downloading requires Pillow and ImageHash. Run: "
            "py -m pip install -r requirements.txt"
        )
    url = row.get("image_url") or ""
    if not url:
        row["rejection_reason"] = "NO_IMAGE_URL"
        return row
    active_throttle = throttle or ArchiveHostThrottle()
    temporary = None
    try:
        species_dir = _safe_archive_directory(
            images_dir.parent,
            images_dir.name,
            _archive_component(row.get("seafood_code", ""), "unknown-species"),
            _archive_component(
                row.get("source_dataset", "").lower(), "unknown-source"
            ),
        )
        local = species_dir / (
            f"{_archive_component(row.get('image_id', ''), 'candidate')}"
            f"{extension_from_url(url)}"
        )
        if _path_is_link_or_reparse(local):
            raise ArchiveDownloadFailure("UNSAFE_PATH")
        try:
            temporary, sha256, phash = _download_valid_archive_image(
                session, url, species_dir, active_throttle
            )
        except ArchiveDownloadFailure as original_error:
            eligible_fallback = (
                original_error.reason.startswith("HTTP_")
                or original_error.reason
                in {"NETWORK", "IMAGE_TOO_LARGE", "INVALID_IMAGE"}
            )
            if (
                archive_mode
                and eligible_fallback
                and row.get("source_dataset") == "WIKIMEDIA_COMMONS"
            ):
                thumbnail = _commons_thumbnail_url(
                    session, row.get("source_url", ""), active_throttle
                )
                if thumbnail:
                    temporary, sha256, phash = _download_valid_archive_image(
                        session, thumbnail, species_dir, active_throttle
                    )
                else:
                    raise original_error
            else:
                raise
        temporary.replace(local)
        temporary = None
        row["sha256"] = sha256
        row["perceptual_hash"] = phash
        row["local_path"] = str(local.relative_to(images_dir.parent))
        row["rejection_reason"] = ""
    except ArchiveDownloadFailure as exc:
        row["rejection_reason"] = f"DOWNLOAD_ERROR:{exc.reason}"
    except Exception as exc:  # network/decoding errors remain reviewable in the manifest
        row["rejection_reason"] = f"DOWNLOAD_ERROR:{type(exc).__name__}"
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return row


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verified_archive_path(row: dict[str, str], output_dir: Path) -> Path | None:
    relative = row.get("local_path", "").strip()
    expected = row.get("sha256", "").strip().lower()
    if not relative or not re.fullmatch(r"[0-9a-f]{64}", expected):
        return None
    candidate_relative = Path(relative)
    if candidate_relative.is_absolute():
        return None
    root = output_dir.resolve()
    candidate = (root / candidate_relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    try:
        return candidate if _sha256_path(candidate) == expected else None
    except OSError:
        return None


def _merge_archive_progress(
    rows: list[dict[str, str]], previous: list[dict[str, str]]
) -> None:
    by_id = {row.get("image_id", ""): row for row in previous if row.get("image_id")}
    for row in rows:
        old = by_id.get(row.get("image_id", ""))
        if old is None:
            continue
        if any(
            row.get(field_name, "") != old.get(field_name, "")
            for field_name in ARCHIVE_PROGRESS_IDENTITY_FIELDS
        ):
            continue
        for field_name in ARCHIVE_PROGRESS_FIELDS:
            row[field_name] = old.get(field_name, "")


def download_manifest_archive(
    session: requests.Session,
    input_path: Path,
    output_dir: Path,
    *,
    checkpoint_every: int = 10,
) -> ArchiveSummary:
    if checkpoint_every < 1:
        raise ValueError("checkpoint_every must be >= 1")
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    if not input_path.is_file():
        raise SystemExit(f"Candidate manifest not found: {input_path}")
    rows = read_manifest(input_path)
    _safe_archive_directory(output_dir)
    output_manifest = output_dir / "candidates.csv"
    if output_manifest.is_file() and output_manifest.resolve() != input_path.resolve():
        _merge_archive_progress(rows, read_manifest(output_manifest))

    downloaded = skipped = failed = 0
    throttle = ArchiveHostThrottle()
    for index, row in enumerate(rows, start=1):
        if verified_archive_path(row, output_dir) is not None:
            skipped += 1
        else:
            download_one(
                session,
                row,
                output_dir / "images",
                archive_mode=True,
                throttle=throttle,
            )
            if row.get("rejection_reason"):
                failed += 1
            else:
                downloaded += 1
        if index % checkpoint_every == 0:
            write_manifest(rows, output_manifest)
        if index % 25 == 0:
            print(f"archiving {index}/{len(rows)}...", file=sys.stderr)
    write_manifest(rows, output_manifest)
    summary = ArchiveSummary(
        total=len(rows),
        downloaded=downloaded,
        skipped=skipped,
        failed=failed,
    )
    print(
        f"Archive complete: total={summary.total}, downloaded={summary.downloaded}, "
        f"verified-and-skipped={summary.skipped}, failed={summary.failed}. "
        f"Progress manifest: {output_manifest}"
    )
    return summary


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
    p.add_argument(
        "--source",
        dest="sources",
        action="append",
        choices=["all", *ALL_SOURCES],
        help="Source to collect. Repeat for multiple sources. Default: all sources that need no API key.",
    )
    p.add_argument(
        "--smithsonian-api-key",
        default=os.environ.get("SMITHSONIAN_API_KEY"),
        help="Free Smithsonian Open Access API key. Can also be set with SMITHSONIAN_API_KEY.",
    )
    p.add_argument("--species", action="append", help="Seafood code, e.g. FISH_A. Repeat for multiple. Default: all configured species.")
    p.add_argument("--max-per-species", type=int, default=100, help="Maximum candidate rows per species per source (default 100).")
    p.add_argument("--minimum-total-per-species", type=int, help="Collect only the shortfall needed for each species to reach this server candidate total.")
    p.add_argument("--maximum-total-per-species", type=int, help="When collection is needed, stop before the server candidate total would exceed this value.")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument(
        "--download-manifest",
        type=Path,
        help=(
            "Download only the images listed in an existing candidate CSV. "
            "This does not collect metadata or contact the review server."
        ),
    )
    p.add_argument("--download-images", action="store_true", help="Download image bytes after metadata collection. Default is metadata-only.")
    p.add_argument("--resume", action="store_true", help="Merge newly collected rows into an existing output/candidates.csv instead of overwriting it.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json,text/csv,*/*"})
    if args.download_manifest is not None:
        download_manifest_archive(session, args.download_manifest, args.output_dir)
        return 0
    if args.max_per_species < 1:
        raise SystemExit("--max-per-species must be >= 1")
    if args.minimum_total_per_species is not None and args.minimum_total_per_species < 1:
        raise SystemExit("--minimum-total-per-species must be >= 1")
    if args.maximum_total_per_species is not None and args.maximum_total_per_species < 1:
        raise SystemExit("--maximum-total-per-species must be >= 1")
    if (
        args.minimum_total_per_species is not None
        and args.maximum_total_per_species is not None
        and args.maximum_total_per_species < args.minimum_total_per_species
    ):
        raise SystemExit("--maximum-total-per-species must be >= --minimum-total-per-species")
    cfg = load_config(args.config)
    selected = set(args.species or [])
    species_list = [x for x in cfg["species"] if not selected or x["seafood_code"] in selected]
    unknown = selected - {x["seafood_code"] for x in cfg["species"]}
    if unknown:
        raise SystemExit(f"Unknown seafood code(s): {', '.join(sorted(unknown))}")

    collector = Collector(
        session=session,
        smithsonian_api_key=args.smithsonian_api_key,
        delay_seconds=1.05,
        commons_delay_seconds=6.5,
    )
    requested_sources = args.sources or ["all"]
    if "all" in requested_sources:
        sources = [
            source
            for source in ALL_SOURCES
            if source != "smithsonian" or args.smithsonian_api_key
        ]
    else:
        sources = list(dict.fromkeys(requested_sources))

    manifest = args.output_dir / "candidates.csv"
    existing_rows: list[dict[str, str]] = []
    if args.resume:
        existing_rows = read_manifest(manifest)
        if existing_rows:
            print(f"Resume mode: loaded {len(existing_rows)} existing candidate rows from {manifest}", file=sys.stderr)
        else:
            print(f"Resume mode: no existing manifest found at {manifest}; starting from empty", file=sys.stderr)
    existing_source_counts: dict[tuple[str, str], int] = {}
    for row in existing_rows:
        key = (row.get("seafood_code", ""), row.get("source_dataset", ""))
        existing_source_counts[key] = existing_source_counts.get(key, 0) + 1

    rows: list[dict[str, str]] = []
    seen_rows = {
        (row.get("seafood_code", ""), row.get("image_url", "") or row.get("source_record_id", ""))
        for row in existing_rows
    }
    for species in species_list:
        remaining = None
        if args.minimum_total_per_species is not None:
            if species["candidate_count"] >= args.minimum_total_per_species:
                print(
                    f"[{species['seafood_code']}] already has {species['candidate_count']} server candidates; minimum reached, skipping",
                    file=sys.stderr,
                )
                continue
        if args.maximum_total_per_species is not None:
            remaining = max(0, args.maximum_total_per_species - species["candidate_count"])
        elif args.minimum_total_per_species is not None:
            remaining = max(0, args.minimum_total_per_species - species["candidate_count"])
        if remaining == 0:
            print(
                f"[{species['seafood_code']}] already has {species['candidate_count']} server candidates; maximum reached, skipping",
                file=sys.stderr,
            )
            continue
        if remaining is not None:
            print(
                f"[{species['seafood_code']}] server has {species['candidate_count']}; collecting up to {remaining} new candidates",
                file=sys.stderr,
            )
        for source in sources:
            if remaining == 0:
                break
            source_limit = args.max_per_species if remaining is None else min(args.max_per_species, remaining)
            fetch_limit = source_limit + existing_source_counts.get(
                (species["seafood_code"], SOURCE_DATASET_CODES[source]), 0
            )
            print(f"[{species['seafood_code']}] collecting {source} metadata...", file=sys.stderr)
            try:
                if source == "fish-vista":
                    found = collector.collect_fish_vista(species, fetch_limit)
                elif source == "inat":
                    found = collector.collect_inat(species, fetch_limit)
                elif source == "gbif":
                    found = collector.collect_gbif(species, fetch_limit)
                elif source == "commons":
                    found = collector.collect_commons(species, fetch_limit)
                elif source == "ala":
                    found = collector.collect_ala(species, fetch_limit)
                elif source == "obis":
                    found = collector.collect_obis(species, fetch_limit)
                elif source == "noaa":
                    found = collector.collect_noaa(species, fetch_limit)
                elif source == "smithsonian":
                    found = collector.collect_smithsonian(species, fetch_limit)
                else:  # pragma: no cover
                    found = []
                accepted = []
                for row in found:
                    key = (row.get("seafood_code", ""), row.get("image_url", "") or row.get("source_record_id", ""))
                    if key in seen_rows:
                        continue
                    seen_rows.add(key)
                    accepted.append(row)
                    if remaining is not None and len(accepted) >= remaining:
                        break
                rows.extend(accepted)
                if remaining is not None:
                    remaining -= len(accepted)
                print(f"  -> {len(accepted)} new usable licensed candidate rows", file=sys.stderr)
            except requests.RequestException as exc:
                print(
                    f"!! {species['seafood_code']} {source} failed: {exc}; retry later with --resume",
                    file=sys.stderr,
                )
            except (ValueError, AttributeError, TypeError) as exc:
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
