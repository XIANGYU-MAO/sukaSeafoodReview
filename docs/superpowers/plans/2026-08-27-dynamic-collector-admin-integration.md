# Dynamic Collector and Admin Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the useful candidate collector into the main repository, make fish species fully dynamic, expose a Mao-only collector configuration download, guide the local Windows collection flow in the admin UI, and remove the obsolete desktop collector and fixed 1,221-row assumptions.

**Architecture:** PostgreSQL remains the authority for the active species catalog and optional source overrides. The API exports a small JSON configuration; Mao runs the collector locally against external source APIs and uploads the resulting CSV through the existing preview/commit importer. A versioned static ZIP publishes the local collector without routing image traffic through the server.

**Tech Stack:** Python 3.12, requests, pytest, FastAPI, Pydantic v2, SQLAlchemy async, Alembic, PostgreSQL 16/SQLite tests, React 19, TypeScript 7, Vite 8, Vitest, PowerShell 7.

**Spec:** `docs/superpowers/specs/2026-08-27-dynamic-collector-admin-integration-design.md`

## Global Constraints

- The server must never request, proxy, cache, or store external candidate image bytes.
- Collection runs only on Mao's Windows computer, one process at a time.
- Do not add multi-process file races, exact crash timing, malicious file replacement, cross-root mutation, or live schema-upgrade race tests.
- Species are not limited to SF001–SF005; a fresh deployment starts with an empty species table.
- Inactive species remain queryable for history but are excluded from collector configuration.
- Source overrides are optional; scientific-name resolution is the normal path.
- The legacy 1,221-row CSV and all legacy `output/` data are intentionally deleted, not archived.
- Do not delete `C:\Users\86166\Desktop\SukaSeafood_CV_Dataset_Collector` until migrated files are committed and all normal-path verification passes.
- Do not push, merge, deploy, reload Caddy, or import production data as part of this feature plan; return to the existing deployment workflow only after this plan passes.

---

### Task 1: Migrate and generalize the local collector

**Files:**
- Create: `collector/__init__.py`
- Create: `collector/.gitignore`
- Create: `collector/collect_fish_images.py`
- Create: `collector/species_config.example.json`
- Create: `collector/requirements.txt`
- Create: `collector/README_ZH.md`
- Create: `collector/README.md`
- Create: `collector/tests/test_collect_fish_images.py`
- Create: `collector/tests/test_dynamic_config.py`
- Do not copy: legacy `review_candidates.py`, `output/`, `.pytest_cache/`, `__pycache__/`, or `tests/test_review_candidates.py`

**Interfaces:**
- Consumes: configuration JSON schema version `1` described below.
- Produces: `load_config(path: Path) -> dict[str, Any]`, `Collector.resolve_inat_taxon_id(species) -> int`, `Collector.resolve_gbif_key(species) -> int`, `normalize_species_config(raw) -> dict[str, Any]`, and the existing `collector/output/candidates.csv` manifest contract.

- [ ] **Step 1: Copy only the legacy source material into the new tracked directory**

Run from the repository root in PowerShell:

```powershell
$legacyCollector = 'C:\Users\86166\Desktop\SukaSeafood_CV_Dataset_Collector'
New-Item -ItemType Directory -Force -Path collector, collector\tests | Out-Null
Copy-Item -LiteralPath "$legacyCollector\collect_fish_images.py" -Destination collector\collect_fish_images.py
Copy-Item -LiteralPath "$legacyCollector\requirements.txt" -Destination collector\requirements.txt
Copy-Item -LiteralPath "$legacyCollector\README.md" -Destination collector\README.md
Copy-Item -LiteralPath "$legacyCollector\README_ZH.md" -Destination collector\README_ZH.md
Copy-Item -LiteralPath "$legacyCollector\tests\test_collect_fish_images.py" -Destination collector\tests\test_collect_fish_images.py
```

Expected: only the five named files and one test file are copied; the legacy directory is unchanged.

- [ ] **Step 2: Write failing dynamic-configuration tests**

Create `collector/tests/test_dynamic_config.py` with these focused tests:

```python
from pathlib import Path

import pytest

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
```

Extend the existing collector tests to remove `test_species_config_contains_the_five_frozen_species` and add:

```python
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
```

Import `json` and `collector.collect_fish_images as collector_module` at the top. The test uses the fake collector above and makes no live network request.

- [ ] **Step 3: Run the focused tests and verify RED**

Run:

```powershell
python -m pytest collector/tests/test_dynamic_config.py collector/tests/test_collect_fish_images.py -q
```

Expected: FAIL because `collector` is not yet a package, `normalize_species_config` does not exist, iNaturalist still requires a hard-coded taxon ID, and the old fixed-five assertion remains.

- [ ] **Step 4: Implement the versioned dynamic configuration**

Create an empty `collector/__init__.py` and `collector/.gitignore`:

