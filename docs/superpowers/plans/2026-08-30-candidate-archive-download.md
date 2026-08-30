# Candidate Archive Download Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only all-candidate CSV export and a resumable local archive downloader without changing training-sync or server download state.

**Architecture:** The collector API serializes existing `Candidate` and `Species` rows into the collector's stable 30-column manifest contract. The collector gains a manifest-only execution branch that validates local hashes, retries only incomplete rows, checkpoints progress, and optionally falls back to a Commons API thumbnail for archive use. The web exposes the export and the exact local command; `local_sync` only improves terminal HTTP diagnostics because its retry engine already satisfies the networking requirements.

**Tech Stack:** FastAPI, SQLAlchemy async, Python csv/requests/Pillow, React/TypeScript/Vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-08-30-candidate-archive-download-design.md`

## Global Constraints

- The export is read-only and must not create an export batch, receipt, audit event, or candidate-state mutation.
- Archive download must never upload a receipt or mark a server candidate downloaded.
- Training `local_sync` remains approved-training-only and never substitutes a thumbnail for an original.
- Work directly in the user-approved existing repository; do not create another worktree.

---

### Task 1: Read-only candidate manifest API

**Files:**
- Modify: `api/app/services/collector.py`
- Modify: `api/app/api/routes/collector.py`
- Modify: `api/tests/test_collector_config.py`

**Interfaces:**
- Produces: `build_candidate_manifest(session: AsyncSession) -> bytes`
- Produces: `GET /v1/admin/collector/candidates.csv`

- [ ] **Step 1: Write the failing API test**

Add a test which requests `/v1/admin/collector/candidates.csv`, parses the response with `csv.DictReader`, and asserts the literal 30-column header, stable row order, inactive-candidate inclusion, candidate UUID `image_id`, `original_url` as `image_url`, species values, `status=CANDIDATE`, no cache, and no new `AuditEvent` or export rows.

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest api/tests/test_collector_config.py::test_admin_downloads_all_candidates_as_read_only_archive_manifest -q`

Expected: FAIL with HTTP 404 because the endpoint does not exist.

- [ ] **Step 3: Implement deterministic CSV serialization**

Use `csv.DictWriter` with the collector manifest's literal field tuple. Query `Candidate` joined to `Species`, order by `Species.sort_order`, `Species.code`, `Candidate.source_dataset`, and `Candidate.id`, and map rows as follows:

```python
{
    "image_id": str(candidate.id),
    "seafood_code": species.code,
    "app_label": species.name_en,
    "scientific_name": species.scientific_name,
    "source_dataset": candidate.source_dataset,
    "source_record_id": candidate.source_record_id,
    "source_url": candidate.source_url,
    "image_url": candidate.original_url,
    "creator": candidate.creator or "",
    "license": candidate.license,
    "license_url": candidate.license_url or "",
    "attribution": candidate.attribution,
    "source_location": candidate.location or "",
    "source_date": candidate.observed_on.isoformat() if candidate.observed_on else "",
    "image_context": "REVIEW",
    "whole_fish": "REVIEW",
    "exact_species_verified": "REVIEW",
    "split": "UNASSIGNED",
    "status": "CANDIDATE",
}
```

Return UTF-8 BOM bytes with CRLF records and `Content-Disposition: attachment; filename="sukaseafood-all-candidates.csv"`.

- [ ] **Step 4: Test authorization and read-only behavior**

Assert admin is 200, anonymous is 401, reviewer is 403, and database audit/export counts remain unchanged after an admin download.

- [ ] **Step 5: Run the API tests**

Run: `python -m pytest api/tests/test_collector_config.py -q`

Expected: PASS.

### Task 2: Manifest-only resumable archive downloader

**Files:**
- Modify: `collector/collect_fish_images.py`
- Modify: `collector/tests/test_collect_fish_images.py`

**Interfaces:**
- Produces: CLI flag `--download-manifest PATH`
- Produces: `download_manifest_archive(session, input_path, output_dir, checkpoint_every=10) -> ArchiveSummary`
- Produces: `verified_archive_path(row, output_dir) -> Path | None`

- [ ] **Step 1: Write failing CLI and resume tests**

Add tests asserting `parse_args(["--download-manifest", "all.csv"])` accepts a path and `main()` takes the manifest-only branch without constructing `Collector` or loading `species_config.json`.

- [ ] **Step 2: Run the focused tests and observe failure**

Run: `python -m pytest collector/tests/test_collect_fish_images.py -k "download_manifest" -q`

Expected: FAIL because the option and branch do not exist.

- [ ] **Step 3: Implement safe resume and checkpointing**

For each row, treat it as complete only when `local_path` resolves inside `output_dir`, the file exists, and its literal SHA-256 equals `sha256`. Retry all other rows. Write `output_dir/candidates.csv` after every 10 processed rows and once at the end. On success set `sha256`, `perceptual_hash`, `local_path`, and clear `rejection_reason`; on failure leave it retryable and store a bounded reason such as `DOWNLOAD_ERROR:HTTP_429`.

