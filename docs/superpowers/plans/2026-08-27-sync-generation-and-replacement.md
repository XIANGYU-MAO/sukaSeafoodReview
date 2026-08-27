# Synchronization Generation and Replacement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make upgraded servers and existing Windows training roots converge monotonically, including a changed original image that keeps the same target path and suffix.

**Architecture:** A new Alembic epoch migration raises every candidate synchronization generation above all values ever exported for that candidate and expires pre-epoch pending batches. The API preserves the prior-path relationship for same-path replacements, while local SQLite schema version 3 stores enough replacement intent to perform and recover a managed atomic swap without allowing arbitrary overwrite.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, Alembic, PostgreSQL 16, SQLite, pytest, Pillow/ImageHash, Windows-safe filesystem operations.

**Spec:** `docs/superpowers/specs/2026-08-27-final-acceptance-remediation-design.md`

## Global Constraints

- Preserve the exact 16-column export CSV and exact seven-field receipt JSON.
- The wire column `review_version` means candidate synchronization generation; do not rename it on the wire.
- PostgreSQL is the online database; SQLite is only the local synchronization sidecar.
- Never overwrite a file unless SQLite proves the candidate owns the exact existing path and SHA-256.
- Every production change must follow a focused failing-test, minimal-fix, passing-test cycle.
- Do not push, merge, deploy, import production data, or mutate YGF in this plan.

---

### Task 1: Upgrade the synchronization generation epoch

**Files:**
- Create: `api/alembic/versions/20260827_07_sync_generation_epoch.py`
- Modify: `api/alembic/versions/20260827_06_export_chunking.py`
- Modify: `api/tests/test_exports.py`
- Test: `api/tests/integration/test_export_postgres_races.py`

**Interfaces:**
- Consumes: `Candidate.version`, `Review.version`, `ReviewRevision.review_version`, `ExportItem.review_version`, and `ExportBatch.status/expired_at`.
- Produces: Alembic revision `20260827_07`, where every candidate version is strictly greater than every historical server-originated synchronization value for that candidate and all older pending batches are expired.

- [ ] **Step 1: Write the failing populated upgrade and downgrade tests**

Create a pre-epoch database at revision `20260827_06`, insert one candidate at version 3, a review/revision at version 5, an export item at version 8, and two pending batches in the same scope. Assert after upgrade that the candidate is version 9 and both pending batches are expired. Separately downgrade from revision 06 with duplicate pending scope rows and assert exactly the oldest remains pending before the unique index is recreated.

```python
assert candidate_version == 9
assert batch_statuses == ["expired", "expired"]
assert pending_after_downgrade == [str(oldest_batch_id)]
assert "uq_export_batches_pending_scope" in index_names
```

- [ ] **Step 2: Run the focused migration tests and verify RED**

Run: `python -m pytest tests/test_exports.py -k "generation_epoch or populated_chunking_downgrade" -q`

Expected: FAIL because revision `20260827_07` does not exist and revision 06 downgrade violates the pending-scope unique index.

- [ ] **Step 3: Implement the epoch migration and deterministic downgrade reconciliation**

Use one correlated maximum per candidate and reject exhaustion of PostgreSQL/SQLite `INTEGER` before incrementing. Expire old pending batches in the same migration transaction.

```python
revision = "20260827_07"
down_revision = "20260827_06"

MAX_DB_INTEGER = 2_147_483_647

def upgrade() -> None:
    bind = op.get_bind()
    maximum = bind.execute(sa.text("""
        SELECT MAX(v) FROM (
          SELECT version AS v FROM candidates
          UNION ALL SELECT version FROM reviews
          UNION ALL SELECT review_version FROM review_revisions
          UNION ALL SELECT review_version FROM export_items
        ) AS historical
    """)).scalar()
    if maximum is not None and int(maximum) >= MAX_DB_INTEGER:
        raise RuntimeError("candidate synchronization generation exhausted")
    op.execute(sa.text("""
        UPDATE candidates AS c
        SET version = 1 + MAX(
          c.version,
          COALESCE((SELECT MAX(r.version) FROM reviews r WHERE r.candidate_id = c.id), 0),
          COALESCE((SELECT MAX(rr.review_version) FROM review_revisions rr WHERE rr.candidate_id = c.id), 0),
          COALESCE((SELECT MAX(ei.review_version) FROM export_items ei WHERE ei.candidate_id = c.id), 0)
        )
    """))
    op.execute(sa.text("""
        UPDATE export_batches
        SET status = 'expired', expired_at = COALESCE(expired_at, CURRENT_TIMESTAMP)
        WHERE status = 'pending'
    """))
```