```gitignore
output/
.venv/
__pycache__/
.pytest_cache/
*.py[cod]
species_config.json
```

Replace the raw JSON loader with validation centered on this normalized shape:

```python
CONFIG_SCHEMA_VERSION = 1
INAT_TAXA_API = "https://api.inaturalist.org/v1/taxa"


def normalize_species_config(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise ValueError("collector config schema_version must be 1")
    items = raw.get("species")
    if not isinstance(items, list) or not items:
        raise ValueError("collector config must contain at least one active species")
    normalized = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("each species config entry must be an object")
        code = str(item.get("seafood_code") or "").strip()
        scientific_name = str(item.get("scientific_name") or "").strip()
        name_zh = str(item.get("name_zh") or "").strip()
        name_en = str(item.get("name_en") or "").strip()
        if not code or code in seen or not scientific_name or not name_zh or not name_en:
            raise ValueError("species code and names must be non-empty and unique")
        seen.add(code)
        normalized.append({
            "seafood_code": code,
            "name_zh": name_zh,
            "name_en": name_en,
            "app_label": name_en,
            "scientific_name": scientific_name,
            "inat_taxon_id": item.get("inat_taxon_id"),
            "gbif_taxon_key": item.get("gbif_taxon_key"),
            "commons_category": item.get("commons_category") or f"Category:{scientific_name}",
            "fish_vista_filter": item.get("fish_vista_filter") or scientific_name,
        })
    return {"schema_version": 1, "generated_at": raw.get("generated_at"), "species": normalized}


def load_config(path: Path) -> dict[str, Any]:
    return normalize_species_config(json.loads(path.read_text(encoding="utf-8")))
```

Validate positive integer overrides and bounded non-empty text before returning. Reject unknown top-level/species keys so a misspelled override does not silently do nothing.

- [ ] **Step 5: Implement automatic source resolution and source-specific continuation**

Add exact-name iNaturalist resolution and override-first GBIF behavior:

```python
def resolve_inat_taxon_id(self, species: dict[str, Any]) -> int:
    if species["inat_taxon_id"] is not None:
        return int(species["inat_taxon_id"])
    data = self.get_json(INAT_TAXA_API, {"q": species["scientific_name"], "rank": "species"})
    exact = [row for row in data.get("results", []) if norm_species(row.get("name")) == norm_species(species["scientific_name"])]
    if len(exact) != 1 or not exact[0].get("id"):
        raise ValueError(f"{species['seafood_code']} iNaturalist exact taxon was not resolved; set inat_taxon_id")
    return int(exact[0]["id"])


def resolve_gbif_key(self, species: dict[str, Any]) -> int:
    if species["gbif_taxon_key"] is not None:
        return int(species["gbif_taxon_key"])
    data = self.get_json(GBIF_MATCH_API, {"name": species["scientific_name"], "rank": "SPECIES"})
    if data.get("usageKey") and str(data.get("matchType") or "").upper() != "NONE":
        return int(data["usageKey"])
    raise ValueError(f"{species['seafood_code']} GBIF taxon was not resolved; set gbif_taxon_key")
```

`collect_inat()` must call `resolve_inat_taxon_id()`. Fish-Vista must compare against `fish_vista_filter`; Commons must use the normalized `commons_category`. Keep the existing per-species/per-source `try/except` in `main()` and format failure output as:

```text
!! FISH_A inat failed: FISH_A iNaturalist exact taxon was not resolved; set inat_taxon_id
```

Change CLI help from “Default: all five” to “Default: all configured species.”

- [ ] **Step 6: Add the dynamic example configuration and collector documentation**

Create `collector/species_config.example.json` with two fictional species codes and null overrides. Update both READMEs to describe:

```powershell
Copy-Item .\species_config.example.json .\species_config.json
python .\collect_fish_images.py --config .\species_config.json --source all --max-per-species 100
python .\collect_fish_images.py --config .\species_config.json --source commons --species FISH_A --resume
```

Remove the frozen-five table, local review page, KEEP download, and legacy `output/review/` instructions. State that review happens in the online system and training originals are handled by `local_sync/`.

- [ ] **Step 7: Run collector tests and commit**

Run:

```powershell
python -m pytest collector/tests -q
python -m compileall -q collector
python collector/collect_fish_images.py --help
git diff --check
```

Expected: all collector tests PASS; help says all configured species; compile and diff checks are clean.

Commit:

```powershell
git add collector
git commit -m "feat(collector): support dynamic fish catalogs"
```

---

### Task 2: Persist optional source overrides on species

**Files:**
- Create: `api/alembic/versions/20260827_08_species_collector_overrides.py`
- Modify: `api/app/models/catalog.py`
- Modify: `api/app/schemas/admin.py`
- Modify: `api/app/services/admin.py`
- Modify: `api/tests/test_admin_catalog.py`
- Modify: `api/tests/test_model_constraints.py`