- [ ] **Step 4: Test the real observable behavior**

Use a real generated JPEG fixture and a controlled HTTP adapter. Assert a successful first run creates and hashes the image, a second run issues no HTTP call, a corrupted file is redownloaded, an old failure reason is cleared, and checkpoint writes occur at rows 10 and 20 plus final completion.

- [ ] **Step 5: Add bounded retry and Commons archive fallback tests**

Assert 429/503 use bounded `Retry-After`/backoff, terminal errors contain the HTTP status, and a failed `WIKIMEDIA_COMMONS` original can be fulfilled by the official Commons API `thumburl` without changing the row's `image_url`.

- [ ] **Step 6: Run collector tests**

Run: `python -m pytest collector/tests -q`

Expected: PASS.

### Task 3: Admin export UX and operator guidance

**Files:**
- Modify: `web/src/admin/ImportsTab.tsx`
- Modify: `web/src/pages/AdminPage.review.test.tsx`
- Modify: `collector/README_ZH.md`
- Modify: `collector/README.md`
- Rebuild: `web/public/downloads/sukaseafood-collector.zip`

**Interfaces:**
- Consumes: `GET /v1/admin/collector/candidates.csv`
- Consumes: `python collect_fish_images.py --download-manifest PATH --output-dir PATH`

- [ ] **Step 1: Write the failing UI test**

Assert the “准备本地采集器” card contains an authenticated link named “导出全部候选 CSV”, points to `/sukaseafood/api/v1/admin/collector/candidates.csv`, and shows a manifest-only archive command plus text stating that it neither creates a training batch nor writes download state to the server.

- [ ] **Step 2: Run the focused UI test and observe failure**

Run: `npm test -- --run src/pages/AdminPage.review.test.tsx`

Expected: FAIL because the export link and archive guidance are absent.

- [ ] **Step 3: Implement the compact export guidance**

Add the link beside the collector/config downloads and render this Windows example without coupling it to training sync:

```text
python .\collect_fish_images.py --download-manifest .\sukaseafood-all-candidates.csv --output-dir "G:\sukaseafood-candidate-archive"
```

Explain that rerunning validates existing SHA-256 values and retries only missing/failed files.

- [ ] **Step 4: Update bilingual collector documentation**

Document the same command, archive-only semantics, checkpoint/resume behavior, and Commons thumbnail limitation. Keep the training-original paragraph explicit that `local_sync` is separate.

- [ ] **Step 5: Run UI tests and rebuild the ZIP**

Run: `npm test -- --run src/pages/AdminPage.review.test.tsx`

Run: `python collector/build_package.py`

Run: `python collector/build_package.py --check`

Expected: all commands pass.

### Task 4: Local-sync terminal HTTP diagnostics

**Files:**
- Modify: `local_sync/src/sukaseafood_sync/downloader.py`
- Modify: `local_sync/tests/test_downloader.py`

**Interfaces:**
- Preserves: `download_image(...) -> DownloadResult`
- Changes: terminal `DownloadError` messages include the final safe HTTP status and bounded `Retry-After` delay when available.

- [ ] **Step 1: Write failing diagnostic tests**

Assert a terminal 404 error contains `HTTP 404`, a terminal sequence of 429 responses contains `HTTP 429` and `Retry-After 3`, and the existing secret-free error-chain assertions still pass.

- [ ] **Step 2: Run tests and observe the missing detail**

Run: `python -m pytest local_sync/tests/test_downloader.py -k "http_failure or retry_after" -q`

Expected: FAIL because current errors say only generic HTTP failure.

- [ ] **Step 3: Preserve safe final response facts**

Track only integer status and parsed bounded retry delay. Build messages from these values; never include the request URL, response body, headers other than the parsed delay, or exception text.

- [ ] **Step 4: Run local-sync tests**

Run: `python -m pytest local_sync/tests -q`

Expected: PASS.

### Task 5: Full verification, commit, push, and production deployment

**Files:**
- Verify all modified files and generated collector ZIP.

**Interfaces:**
- Produces: deployed API/web and committed collector/local-sync source.

- [ ] **Step 1: Run complete automated verification**

Run API, collector, local-sync, root, and web suites using their repository-native commands; run the production web build and collector ZIP check.

- [ ] **Step 2: Review the final diff**

Confirm no candidate/download database migration exists, no receipt call was added to the collector, no thumbnail fallback exists in `local_sync`, and no unrelated user files changed.

- [ ] **Step 3: Commit and push**

Create a focused commit on the user-approved `main` branch and push it to the configured remote.

- [ ] **Step 4: Deploy and smoke-test**

Run the repository deployment script, then verify production health, admin authentication protection on the new CSV endpoint, web asset availability, and the deployed page response.
