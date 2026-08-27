# SukaSeafood candidate-image collector

[中文](README_ZH.md) | **English**

This local tool collects licensed candidate-image metadata from Fish-Vista,
iNaturalist, GBIF, and Wikimedia Commons for the active fish catalog. It writes
the stable manifest contract to `output/candidates.csv`; candidate records are
not training-approved automatically.

## Configure the active catalog

The collector supports configuration schema version `2` only. The latest
configuration downloaded from the admin page includes each species' current
`candidate_count`. Start from the
tracked example, then replace its fictional entries with the active catalog:

```powershell
Copy-Item .\species_config.example.json .\species_config.json
python .\collect_fish_images.py --config .\species_config.json --source all --max-per-species 100 --minimum-total-per-species 300
python .\collect_fish_images.py --config .\species_config.json --source commons --species FISH_A --resume
```

Every active entry needs a unique `seafood_code`, Chinese and English names, and
an exact `scientific_name`. `inat_taxon_id` and `gbif_taxon_key` may be `null`;
the collector then resolves the exact source taxon from the scientific name.
Use a positive integer override when an automatic lookup needs an explicit
choice. `commons_category` defaults to `Category:<scientific_name>` and
`fish_vista_filter` defaults to the scientific name.

Unknown keys, empty text, duplicate codes, non-positive overrides, and other
schema versions are rejected before collection begins.

## Installation and collection

From this directory on Windows:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
```

Use `--source` to choose `all`, `fish-vista`, `inat`, `gbif`, or `commons`.
With no `--species` arguments, every configured species is collected. Use
`--resume` to merge later collection runs into the existing manifest without
overwriting existing candidate rows. `--minimum-total-per-species 300` skips
species already at 300 server candidates and collects only each remaining
shortfall, stopping once it is filled. A source may not have enough usable
images, and import deduplication can leave the server below the target; import
the CSV, download a fresh configuration, and run replenishment again.
`--download-images` is optional; metadata
collection is the default.

## Source and license policy

The collector keeps only media with `CC0`, Public Domain, `CC BY`, `CC BY-SA`,
`CC BY-NC`, or `CC BY-NC-SA` licenses. It preserves source URLs, attribution,
and source metadata for later verification. Images with `ND`, missing, or
unrecognized licenses are excluded.

iNaturalist queries Research Grade observations after exact taxon resolution.
GBIF keeps licensed still-image media. Fish-Vista uses the configured exact
filter, and Commons reads the configured category. A source failure is reported
for that species and source while the remaining collection continues.

## Review and originals

Review happens in the online system, not through this collector. Training
originals are handled by `local_sync/`; do not use this directory as a local
review workflow. Keep provenance and license fields intact, and do not treat a
candidate row as training-ready until the online review process approves it.

## Tests

```powershell
python -m pytest tests -q
```