**Interfaces:**
- Consumes: existing `SpeciesCreateRequest`, `SpeciesPatchRequest`, `SpeciesResponse`, and audited species mutations.
- Produces: nullable `inat_taxon_id: int | None`, `gbif_taxon_key: int | None`, `commons_category: str | None`, and `fish_vista_filter: str | None` on the model and admin API.

- [ ] **Step 1: Write failing API contract tests**

Extend `api/tests/test_admin_catalog.py` so creation includes all four overrides, editing clears one override with explicit `null`, and response/audit snapshots include exact values:

```python
created = client.post("/v1/admin/species", headers=headers, json={
    "code": "FISH_A",
    "name_zh": "鱼甲",
    "name_en": "Fish A",
    "scientific_name": "Piscis alpha",
    "inat_taxon_id": 123,
    "gbif_taxon_key": 456,
    "commons_category": "Category:Piscis alpha",
    "fish_vista_filter": "Piscis alpha",
    "reason": "add current class",
})
assert created.json()["inat_taxon_id"] == 123

cleared = client.patch(
    f"/v1/admin/species/{created.json()['id']}",
    headers=headers,
    json={"inat_taxon_id": None, "reason": "return to automatic resolution"},
)
assert cleared.json()["inat_taxon_id"] is None
```

Add validation cases for zero/negative IDs and blank override strings. Base fields must still reject explicit null; override fields must permit explicit null.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
Set-Location api
python -m pytest tests/test_admin_catalog.py tests/test_model_constraints.py -q
Set-Location ..
```

Expected: FAIL because the model and request/response schemas do not contain source overrides.

- [ ] **Step 3: Add the Alembic revision and SQLAlchemy fields**

Create revision `20260827_08` with `down_revision = "20260827_07"`. `upgrade()` adds:

```python
with op.batch_alter_table("species") as batch:
    batch.add_column(sa.Column("inat_taxon_id", sa.BigInteger(), nullable=True))
    batch.add_column(sa.Column("gbif_taxon_key", sa.BigInteger(), nullable=True))
    batch.add_column(sa.Column("commons_category", sa.String(length=512), nullable=True))
    batch.add_column(sa.Column("fish_vista_filter", sa.String(length=255), nullable=True))
    batch.create_check_constraint("ck_species_inat_taxon_positive", "inat_taxon_id IS NULL OR inat_taxon_id > 0")
    batch.create_check_constraint("ck_species_gbif_taxon_positive", "gbif_taxon_key IS NULL OR gbif_taxon_key > 0")
```

`downgrade()` drops both checks and all four columns in reverse order. Mirror the same nullable fields and check constraints on `Species`.

- [ ] **Step 4: Extend Pydantic requests and audited service responses**

Define bounded types:

```python
PositiveTaxonId = Annotated[int, Field(gt=0, le=9_223_372_036_854_775_807)]
CommonsOverride = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]
FilterOverride = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
```

Add nullable override fields to create, patch, response, `species_snapshot()`, `_species_summary()` only where the full admin response is intended, `create_species()`, and `patch_species()`. `SpeciesPatchRequest.require_change()` must reject null only for `name_zh`, `name_en`, `scientific_name`, `active`, and `sort_order`; explicit null is valid for the four overrides.

- [ ] **Step 5: Verify migration and focused behavior**

Run:

```powershell
Set-Location api
python -m pytest tests/test_admin_catalog.py tests/test_model_constraints.py -q
python -m alembic upgrade head
python -m alembic check
python -m compileall -q app tests alembic
Set-Location ..
```

Expected: all selected tests PASS; Alembic reaches `20260827_08`; no model drift is reported.

- [ ] **Step 6: Commit**

```powershell
git add api/alembic/versions/20260827_08_species_collector_overrides.py api/app/models/catalog.py api/app/schemas/admin.py api/app/services/admin.py api/tests/test_admin_catalog.py api/tests/test_model_constraints.py
git commit -m "feat(api): store collector source overrides"
```

---

### Task 3: Export the active species collector configuration

**Files:**
- Create: `api/app/schemas/collector.py`
- Create: `api/app/services/collector.py`
- Create: `api/app/api/routes/collector.py`
- Create: `api/tests/test_collector_config.py`
- Modify: `api/app/main.py`

**Interfaces:**
- Consumes: active `Species` rows and optional overrides from Task 2.
- Produces: authenticated `GET /v1/admin/collector/config`, attachment filename `species_config.json`, JSON schema version `1`, and `409 {"detail":{"code":"NO_ACTIVE_SPECIES"}}` when no species are active.

- [ ] **Step 1: Write failing endpoint tests**

Create `api/tests/test_collector_config.py` with normal-path coverage:

```python
def test_mao_downloads_only_active_species_in_deterministic_order(settings):
    seed = asyncio.run(seed_admin_database(settings, candidate_count=0))
    # Make one seeded species inactive and set overrides on the other.
    response = client.get("/v1/admin/collector/config", headers=admin_headers(seed))
    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="species_config.json"'
    assert response.json() == {
        "schema_version": 1,
        "generated_at": response.json()["generated_at"],
        "species": [{
            "seafood_code": "SF002",
            "name_zh": "其他鱼",
            "name_en": "Other fish",
            "scientific_name": "Piscis alter",
            "inat_taxon_id": 123,
            "gbif_taxon_key": None,
            "commons_category": None,
            "fish_vista_filter": None,
        }],
    }
