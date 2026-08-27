# Import Origin Approval and Deduplication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add audited per-host approval, grouped import issues, explicit valid-row commit, URL deduplication, and matching dynamic local-sync origin validation.

**Architecture:** PostgreSQL stores exact host approvals. Import and export services combine built-in origins with database approvals. Preview issues distinguish intrinsic URL danger from an unapproved public host and expose exact groups. Export rows carry exact approved hosts so the local tool validates new approved sources without a software release.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy async ORM, Alembic, PostgreSQL/SQLite tests, React/TypeScript, pytest, Vitest, Python local-sync package.

**Spec:** `docs/superpowers/specs/2026-08-27-import-origin-approval-and-collector-guidance-design.md`

## Global Constraints

- Only the fixed Mao admin account may approve image origins or commit imports.
- Admin approvals accept exact lowercase ASCII hostnames only; no wildcard or suffix entries.
- Intrinsically unsafe URLs remain non-approvable and blocking.
- Servers never fetch image bytes.
- Import and export operations remain transactional and audit every mutation.
- Frontend never receives staged CSV bytes or stores preview tokens outside memory.
- Do not add compatibility branches for older undeployed frontend/API contracts.

---

### Task 1: Origin approval persistence and effective policy

**Files:**
- Create: `api/app/models/origins.py`
- Create: `api/app/services/origins.py`
- Create: `api/alembic/versions/20260827_08_image_origin_approvals.py`
- Modify: `api/app/models/__init__.py`
- Modify: `api/app/image_origins.py`
- Modify: `local_sync/src/sukaseafood_sync/image_origins.py`
- Test: `api/tests/test_image_origins.py`
- Test: `api/tests/test_migrations.py`

**Interfaces:**
- Produces: `ImageOriginApproval(hostname, approved_by_id, created_at)`.
- Produces: `effective_image_origin_allowlist(session, configured) -> tuple[str, ...]`.
- Produces: `normalize_exact_image_hostname(value: str) -> str`.

- [ ] **Step 1: Write failing persistence and policy tests**

```python
def test_exact_origin_approval_is_persisted_and_combined(settings):
    approval = ImageOriginApproval(hostname="data.example.org", approved_by_id=mao_id)
    assert effective == (*settings.IMAGE_ORIGIN_ALLOWLIST, "data.example.org")

def test_exact_origin_rejects_suffix_wildcard_and_literal_ip():
    for value in (".example.org", "*.example.org", "127.0.0.1"):
        with pytest.raises(ImageOriginError):
            normalize_exact_image_hostname(value)
```

- [ ] **Step 2: Run the focused tests and verify model/import failures**

Run: `python -m pytest api/tests/test_image_origins.py api/tests/test_migrations.py -q`

Expected: FAIL because `ImageOriginApproval`, revision `20260827_08`, and effective policy do not exist.

- [ ] **Step 3: Implement model, migration and exact-host helpers**

```python
class ImageOriginApproval(Base):
    __tablename__ = "image_origin_approvals"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    hostname: Mapped[str] = mapped_column(String(253), unique=True, nullable=False)
    approved_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

Add exact `data.nhm.ac.uk` to both built-in defaults. Keep hostname syntax helpers in `app/image_origins.py`; implement the async effective policy in `app/services/origins.py` with a sorted select of stored hostnames and `normalize_image_origin_allowlist`.

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest api/tests/test_image_origins.py api/tests/test_migrations.py -q`

Expected: PASS.

### Task 2: Structured unapproved-host and grouped issue reporting

**Files:**
- Modify: `api/app/schemas/imports.py`
- Modify: `api/app/services/imports.py`
- Modify: `api/app/api/routes/imports.py`
- Test: `api/tests/test_imports.py`

**Interfaces:**
- Produces: `ImportIssue.host: str | None`.
- Produces: `ImportIssueGroup(code, message, blocking, host, count, sample_rows, omitted_rows)`.
- Produces: `ImportPreview.issue_groups`.

- [ ] **Step 1: Write failing URL classification and grouping tests**