Use dialect-specific `GREATEST(...)` for PostgreSQL and scalar `MAX(...)` for SQLite. In revision 06 downgrade, rank pending rows by `(created_at, id)` within `scope_key`, expire rows whose rank is greater than one, then create `uq_export_batches_pending_scope`.

- [ ] **Step 4: Run SQLite and real PostgreSQL migration tests and verify GREEN**

Run: `python -m pytest tests/test_exports.py -k "migration or generation_epoch or populated_chunking_downgrade" -q`

Run with disposable PostgreSQL: `python -m pytest tests/integration/test_export_postgres_races.py -q`

Expected: all selected tests PASS; the PostgreSQL run has no skip when `TEST_POSTGRES_DSN` is set.

- [ ] **Step 5: Commit the epoch transition**

```bash
git add api/alembic/versions/20260827_06_export_chunking.py api/alembic/versions/20260827_07_sync_generation_epoch.py api/tests/test_exports.py api/tests/integration/test_export_postgres_races.py
git commit -m "fix(api): migrate synchronization generation epoch"
```

---

### Task 2: Preserve same-path replacement intent in exports

**Files:**
- Modify: `api/app/services/exports.py`
- Modify: `api/tests/test_exports.py`

**Interfaces:**
- Consumes: `Delta.previous_relative_path`, original fingerprint comparison, and successful prior `ExportItem.local_relative_path`.
- Produces: an `ADD` row whose `previous_relative_path` equals `target_relative_path` only when changed original content must replace a managed file in place.

- [ ] **Step 1: Write the failing same-JPG export test**

Create a completed approved JPG ADD, acknowledge its receipt, change only `original_url` and `preview_url` to another approved JPG URL, advance the candidate generation, and create the next batch.

```python
row = parse_export_csv(response.content)[0]
assert row["action"] == "ADD"
assert row["target_relative_path"].endswith(".jpg")
assert row["previous_relative_path"] == row["target_relative_path"]
assert int(row["review_version"]) > old_generation
```

- [ ] **Step 2: Run the focused export test and verify RED**

Run: `python -m pytest tests/test_exports.py -k "same_suffix_original_replacement" -q`

Expected: FAIL because `previous_relative_path` is currently serialized as an empty string.

- [ ] **Step 3: Retain the prior path only for changed original content**

Change the same-path clearing rule in `derive_deltas` to:

```python
if previous_path == desired_path and not original_changed:
    previous_path = None
```

Keep unrelated metadata-only refreshes from claiming replacement semantics.

- [ ] **Step 4: Run export tests and verify GREEN**

Run: `python -m pytest tests/test_exports.py -k "same_suffix_original_replacement or original_url or metadata" -q`

Expected: selected tests PASS, including the existing JPG-to-PNG case.

- [ ] **Step 5: Commit the export contract fix**

```bash
git add api/app/services/exports.py api/tests/test_exports.py
git commit -m "fix(api): preserve managed same-path replacement"
```

---

### Task 3: Add recoverable local replacement intents

**Files:**
- Modify: `local_sync/src/sukaseafood_sync/index.py`
- Modify: `local_sync/src/sukaseafood_sync/operations.py`
- Modify: `local_sync/tests/test_index.py`
- Modify: `local_sync/tests/test_operations.py`
- Create: `local_sync/tests/test_replacement_recovery.py`

**Interfaces:**
- Consumes: Task 2 same-path `previous_relative_path`, `SyncIndex.latest_for_candidate()`, and the existing root interprocess lock.
- Produces: SQLite schema version 3 and `AddIntent` replacement fields: `prior_relative_path: PurePosixPath | None`, `prior_sha256: str | None`, and `backup_relative_path: PurePosixPath | None`.

- [ ] **Step 1: Write failing schema-upgrade and replacement tests**

Create a version-2 SQLite fixture with a completed old ADD, reopen it through `SyncIndex`, and assert schema version 3 preserves the row. Add an operation test where the old managed JPG and new staged JPG differ but the manifest points previous and target to the same path.