```

Add tests proving reviewer access returns 403, unauthenticated access returns 401, empty active catalog returns `NO_ACTIVE_SPECIES`, and the top-level JSON contains only `schema_version`, `generated_at`, and `species`.

- [ ] **Step 2: Run endpoint tests and verify RED**

Run:

```powershell
Set-Location api
python -m pytest tests/test_collector_config.py -q
Set-Location ..
```

Expected: FAIL because the route and service do not exist.

- [ ] **Step 3: Implement schemas and deterministic config building**

In `api/app/schemas/collector.py` define:

```python
class CollectorSpecies(BaseModel):
    seafood_code: str
    name_zh: str
    name_en: str
    scientific_name: str
    inat_taxon_id: int | None
    gbif_taxon_key: int | None
    commons_category: str | None
    fish_vista_filter: str | None


class CollectorConfig(BaseModel):
    schema_version: Literal[1] = 1
    generated_at: datetime
    species: list[CollectorSpecies]
```

In `api/app/services/collector.py`, implement:

```python
class NoActiveSpecies(Exception):
    pass


async def build_collector_config(session: AsyncSession) -> CollectorConfig:
    rows = list((await session.scalars(
        select(Species).where(Species.active.is_(True)).order_by(Species.sort_order, Species.code, Species.id)
    )).all())
    if not rows:
        raise NoActiveSpecies
    return CollectorConfig(
        generated_at=datetime.now(timezone.utc),
        species=[CollectorSpecies(
            seafood_code=row.code,
            name_zh=row.name_zh,
            name_en=row.name_en,
            scientific_name=row.scientific_name,
            inat_taxon_id=row.inat_taxon_id,
            gbif_taxon_key=row.gbif_taxon_key,
            commons_category=row.commons_category,
            fish_vista_filter=row.fish_vista_filter,
        ) for row in rows],
    )
```

- [ ] **Step 4: Implement the Mao-only attachment route**

Create a router with prefix `/admin/collector` and register it in `api/app/main.py`:

```python
@router.get("/config")
async def download_collector_config(
    _: CurrentAuth = Depends(require_admin_access),
    db: AsyncSession = Depends(get_db),
) -> Response:
    try:
        config = await build_collector_config(db)
    except NoActiveSpecies as exc:
        raise HTTPException(status_code=409, detail={"code": "NO_ACTIVE_SPECIES"}) from exc
    content = config.model_dump_json(indent=2).encode("utf-8") + b"\n"
    return Response(
        content=content,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="species_config.json"', "Cache-Control": "no-store"},
    )
```

Do not add CSRF to this read-only GET. Do not access any external URL in this service or route.

- [ ] **Step 5: Verify API behavior and commit**

Run:

```powershell
Set-Location api
python -m pytest tests/test_collector_config.py tests/test_admin_permissions.py tests/test_openapi_contract.py -q
python -m compileall -q app tests
Set-Location ..
git diff --check
```

Expected: all selected tests PASS; reviewer/anonymous boundaries remain intact.

Commit:

```powershell
git add api/app/schemas/collector.py api/app/services/collector.py api/app/api/routes/collector.py api/app/main.py api/tests/test_collector_config.py
git commit -m "feat(api): export active collector configuration"
```

---

### Task 4: Build and publish the collector ZIP

**Files:**
- Create: `collector/build_package.py`
- Create: `collector/tests/test_package.py`
- Create: `web/public/downloads/sukaseafood-collector.zip` (generated and tracked)
- Modify: `tests/test_compose_config.py`

**Interfaces:**
- Consumes: tracked collector runtime files from Task 1.
- Produces: `python collector/build_package.py`, `python collector/build_package.py --check`, and a static ZIP containing exactly the five public runtime files.

- [ ] **Step 1: Write failing package-content tests**

Create `collector/tests/test_package.py`:

```python
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
ZIP = ROOT / "web/public/downloads/sukaseafood-collector.zip"
EXPECTED = {
    "sukaseafood-collector/collect_fish_images.py",
    "sukaseafood-collector/species_config.example.json",
    "sukaseafood-collector/requirements.txt",
    "sukaseafood-collector/README_ZH.md",
    "sukaseafood-collector/README.md",
}