```python
def test_public_https_unknown_host_is_approvable_group_not_unsafe():
    report = preview_candidate_csv(csv_bytes([
        valid_row(image_url="https://data.newmuseum.org/media/a"),
        valid_row(source_record_id="two", image_url="https://data.newmuseum.org/media/b"),
    ]))
    group = report.issue_groups[0]
    assert (group.code, group.host, group.count) == (
        "UNAPPROVED_IMAGE_HOST", "data.newmuseum.org", 2
    )

def test_http_and_local_urls_remain_unsafe_without_approvable_host():
    assert all(issue.host is None for issue in report.issues)
```

- [ ] **Step 2: Run focused RED tests**

Run: `python -m pytest api/tests/test_imports.py -k "unapproved_host or issue_group" -q`

Expected: FAIL because unknown hosts are currently reported as `UNSAFE_URL` and no groups exist.

- [ ] **Step 3: Implement grouped issue accumulation**

Extend `RowProblem` and `_bounded_issue` with `host`. Aggregate by `(code, blocking, host, message)` independently of the 100-row detail bound. In `_normalize_candidate`, keep `_normalize_url` as the intrinsic safety gate and translate only `ImageOriginError("image origin is not approved")` into `UNAPPROVED_IMAGE_HOST` with `urlsplit(original_url).hostname`.

- [ ] **Step 4: Load the effective database policy during preview**

Inside `_db_preview`, call:

```python
allowlist = await effective_image_origin_allowlist(session, image_origin_allowlist)
report = _parse_candidate_csv(content, image_origin_allowlist=allowlist)
```

- [ ] **Step 5: Run focused tests**

Run: `python -m pytest api/tests/test_imports.py -k "unsafe or unapproved or issue_group" -q`

Expected: PASS.

### Task 3: Preview-bound Mao origin approval endpoint

**Files:**
- Modify: `api/app/schemas/imports.py`
- Modify: `api/app/services/imports.py`
- Modify: `api/app/api/routes/imports.py`
- Test: `api/tests/test_imports.py`

**Interfaces:**
- Consumes: `ImageOriginApproval`, `normalize_exact_image_hostname`.
- Produces: `POST /v1/admin/imports/approve-origin` with `ImportOriginApprovalRequest(preview_token, hostname)`.
- Produces: `ImportOriginApprovalReceipt(hostname, created)`.

- [ ] **Step 1: Write failing API tests**

```python
def test_mao_approves_host_from_own_unexpired_preview_and_audits(client):
    preview = stage_unknown_host(client)
    response = client.post(
        "/v1/admin/imports/approve-origin",
        json={"preview_token": preview["preview_token"], "hostname": "data.newmuseum.org"},
        headers=admin_headers(seed, csrf=True),
    )
    assert response.json() == {"hostname": "data.newmuseum.org", "created": True}

def test_approval_rejects_unobserved_host_other_session_and_intrinsic_unsafe_url():
    assert response.status_code in {403, 409, 422}
```

- [ ] **Step 2: Run endpoint tests and verify 404/failures**

Run: `python -m pytest api/tests/test_imports.py -k "approve_origin" -q`

Expected: FAIL because the route does not exist.

- [ ] **Step 3: Implement preview-bound approval transaction**

Lock the staged preview by token digest, actor and actor session. Require unexpired/uncommitted content and a matching `UNAPPROVED_IMAGE_HOST` group. Insert only if absent. Write `AuditEvent(action="IMAGE_ORIGIN_APPROVED", object_type="ImageOrigin", object_id=hostname, reason="Approved from candidate import preview")`.

- [ ] **Step 4: Run endpoint tests**

Run: `python -m pytest api/tests/test_imports.py -k "approve_origin" -q`

Expected: PASS.

### Task 4: URL duplicate classification

**Files:**
- Modify: `api/app/schemas/imports.py`
- Modify: `api/app/services/imports.py`
- Test: `api/tests/test_imports.py`

**Interfaces:**
- Produces: `ImportPreview.url_duplicates` and `ImportResult.skipped_url_duplicates`.
- Produces: `DUPLICATE_IMAGE_URL` non-blocking groups and `CONFLICTING_IMAGE_SPECIES` blocking groups.

- [ ] **Step 1: Change duplicate tests to the desired contract**