```python
assert index.latest_for_candidate(candidate_id).sha256 == old_sha
result = apply_operation(root, replacement_row, new_download, index)
assert result.sha256 == new_sha
assert target.read_bytes() == new_jpg
assert index.latest_for_candidate(candidate_id).review_version == 9
```

Also assert a user-modified target still raises `OperationError("SOURCE_STATE_MISMATCH")` and remains byte-for-byte unchanged.

- [ ] **Step 2: Run focused local tests and verify RED**

Run: `python -m pytest tests/test_index.py tests/test_operations.py tests/test_replacement_recovery.py -k "schema_v3 or same_path_replacement" -q`

Expected: FAIL because schema version 3 and managed same-path replacement do not exist; current code raises `ADD_TARGET_COLLIDES_PREVIOUS`.

- [ ] **Step 3: Implement SQLite schema version 3**

Extend `pending_adds` with nullable replacement evidence and migrate version 2 using a create-copy-verify-rename transaction.

```python
SCHEMA_VERSION = 3

@dataclass(frozen=True, slots=True)
class AddIntent:
    candidate_id: UUID
    review_id: UUID
    review_version: int
    action: Literal["ADD"]
    batch_id: UUID
    target_relative_path: PurePosixPath
    actual_relative_path: PurePosixPath
    sha256: str
    perceptual_hash: str
    prior_relative_path: PurePosixPath | None = None
    prior_sha256: str | None = None
    backup_relative_path: PurePosixPath | None = None
```

Validate that the three replacement fields are either all absent or all present, paths identify the same candidate, backup is inside `_removed/<batch_id>/`, and prior/new SHA values differ.

- [ ] **Step 4: Run index tests and verify GREEN before filesystem code**

Run: `python -m pytest tests/test_index.py -k "schema or intent" -q`

Expected: all selected index tests PASS and the version-2 fixture retains its completed rows.

- [ ] **Step 5: Implement the guarded filesystem swap**

Add a focused helper and call it only when previous and actual paths have the same Windows identity.

```python
def _apply_managed_replacement(
    root: Path,
    row: ManifestRow,
    result: SyncResult,
    staging: Path,
    staging_metadata: os.stat_result,
    index: SyncIndex,
) -> None:
    prior = _latest(index, row)
    if prior is None or not prior.present or prior.relative_path != result.relative_path:
        raise OperationError("SOURCE_STATE_MISMATCH")
    if row.review_version <= prior.review_version:
        raise OperationError("STALE_GENERATION")
    target = _resolved_path(root, result.relative_path)
    target_sha, _, target_owned = _hash_regular(target, "SOURCE_UNSAFE")
    if target_sha != prior.sha256:
        raise OperationError("SOURCE_STATE_MISMATCH")
    backup_relative = PurePosixPath("_removed", str(row.batch_id), target.name)
    backup = _ensure_parents(root, backup_relative)
    intent = _record_replacement_intent(index, result, prior, backup_relative)
    _link_no_clobber(target, target_owned, backup, prior.sha256)
    _unlink_owned(target, target_owned, "FILESYSTEM_OPERATION_FAILED")
    _link_no_clobber(staging, staging_metadata, target, result.sha256)
```

Recovery uses the intent plus hashes to complete exactly one of these states: old target only before swap, old backup plus new target after swap, or restore old target when new staging is unavailable. It records success only after the new target hash matches the intent.

- [ ] **Step 6: Run operation and recovery tests and verify GREEN**

Run: `python -m pytest tests/test_operations.py tests/test_replacement_recovery.py -k "same_path_replacement or replacement_intent" -q`

Expected: all selected tests PASS; unmanaged/modified files are never replaced.

- [ ] **Step 7: Commit the local replacement transaction**

```bash
git add local_sync/src/sukaseafood_sync/index.py local_sync/src/sukaseafood_sync/operations.py local_sync/tests/test_index.py local_sync/tests/test_operations.py local_sync/tests/test_replacement_recovery.py
git commit -m "fix(sync): recover managed same-path replacement"
```

---

### Task 4: Prove cancellation, replay, and concurrent convergence

**Files:**
- Modify: `local_sync/tests/test_engine.py`
- Modify: `local_sync/tests/test_end_to_end.py`
- Create: `local_sync/tests/test_replacement_process_concurrency.py`
- Modify: `local_sync/tests/test_canonical.py`