def test_release_zip_contains_only_public_runtime_files():
    with ZipFile(ZIP) as archive:
        assert set(archive.namelist()) == EXPECTED
        assert not any("output" in name or "cache" in name or ".env" in name for name in archive.namelist())
```

Add a byte-comparison assertion for each ZIP member against the current tracked source so a stale ZIP fails.

- [ ] **Step 2: Run the package test and verify RED**

Run:

```powershell
python -m pytest collector/tests/test_package.py -q
```

Expected: FAIL because the build script and ZIP do not exist.

- [ ] **Step 3: Implement the allowlisted package builder**

`collector/build_package.py` must use a fixed tuple:

```python
PUBLIC_FILES = (
    "collect_fish_images.py",
    "species_config.example.json",
    "requirements.txt",
    "README_ZH.md",
    "README.md",
)
OUTPUT = ROOT.parent / "web" / "public" / "downloads" / "sukaseafood-collector.zip"
```

The default command creates the parent directory and writes only these files below `sukaseafood-collector/`. `--check` opens the existing ZIP, verifies exact member names, and verifies each member byte-for-byte without rewriting it. It exits nonzero with a clear message if stale.

- [ ] **Step 4: Generate the ZIP and verify the web image will include it**

Run:

```powershell
python collector/build_package.py
python collector/build_package.py --check
python -m pytest collector/tests/test_package.py tests/test_compose_config.py -q
```

Extend `tests/test_compose_config.py` to assert the web build context remains `./web` and that `web/public/downloads/sukaseafood-collector.zip` exists. No Docker context widening is required because the generated ZIP lives inside `web/` before the image build.

- [ ] **Step 5: Commit**

```powershell
git add collector/build_package.py collector/tests/test_package.py web/public/downloads/sukaseafood-collector.zip tests/test_compose_config.py
git commit -m "build(web): publish the Windows collector bundle"
```

---

### Task 5: Add advanced species overrides and the four-step admin workflow

**Files:**
- Modify: `web/src/admin/types.ts`
- Modify: `web/src/admin/types.review.test.ts`
- Modify: `web/src/admin/common.tsx`
- Modify: `web/src/admin/SpeciesTab.tsx`
- Modify: `web/src/admin/ImportsTab.tsx`
- Modify: `web/src/pages/AdminPage.tsx`
- Modify: `web/src/pages/AdminPage.test.tsx`
- Modify: `web/src/pages/AdminPage.review.test.tsx`
- Modify: `web/src/styles/global.css`
- Modify: `web/src/deployment.test.ts`

**Interfaces:**
- Consumes: the four optional API fields, `GET /admin/collector/config`, Vite `WEB_BASE`, and the static ZIP from Task 4.
- Produces: seven-tab admin navigation with label `采集与导入`, optional advanced override editing, four ordered collection/import steps, `openSpecies(): void`, config and ZIP downloads, and a copyable PowerShell command.

- [ ] **Step 1: Write failing UI and parser tests**

Update species fixtures and strict parsers to expect all four nullable override keys. Add UI assertions:

```tsx
expect(screen.getAllByRole("tab")).toHaveLength(7);
expect(screen.getByRole("tab", { name: "采集与导入" })).toBeInTheDocument();
await user.click(screen.getByRole("tab", { name: "采集与导入" }));
for (const heading of ["1. 管理鱼种", "2. 准备本地采集器", "3. 本地生成 CSV", "4. 预检查并导入"]) {
  expect(screen.getByRole("heading", { name: heading })).toBeInTheDocument();
}
expect(screen.getByRole("link", { name: "下载采集器 ZIP" })).toHaveAttribute(
  "href", "/sukaseafood/review/downloads/sukaseafood-collector.zip",
);
expect(screen.getByRole("link", { name: "下载最新鱼种配置" })).toHaveAttribute(
  "href", "/sukaseafood/api/v1/admin/collector/config",
);
```

Add an empty-directory case: config download is disabled and the page says `请先在鱼种管理中新增并启用鱼种。`. Add a SpeciesTab test that opens `高级来源配置`, saves override values, then clears one value and sends JSON null.

- [ ] **Step 2: Run focused web tests and verify RED**

Run:

```powershell
Set-Location web
npm test -- --run src/pages/AdminPage.test.tsx src/pages/AdminPage.review.test.tsx src/admin/types.review.test.ts src/deployment.test.ts
Set-Location ..
```

Expected: FAIL because the tab label, four-step content, override fields, download links, and callback do not exist.

- [ ] **Step 3: Extend strict frontend species types**

Add to `AdminSpecies`:

```ts
inat_taxon_id: number | null;
gbif_taxon_key: number | null;
commons_category: string | null;
fish_vista_filter: string | null;
```

Update `parseSpeciesFull()` and `parseSpeciesReceipt()` to require these exact keys, bound IDs to positive safe integers, and bound optional text to API limits. Preserve strict response validation.

- [ ] **Step 4: Add the collapsible advanced override editor**

Extend `Draft` and `EMPTY` in `SpeciesTab.tsx`. Render:

```tsx
<details className="admin-card-subsection">
  <summary>高级来源配置（通常不需要填写）</summary>
  <div className="admin-form-grid">
    <label>iNaturalist taxon ID<input inputMode="numeric" value={draft.inat_taxon_id} onChange={(event) => setDraft({ ...draft, inat_taxon_id: event.target.value })} /></label>
    <label>GBIF taxon key<input inputMode="numeric" value={draft.gbif_taxon_key} onChange={(event) => setDraft({ ...draft, gbif_taxon_key: event.target.value })} /></label>
    <label>Commons 分类<input value={draft.commons_category} onChange={(event) => setDraft({ ...draft, commons_category: event.target.value })} /></label>
    <label>Fish-Vista 过滤名称<input value={draft.fish_vista_filter} onChange={(event) => setDraft({ ...draft, fish_vista_filter: event.target.value })} /></label>
  </div>