```python
def test_same_species_same_original_url_is_skipped():
    assert (report.new_rows, report.url_duplicates, report.can_commit) == (1, 1, True)

def test_same_original_url_across_species_blocks():
    assert report.issue_groups[0].code == "CONFLICTING_IMAGE_SPECIES"
    assert report.can_commit is False
```

- [ ] **Step 2: Run duplicate tests and verify current warning-only failure**

Run: `python -m pytest api/tests/test_imports.py -k "duplicate" -q`

Expected: FAIL because the second same-URL candidate is currently inserted.

- [ ] **Step 3: Implement species-aware canonical URL index**

Track each canonical URL’s prior `(identity, species_code)`. Same species increments `url_duplicates` and continues without appending. Different species increments the conflict and continues as blocking. Apply the same rule to existing database candidates.

- [ ] **Step 4: Run duplicate tests**

Run: `python -m pytest api/tests/test_imports.py -k "duplicate" -q`

Expected: PASS.

### Task 5: Explicit skip-blocking commit

**Files:**
- Modify: `api/app/schemas/imports.py`
- Modify: `api/app/services/imports.py`
- Modify: `api/app/api/routes/imports.py`
- Test: `api/tests/test_imports.py`

**Interfaces:**
- Produces: `ImportCommitRequest(preview_token, skip_blocking_rows=False)`.
- Produces: `ImportResult.skipped_blocking`.

- [ ] **Step 1: Write failing skip tests**

```python
def test_explicit_skip_commits_valid_rows_and_audits_skipped_count():
    result = commit(blocked_preview, skip_blocking_rows=True)
    assert (result.inserted, result.skipped_blocking) == (1, 1)

def test_default_commit_still_refuses_blocked_preview():
    with pytest.raises(ImportConflict, match="IMPORT_PREVIEW_BLOCKED"):
        commit(blocked_preview, skip_blocking_rows=False)
```

- [ ] **Step 2: Run focused tests and verify schema/behavior failures**

Run: `python -m pytest api/tests/test_imports.py -k "skip_blocking" -q`

Expected: FAIL because the request and result fields do not exist.

- [ ] **Step 3: Implement skip-aware revalidation and commit**

Pass `skip_blocking_rows` into `commit_candidate_csv` and `_commit_once`. Preserve exact staged report/fingerprint comparison. Allow blocking only when explicit, `new_rows > 0`, and the stage has no fatal file code. Record the flag and exact count in the audit and stored idempotent result.

- [ ] **Step 4: Run focused and full import tests**

Run: `python -m pytest api/tests/test_imports.py -q`

Expected: PASS.

### Task 6: Export effective approvals and local manifest origins

**Files:**
- Modify: `api/app/services/exports.py`
- Modify: `api/app/api/routes/exports.py`
- Modify: `local_sync/src/sukaseafood_sync/manifest.py`
- Modify: `local_sync/src/sukaseafood_sync/image_origins.py`
- Modify: `local_sync/tests/conftest.py`
- Modify: `local_sync/tests/fixtures/export_batch.csv`
- Test: `api/tests/test_exports.py`
- Test: `local_sync/tests/test_manifest.py`

**Interfaces:**
- Produces export columns: `preview_origin`, `original_origin`.
- Produces `ManifestRow.preview_origin` and `ManifestRow.original_origin`.

- [ ] **Step 1: Write failing export/local parser tests**

```python
def test_export_carries_exact_approved_origins():
    assert row["preview_origin"] == "data.newmuseum.org"
    assert row["original_origin"] == "data.newmuseum.org"

def test_manifest_rejects_origin_not_matching_url(tmp_path):
    row["original_origin"] = "other.example.org"
    with pytest.raises(ManifestError, match="original_url"):
        load_manifest(write(row))
```

- [ ] **Step 2: Run focused RED tests**

Run: `python -m pytest api/tests/test_exports.py -k "origin" -q`

Run: `python -m pytest local_sync/tests/test_manifest.py -k "origin" -q`

Expected: FAIL because the columns and fields do not exist.

- [ ] **Step 3: Implement export columns using effective policy**

Combine database approvals before validating batch candidates. Serialize `urlsplit(candidate.preview_url).hostname.lower()` and the original equivalent after policy validation.

- [ ] **Step 4: Implement exact per-row local validation**