**Interfaces:**
- Consumes: schema version 3 and managed replacement from Task 3.
- Produces: regression evidence that partial receipts, stale CSV replay, cancellation, and two real processes cannot regress the new file or canonical manifest.

- [ ] **Step 1: Write failing end-to-end interruption tests**

Use an ADD generation 5 fixture, then same-path replacement generation 9. Inject cancellation after backup creation and a crash after new target creation. Reopen the root and rerun generation 9, then replay generation 5.

```python
assert final_file.read_bytes() == new_jpg
assert canonical_row["review_version"] == "9"
assert index.max_generation(candidate_id) == 9
assert old_receipt.items[0].status == "FAILED"
assert old_receipt.items[0].error == "STALE_GENERATION"
```

Start two subprocesses against the same root with generations 9 and 10; assert the final file, SQLite and canonical manifest all describe generation 10.

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_engine.py tests/test_end_to_end.py tests/test_replacement_process_concurrency.py tests/test_canonical.py -k "replacement or pre_epoch or stale_replay" -q`

Expected: at least the injected replacement interruption or replay case FAILS before orchestration recognizes replacement intents.

- [ ] **Step 3: Integrate replacement recovery into engine startup and cancellation flow**

Call replacement recovery under the existing root lock before new work and before emitting an uploadable partial receipt. Preserve the rule that every durable success reaches canonical state before receipt persistence.

```python
with training_root_lock(root):
    recover_pending_operations(root, manifest, index)
    outcome = execute_manifest(manifest, index, cancel_event)
    merge_successes_into_canonical(root, outcome.receipt.items, index)
    save_partial_receipt(outcome.receipt)
```

- [ ] **Step 4: Run focused and full local suites and verify GREEN**

Run: `python -m pytest tests/test_engine.py tests/test_end_to_end.py tests/test_replacement_process_concurrency.py tests/test_canonical.py -q`

Run: `python -m pytest -q`

Expected: focused and full local suites PASS, with only the already documented platform-conditional skip.

- [ ] **Step 5: Commit convergence coverage**

```bash
git add local_sync/src/sukaseafood_sync local_sync/tests/test_engine.py local_sync/tests/test_end_to_end.py local_sync/tests/test_replacement_process_concurrency.py local_sync/tests/test_canonical.py
git commit -m "test(sync): prove replacement convergence"
```

---

### Task 5: Update synchronization terminology and verify the subsystem

**Files:**
- Modify: `README.md`
- Modify: `README_ZH.md`
- Modify: `local_sync/README_ZH.md`
- Modify: `.superpowers/sdd/2026-08-26-local-training-sync/final-fix-report.md`
- Create: `local_sync/tests/test_readme_contract.py`

**Interfaces:**
- Consumes: Tasks 1-4 behavior and exact test output.
- Produces: truthful bilingual documentation that calls the overloaded wire value “candidate synchronization generation”.

- [ ] **Step 1: Write the failing documentation contract assertions**

Add assertions to the existing README contract tests:

```python
assert "candidate synchronization generation" in readme_en
assert "候选图片同步代次" in readme_zh
assert "review generation" not in readme_en.lower()
assert "审核代次" not in local_readme_zh
```

- [ ] **Step 2: Run documentation tests and verify RED**

Run: `python -m pytest api/tests/test_readme_contract.py local_sync/tests/test_readme_contract.py -q`

Expected: FAIL on the old terminology.

- [ ] **Step 3: Update documentation with measured claims only**

Describe revision 07 upgrade behavior, schema version 3 recovery, same-path replacement safety, and the fact that no live deployment has occurred. Replace old “review generation” wording without changing the CSV column name.

- [ ] **Step 4: Run subsystem verification and verify GREEN**

Run: `python -m pytest api/tests/test_exports.py api/tests/test_readme_contract.py -q`

Run: `python -m pytest -q` from `local_sync/`.

Run: `python -m compileall -q api/app api/alembic local_sync/src`

Expected: all tests and compilation PASS.

- [ ] **Step 5: Commit the synchronization documentation**

```bash
git add README.md README_ZH.md local_sync/README_ZH.md .superpowers/sdd/2026-08-26-local-training-sync/final-fix-report.md api/tests/test_readme_contract.py local_sync/tests/test_readme_contract.py
git commit -m "docs: define candidate synchronization generation"
```