</details>
```

Create sends integers or null. Edit sends only changed fields; clearing a previously populated override sends `null`. Keep base fields and the reason requirement unchanged.

- [ ] **Step 5: Turn the import tab into the four-step workflow**

Add `openSpecies: () => void` to `AdminTabProps` and pass `() => select(2)` from `AdminPage`. Rename only the displayed tab label to `采集与导入`; retain component/file name `ImportsTab` to avoid unrelated refactoring.

At the top of `ImportsTab`, render the four numbered sections. Use:

```ts
const packageUrl = `${WEB_BASE}downloads/sukaseafood-collector.zip`;
const configUrl = `${API_BASE}/admin/collector/config`;
const command = "python .\\collect_fish_images.py --config .\\species_config.json --source all --max-per-species 100";
```

The ZIP link always downloads. The config link renders only when `props.species.length > 0`; otherwise render a disabled button and the empty-catalog instruction. The “前往鱼种管理” button calls `props.openSpecies`. The copy button calls `navigator.clipboard.writeText(command)` and reports `命令已复制。`; clipboard failure reports `复制失败，请手动选择命令。`. Keep the existing preview/commit UI intact inside step 4.

- [ ] **Step 6: Verify focused UI behavior**

Run:

```powershell
Set-Location web
npm test -- --run src/pages/AdminPage.test.tsx src/pages/AdminPage.review.test.tsx src/admin/types.review.test.ts src/deployment.test.ts
npm run typecheck
Set-Location ..
```

Expected: selected tests PASS; TypeScript reports no errors; the admin still has exactly seven keyboard-accessible tabs.

- [ ] **Step 7: Commit**

```powershell
git add web/src/admin/types.ts web/src/admin/types.review.test.ts web/src/admin/common.tsx web/src/admin/SpeciesTab.tsx web/src/admin/ImportsTab.tsx web/src/pages/AdminPage.tsx web/src/pages/AdminPage.test.tsx web/src/pages/AdminPage.review.test.tsx web/src/styles/global.css web/src/deployment.test.ts
git commit -m "feat(web): guide dynamic candidate collection"
```

---

### Task 6: Remove fixed-five initialization and fixed-row deployment assumptions

**Files:**
- Delete: `api/app/commands/seed_species.py`
- Delete: `api/tests/test_seed_species.py`
- Modify: `deploy/scripts/import_candidates_from_windows.ps1`
- Modify: `tests/test_import_deploy.py`
- Modify: `tests/test_first_deploy.py`
- Modify: `deploy/OPERATIONS_ZH.md`
- Modify: `deploy/RELEASE_CHECKLIST_ZH.md`
- Modify: `README_ZH.md`
- Modify: `README.md`
- Modify: `api/tests/test_readme_contract.py`
- Modify: `docs/superpowers/plans/README.md`

**Interfaces:**
- Consumes: fresh Alembic database with an empty `species` table, Mao-created species, and arbitrary future collector CSV row counts.
- Produces: deployment/import instructions and scripts with no old desktop path, no seed_species command, and no 1,221-row success invariant.

- [ ] **Step 1: Write failing deployment and documentation contract tests**

Change `tests/test_import_deploy.py` to require:

```python
assert "sukaSeafoodReview\\collector\\output\\candidates.csv" in script
assert "$Report.total -ne 1221" not in script
assert '"1221|1221"' not in script
assert "$Report.blocking_errors -ne 0" in script
assert "--commit" in script
```

Change `tests/test_first_deploy.py` to assert `seed_users --print-once` remains and `seed_species` is absent. Change README contract tests to require the empty-catalog/admin-create workflow and forbid commands invoking `app.commands.seed_species`.

- [ ] **Step 2: Run focused contract tests and verify RED**

Run:

```powershell
python -m pytest tests/test_import_deploy.py tests/test_first_deploy.py api/tests/test_readme_contract.py -q
```

Expected: FAIL because the current script and documentation still require the legacy path and exact 1,221 counts.

- [ ] **Step 3: Remove the default species command**

Delete `api/app/commands/seed_species.py` and `api/tests/test_seed_species.py`. Do not change user seeding. Confirm no deploy script invokes the removed module:

```powershell
rg -n "app\.commands\.seed_species|DEFAULT_SPECIES" api deploy docker-compose.yml docker-compose.production.yml README.md README_ZH.md
```

Expected after edits: no matches.

- [ ] **Step 4: Make the Windows import helper count-agnostic**

Set the default path to:

```powershell
[string]$CandidateCsv = "C:\Users\86166\Desktop\sukaSeafoodReview\collector\output\candidates.csv"
```

Keep file validation, hashing, upload, dry-run, `blocking_errors == 0`, `can_commit`, and explicit `-Commit`. Remove the exact 1,221 total check, exact database count query, and fixed four-source sample query. After commit, download the commit report, verify its `file_sha256` equals `$Sha256`, print `total`, `inserted`, `skipped_exact`, and `possible_url_duplicates`, and report success without assuming a row count or source set.

- [ ] **Step 5: Update current operational truth**

Update both root READMEs, `deploy/OPERATIONS_ZH.md`, `deploy/RELEASE_CHECKLIST_ZH.md`, and `docs/superpowers/plans/README.md` to say:

- fresh deployment creates six accounts but no fish species;
- Mao creates current species in the admin;
- “采集与导入” downloads the ZIP/config and accepts the newly generated CSV;
- the collector lives at `collector/`;
- no fixed row count or fixed source set is expected;
- legacy 1,221-row import instructions are retired;
- `local_sync/` remains the separate approved-original downloader.

Historical dated specs/plans remain historical records and are not rewritten; the new approved spec is linked as the current collector authority.

- [ ] **Step 6: Run contract tests and commit**

Run:

```powershell
python -m pytest tests/test_import_deploy.py tests/test_first_deploy.py api/tests/test_readme_contract.py -q
python -m compileall -q api/app api/tests
git diff --check
```

Expected: selected tests PASS; current docs contain no executable seed_species instruction or fixed legacy import command.

Commit:

```powershell
git add -A api/app/commands/seed_species.py api/tests/test_seed_species.py deploy/scripts/import_candidates_from_windows.ps1 tests/test_import_deploy.py tests/test_first_deploy.py deploy/OPERATIONS_ZH.md deploy/RELEASE_CHECKLIST_ZH.md README_ZH.md README.md api/tests/test_readme_contract.py docs/superpowers/plans/README.md
git commit -m "docs(deploy): retire the fixed five-species import"
```

---

### Task 7: Prove the normal end-to-end contract and remove the legacy desktop directory

**Files:**
- Create: `api/tests/fixtures/collector_dynamic_candidates.csv`
- Create: `api/tests/test_collector_import_contract.py`
- Modify: `collector/tests/test_collect_fish_images.py` only if the generated fixture reveals a real CSV contract mismatch
- Regenerate: `web/public/downloads/sukaseafood-collector.zip` if Task 7 changes a packaged file
- Delete outside Git after verification: `C:\Users\86166\Desktop\SukaSeafood_CV_Dataset_Collector`

**Interfaces:**
- Consumes: collector manifest columns, dynamic fish codes already created in the test database, API preview/commit importer, and the built ZIP.
- Produces: one normal-path end-to-end acceptance test and verified removal of the explicitly authorized legacy directory.

- [ ] **Step 1: Write the failing collector-to-importer contract test**

Create a two-row fixture using the collector's `write_manifest()` with codes `FISH_A` and `FISH_B`, two supported sources, permitted licenses, public HTTPS source/image URLs, and blank review/local fields. Add `api/tests/test_collector_import_contract.py`:

```python
def test_dynamic_collector_csv_previews_and_commits_for_admin_created_species(settings):
    seed = asyncio.run(seed_admin_database(settings, candidate_count=0))
    content = FIXTURE.read_bytes()

    async def exercise():
        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as db:
                old_species = list((await db.scalars(select(Species))).all())
                for item in old_species:
                    await db.delete(item)
                db.add_all([
                    Species(code="FISH_A", name_zh="鱼甲", name_en="Fish A", scientific_name="Piscis alpha"),
                    Species(code="FISH_B", name_zh="鱼乙", name_en="Fish B", scientific_name="Piscis beta"),
                ])
                await db.commit()
                preview = await stage_candidate_csv(
                    db,
                    content,
                    actor_id=seed.user_ids["Mao"],
                    actor_session_id=seed.session_ids["Mao"],
                    filename="collector_dynamic_candidates.csv",
                )
                assert preview.preview_token is not None
                result = await commit_candidate_csv(
                    db,
                    preview.preview_token,
                    seed.user_ids["Mao"],
                    actor_session_id=seed.session_ids["Mao"],
                )
                return preview, result
        finally:
            await engine.dispose()

    preview, result = asyncio.run(exercise())
    assert preview.total == 2
    assert preview.new_rows == 2
    assert preview.blocking_errors == 0
    assert result.inserted == 2