Parse each origin with a new exact-host helper and call `require_approved_image_url(url, (declared_origin,))`. Require all three text fields to remain bounded and preserve existing redirect validation against the same declared original policy.

- [ ] **Step 5: Update local test fixture builders mechanically**

Every test export row must include the exact hostname derived from its preview/original URL. Do not add fallback behavior for absent columns.

- [ ] **Step 6: Run focused tests**

Run: `python -m pytest api/tests/test_exports.py -k "origin" -q`

Run: `python -m pytest local_sync/tests/test_manifest.py -q`

Expected: PASS.

### Task 7: Frontend grouped issue workflow

**Files:**
- Modify: `web/src/admin/types.ts`
- Modify: `web/src/admin/ImportsTab.tsx`
- Modify: `web/src/pages/AdminPage.test.tsx`
- Modify: `web/src/styles/global.css`

**Interfaces:**
- Consumes: `ImportPreview.issue_groups`, approval and skip endpoints.
- Produces: grouped summaries, approval/repreview, skip confirmation UI, and a native accessible CSV drop zone.

- [ ] **Step 1: Write failing Vitest interactions**

```tsx
expect(screen.getByText("data.newmuseum.org：17 张")).toBeVisible();
await user.click(screen.getByRole("button", { name: "批准 data.newmuseum.org" }));
expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("approve-origin"), expect.anything());
expect(await screen.findByText("预检查完成，可以提交")).toBeVisible();

await user.click(screen.getByRole("button", { name: /跳过 2 条阻断/ }));
expect(screen.getByText(/将导入 10 条有效数据/)).toBeVisible();

const dropZone = screen.getByRole("button", { name: "拖入或选择候选 CSV" });
fireEvent.dragEnter(dropZone, { dataTransfer: { files: [file], types: ["Files"] } });
expect(dropZone).toHaveClass("csv-drop-zone--active");
fireEvent.drop(dropZone, { dataTransfer: { files: [file], types: ["Files"] } });
expect(screen.getByText("candidates.csv")).toBeVisible();
```

- [ ] **Step 2: Run RED frontend tests**

Run: `npm test -- src/pages/AdminPage.test.tsx`

Expected: FAIL because issue groups and actions are absent.

- [ ] **Step 3: Parse the new strict response contract**

Validate exact group keys, bounded hostnames, bounded sample rows and the renamed duplicate/result counters. Continue to reject malformed success responses.

- [ ] **Step 4: Render grouped problems and actionable solutions**

Use one card per group. `UNAPPROVED_IMAGE_HOST` gets an approve button. Warnings state that they do not block. A `<details>` section shows sample rows. A blocked preview offers the explicit skip flow only when `new_rows > 0`.

- [ ] **Step 5: Implement the CSV drop zone**

Keep the real file input and associate it with a focusable full-width label/drop target. Route native selection and drop through `choose(nextFile)`. Track drag depth so nested drag events do not flicker. Reject zero/multiple files and non-`.csv` extensions before preview with the existing Chinese error notice. Dropping never invokes `runPreview()` automatically.

- [ ] **Step 6: Implement approve then repreview**

POST the preview token and hostname with CSRF. On success call the same `runPreview()` using the retained `File`; discard the old visible token/result before the request.

- [ ] **Step 7: Implement two-step skip commit**

Send `{ preview_token, skip_blocking_rows: true }` only after the confirmation names both counts. Normal submit sends `false`.

- [ ] **Step 8: Run frontend tests**

Run: `npm test -- src/pages/AdminPage.test.tsx src/pages/AdminPage.review.test.tsx`

Expected: PASS.

### Task 8: Cross-layer verification

**Files:**
- Modify only files required by failures that directly contradict this spec.

- [ ] **Step 1: Run API suite**

Run: `python -m pytest api/tests -q`

Expected: PASS.

- [ ] **Step 2: Run local sync suite**

Run: `python -m pytest local_sync/tests -q`

Expected: PASS.

- [ ] **Step 3: Run frontend suite and build**

Run: `npm test && npm run typecheck && npm run build` from `web/`.

Expected: PASS.

- [ ] **Step 4: Run migration upgrade checks**

Run: `python -m alembic -c api/alembic.ini upgrade head` against the test database path used by migration tests.

Expected: revision `20260827_08` is head and the approval table/constraints exist.