```

Import `select`, `async_sessionmaker`, `create_async_engine`, `Species`, `commit_candidate_csv`, `stage_candidate_csv`, and `seed_admin_database` explicitly. The test makes no live external HTTP requests.

- [ ] **Step 2: Run the contract test and verify its initial result**

Run:

```powershell
Set-Location api
python -m pytest tests/test_collector_import_contract.py -q
Set-Location ..
```

Expected: initially FAIL if the migrated collector emits a field/value not accepted by the importer. If it passes immediately, record it as characterization evidence and do not invent a failure.

- [ ] **Step 3: Apply only a real manifest compatibility fix if the test found one**

Allowed corrections are limited to the collector's existing manifest mapping: supported uppercase source names, required URL fields, license normalization, UTF-8 BOM CSV output, and safe bounded text. Do not weaken the API import validator. Re-run the test until PASS.

- [ ] **Step 4: Run the complete normal verification matrix**

Run from the repository root:

```powershell
python -m pytest collector/tests -q
python -m compileall -q collector
python collector/build_package.py --check

Set-Location api
python -m pytest -q
python -m compileall -q app tests alembic
python -m alembic upgrade head
python -m alembic check
Set-Location ..

Set-Location web
npm test
npm run typecheck
npm run build
Set-Location ..

python -m pytest tests -q
git diff --check
git status --short
```

Expected: all suites PASS except previously documented platform skips unrelated to this feature; package check, TypeScript, Vite build, compile, Alembic, and diff checks pass. Do not add new extreme concurrency or crash tests to make this matrix larger.

- [ ] **Step 5: Regenerate, verify, and commit any final compatibility artifact**

If a packaged runtime file changed:

```powershell
python collector/build_package.py
python collector/build_package.py --check
```

Commit only the fixture/test and any real compatibility correction:

```powershell
git add api/tests/fixtures/collector_dynamic_candidates.csv api/tests/test_collector_import_contract.py collector/collect_fish_images.py collector/tests/test_collect_fish_images.py web/public/downloads/sukaseafood-collector.zip
git commit -m "test: verify dynamic collector import flow"
```

If no runtime correction was needed, omit unchanged paths from `git add`.

- [ ] **Step 6: Verify the exact destructive target before deletion**

Run in one PowerShell process:

```powershell
$expectedLegacy = [IO.Path]::GetFullPath('C:\Users\86166\Desktop\SukaSeafood_CV_Dataset_Collector')
$legacyItem = Get-Item -LiteralPath $expectedLegacy -Force
if ($legacyItem.FullName -ne $expectedLegacy -or -not $legacyItem.PSIsContainer) {
    throw "Legacy collector target did not resolve to the explicitly authorized directory"
}
if (($legacyItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "Legacy collector target is unexpectedly a reparse point; stop for inspection"
}
foreach ($required in @(
    'collector\collect_fish_images.py',
    'collector\species_config.example.json',
    'collector\requirements.txt',
    'collector\README_ZH.md',
    'collector\README.md',
    'web\public\downloads\sukaseafood-collector.zip'
)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Migrated artifact missing: $required"
    }
}
git status --short
python collector/build_package.py --check
```

Expected: the exact authorized directory is a real directory, every migrated artifact exists, package check passes, and the Git worktree is clean.

- [ ] **Step 7: Delete only the authorized legacy directory and confirm removal**

Continue in the same PowerShell process after Step 6 succeeds:

```powershell
Remove-Item -LiteralPath $expectedLegacy -Recurse -Force
if (Test-Path -LiteralPath $expectedLegacy) {
    throw "Legacy collector directory still exists"
}
Write-Output "Removed legacy collector directory: $expectedLegacy"
```

Expected: the old collector, its 1,221-row CSV, cached thumbnails, and output files are removed. Source and documentation remain recoverable from the committed main repository; the deleted legacy output is intentionally not recoverable from this feature branch.

- [ ] **Step 8: Record the handoff to deployment**

Update the implementation report/plan progress with exact test counts, commit IDs, ZIP check, and deletion confirmation. Mark this feature complete only when no Critical/Important normal-path issue remains. Then resume the existing production runtime/gateway/deployment plan; do not treat this local feature completion as proof that `https://findai.top/sukaseafood/review` is already online.
