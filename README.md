# SukaSeafood collaborative review system

[中文](README_ZH.md)

## Outcome

This repository implements the complete SukaSeafood collaborative-review Web/API and Windows local-sync tool. Six fixed accounts—Hassan, Mao, Xinhui, Wahid, Sharmaa, and Yiming—share one review pool; Mao is the sole administrator. There are no per-person quotas, and a candidate is never assigned to someone who has already reviewed it. Each KEEP, REJECT, or UNSURE choice is written immediately, and the next candidate is requested only after the database confirms the result.

Reviewers can view and edit only their own current history, while everyone can see aggregate progress. Mao has a Chinese-only, seven-tab area titled “管理后台” for progress, candidates, dynamic species, review history, CSV imports, training-sync batches, and accounts. The server stores structured candidate metadata, external URLs, review results, bounded CSV files, and receipts. It never stores, caches, proxies, or downloads original image bytes. The Windows local-sync tool is implemented and has Mao's computer contact approved external sources directly; see [`local_sync/README_ZH.md`](local_sync/README_ZH.md).

## Repository and current development context

As of 2026-08-27, this implementation is local and unpublished. It is on branch `codex/collaborative-review` in `C:\Users\86166\Desktop\sukaSeafoodReview\.worktrees\collaborative-review`. The `origin` remote currently has no published refs; `https://github.com/XIANGYU-MAO/sukaSeafoodReview.git` is only the target URL. `.worktrees` is a local Git isolation detail, not a runtime requirement.

Only after this branch has been explicitly merged, pushed, and published may other users run the following clone command; it is not a working acquisition path today:

```powershell
git clone https://github.com/XIANGYU-MAO/sukaSeafoodReview.git
Set-Location .\sukaSeafoodReview
```

Until then, local commands run from the repository root of the current checkout above. Do not copy or depend on another machine's `.worktrees` directory. This document describes the current implemented code; it does not claim a merge, push, or production release.

## Architecture and data flow

- `web/`: React 19, TypeScript, and Vite. The production build base and browser router base are fixed at `/sukaseafood/review/`.
- `api/`: FastAPI, async SQLAlchemy, and Alembic. Internal application routes start at `/v1`.
- `local_sync/`: independent CLI/Tkinter Windows synchronizer, resumable index, and frozen build; see [`local_sync/README_ZH.md`](local_sync/README_ZH.md).
- `deploy/` and the two Compose files: fixed-path production backup, restore, first-deploy, preflight, import, and rollback artifacts.
- Development browser entry: `http://localhost:5173/sukaseafood/review/`. Vite rewrites only `/sukaseafood/api` to the local FastAPI root, so the Web app continues to use `/sukaseafood/api/v1`.
- Planned production entry: `https://findai.top/sukaseafood/review`; the external API prefix is fixed at `/sukaseafood/api/v1`.
- The browser obtains candidate metadata from the API and loads an image directly from its external HTTPS URL. Image bytes neither pass through the China server nor enter its database or filesystem.
- Review submissions carry CSRF and an Idempotency-Key. The API returns a receipt after the database transaction commits; the Web app validates that receipt before refreshing progress and requesting the next candidate.

The system exposes no image-upload, original-image proxy, or original-image download API. The prepared YGF release removes `/project`, `/project/*`, and `/project-assets/*` while preserving the other YGF pages. Those gateway changes have not been deployed, so live routing will not change without an explicitly authorized release.

## Prerequisites

- Windows PowerShell 7; Windows PowerShell 5.1 can also run the basic commands below.
- Python 3.12.
- Node.js 22.12 or later, with npm.
- API tests/development may use SQLite; production business data uses PostgreSQL only. The local sync tool has a separate small SQLite recovery index containing operation keys, relative paths, hashes, and receipt state; it stores no image bytes, original URLs, or batch tokens.
- Production requires PostgreSQL 16, HTTPS, and `SECURE_COOKIE=true`. Production configuration rejects SQLite and insecure cookies.

To run the real PostgreSQL concurrency tests, prepare a separate PostgreSQL 16 test database that may be erased. Never point tests at production.

## Windows local quick start

All commands begin at the repository root. Create the API virtual environment and copy the safe example first:

```powershell
Set-Location .\api
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
Set-Location ..
Copy-Item .\api\.env.example .\api\.env
```

`api/.env` is local-only. Replace the three `change-me-*` values with different local random values. Do not commit the file or reuse production values.

Terminal 1: load `.env` into the current PowerShell process, migrate, seed the default species and six accounts, and start the API:

```powershell
Get-Content .\api\.env | Where-Object { $_ -match '^[^#][^=]*=' } | ForEach-Object {
    $envEntry = $_ -split '=', 2
    [Environment]::SetEnvironmentVariable($envEntry[0].Trim(), $envEntry[1].Trim(), 'Process')
}
Set-Location .\api
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m app.commands.seed_species
.\.venv\Scripts\python.exe -m app.commands.seed_users --print-once
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

The species seed only fills missing SF001–SF005 defaults. It does not overwrite Mao's edits or delete or reject future species such as SF006; a no-op rerun prints nothing. The first account seed prints one temporary password for each account exactly once. Store and distribute them securely at once. Running it again against the same database neither prints nor replaces them.

Terminal 2: install Web dependencies and start Vite:

```powershell
Set-Location .\web
npm install
npm run dev
```

Open `http://localhost:5173/sukaseafood/review/`. The development example intentionally uses `APP_ENV=development`, SQLite, and `SECURE_COOKIE=false`, because ordinary `http://localhost` cannot send a Secure cookie. This setting is for local HTTP development only.

## Accounts, passwords, and sessions

The public name order is fixed: Hassan, Mao, Xinhui, Wahid, Sharmaa, Yiming. Mao is `admin`; all others are `reviewer`. Registration, social login, and arbitrary accounts are unsupported. A first login must change the temporary password. A successful password change revokes all sessions and returns to login; an administrator reset also revokes that reviewer's sessions.

Sessions are stored in the database. The browser receives only an HttpOnly, SameSite=Lax cookie with `Path=/sukaseafood`. A refresh restores the session through `/sukaseafood/api/v1/auth/me`. Production must use `SECURE_COOKIE=true` over HTTPS; production configuration rejects `SECURE_COOKIE=false`. In this flow, login is the unauthenticated entry point. After login, browser-authenticated state mutations use the session and its derived CSRF value, and each concrete review submission also needs its own Idempotency-Key. The local-sync tool submits `/v1/sync/batches/{batch_id}/receipt` with a batch token, not a browser session or CSRF.

Temporary and reset passwords are shown once. Never put passwords, session cookies, CSRF values, receipt secrets, database URLs, or SSH credentials in Git, screenshots, logs, or issue reports.

## Import the initial 1,221-row manifest

The real legacy manifest is `C:\Users\86166\Desktop\SukaSeafood_CV_Dataset_Collector\output\candidates.csv`. Confirm that the default species seed has run, then run the read-only dry-run from a terminal in which the API environment has already been loaded:

```powershell
Set-Location .\api
.\.venv\Scripts\python.exe -m app.commands.import_candidates 'C:\Users\86166\Desktop\SukaSeafood_CV_Dataset_Collector\output\candidates.csv' --dry-run
```

The current observation for that real file on 2026-08-26 was: 1,221 new candidate rows, 247 possible URL duplicates, 262 warnings, and 0 blocking errors. This is verification evidence from one run, not a permanent invariant for future files or database states. Re-run the dry-run whenever the file or database changes.

The CLI dry-run writes no candidates and creates no committable preview token. After reviewing it, Mao may choose the same CSV on the Chinese “导入” tab, preview it, and explicitly confirm commit. The production first-import script instead dry-runs server-side, then uses the same CLI's `--commit` mode to transactionally revalidate and insert. Exact CLI repeats are idempotent. A Web preview token remains only in the current page's memory and expires; a new file, terminal conflict, or successful commit invalidates it.

## Reviewer workflow

1. Select a fixed name and log in; change the temporary password on first login.
2. The home page restores or obtains one candidate from the shared pool that this reviewer has not reviewed before.
3. A spinner remains visible while the image loads. A failure becomes a finite error state with retry and “image URL unavailable” actions.
4. Inspect bilingual species names, scientific name, source, source record, licence, and safe external links. The application hands URLs to the browser; it does not fetch images.
5. Choose KEEP, REJECT, or UNSURE. REJECT requires a pill-shaped reason, and “Other” requires notes. K/R/U shortcuts do not capture input controls.
6. The Web app submits immediately and waits for a database receipt. Only a receipt with the correct identity, content, and version triggers a progress refresh and the next candidate. There is no separate Save button.
7. History requests only the signed-in reviewer's rows and never sends a reviewer query parameter. Only the current version is editable; older attempts are read-only, and a 409 conflict never overwrites silently.

Aggregate progress contains only counts and six member aggregates—no notes, image URLs, candidate IDs, review IDs, or personal history items. Member work totals count all submitted attempts, while the overall total describes the active dataset; after Mao reopens an item, those totals can legitimately differ.

## Seven-tab Chinese administration

The page has the generic title “管理后台” (Administration), while Mao remains its only authorized account. After Mao logs in, the interface remains Chinese. A reviewer who enters `/admin` is redirected to review before any admin request is made. The seven tabs are:

1. 审核进度 — team aggregates and current assignments.
2. 候选图片 — filters, safe metadata corrections, release, and transfer of unsubmitted current candidates.
3. 鱼种管理 (Species management) — create, edit, deactivate, and reactivate species with immutable Windows-safe codes. The default seed is not a five-species ceiling: before importing SF006 or any future species, Mao adds its directory entry here under the safe-code rules.
4. 审核历史 — cross-member filtering, version-protected corrections, and reopening for a specified active reviewer who has never reviewed the candidate.
5. 导入 — CSV preview and atomic commit.
6. 训练集同步 — pending counts, immutable incremental batches, small CSV downloads, and JSON receipt-file upload.
7. 账号 — the fixed directory and reviewer password reset; Mao is not reset through the Web UI.

Browser-based admin mutations require Mao's session, CSRF, and the confirmations specified by each API. Only admin data operations whose request models include `reason`—such as candidate, species, review-history, and account changes—require and audit a reason. Import preview/commit, export batches, and receipts follow their own token, confirmation, and authentication contracts and do not invent `reason`. The interface does not render raw server errors, free-text failed-receipt content, import tokens, or dismissed one-time passwords.

## Incremental CSV and local downloader boundary

The server uses one envelope for incremental batches: at most 10,000 rows per batch, at most 20 MiB after serializing the exact 16-column CSV, and at most 20 MiB for an online or offline receipt upload. More than 10,000 eligible operations are split into later, non-overlapping batches; a single row that breaches the byte limit fails before any batch is persisted. ADD, REMOVE, and MOVE rows come from one coherent PostgreSQL snapshot and carry the server-selected exact relative path and a monotonic review generation. CSV download is an authenticated same-origin, `no-store` attachment; receipt submission is a bounded `application/json` POST.

The independent `local_sync` package, CLI/Tkinter UI, and Windows executable are implemented. Mao's computer contacts each approved `original_url` directly, validates every redirect, verifies image content and hashes, uses `.part` plus atomic rename for idempotent resume, and moves REMOVE targets into recoverable `_removed` paths. Exact hosts/domain suffixes are configurable with `IMAGE_ORIGIN_ALLOWLIST` on the server and `SUKASEAFOOD_IMAGE_ORIGIN_ALLOWLIST` in the local tool. Localhost, IP literals, and unapproved sources are rejected. A configured proxy is trusted only to connect to an already approved hostname; the downloader sends no cookies or credentials to image sources. The China server never issues image HEAD/GET requests and has no image cache or proxy.

If cancellation or connectivity interrupts submission, safely completed operations remain in the local index and the tool writes `download_receipt-{batch_id}.json` as an offline receipt for later submission. Replaying an older generation cannot overwrite a newer review result. See [`local_sync/README_ZH.md`](local_sync/README_ZH.md) for commands and recovery; never fabricate successful receipts manually.

## Verification commands

Full SQLite backend, compilation, and migration checks:

```powershell
Set-Location .\api
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall app tests alembic
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic check
```

Verify downgrade and re-upgrade only on a disposable development/test database:

```powershell
.\.venv\Scripts\python.exe -m alembic downgrade -1
.\.venv\Scripts\python.exe -m alembic upgrade head
```

Real PostgreSQL tests (replace placeholders with an isolated test database, never production):

```powershell
$env:TEST_POSTGRES_URL = 'postgresql+asyncpg://<test-user>:<test-password>@127.0.0.1:<port>/<test-db>'
.\.venv\Scripts\python.exe -m pytest -q
```

Full Web tests, type checking, and production build:

```powershell
Set-Location ..\web
npm test
npm run typecheck
npm run build
```

Local-sync tests, compilation, and locked build:

```powershell
Set-Location ..\local_sync
python -m pytest -q
python -m compileall src tests
Set-Location ..
powershell -NoProfile -ExecutionPolicy Bypass -File local_sync/scripts/build_windows.ps1
```

Production assets must remain under `/sukaseafood/review/assets/`. The deployment artifacts are implemented and locally verified, but no production SSH deployment or public acceptance has been executed. Going live, reloading Caddy, six-account browser acceptance, and rollback exercises all require explicit authorization.

## Troubleshooting

- Local login remains at 401: confirm both API and Vite are running, use `http://localhost:5173/sukaseafood/review/`, and keep local `.env` at `APP_ENV=development` plus `SECURE_COOKIE=false`. Reload `.env` in the same terminal before restarting the API.
- Production cookie configuration prevents startup: production supports only HTTPS with `SECURE_COOKIE=true`; do not disable secure cookies to bypass the check.
- A 401 means there is no valid session or it has been revoked. A 403 usually means the role, first-password gate, or CSRF boundary is unmet. Refresh and log in again; never copy CSRF from another session.
- An external image is blocked or broken: inspect browser network access, the source host, HTTPS, and content blockers. The server will not proxy it. Use page retry or “image URL unavailable”; Mao can correct the URL if necessary.
- Source collection receives 429: Wikimedia/GBIF/iNaturalist collection and retry belong to the legacy `SukaSeafood_CV_Dataset_Collector`, not this review server. A 429 from the login API is its own authentication limit and should also be retried later.
- PostgreSQL integration tests are skipped: set `TEST_POSTGRES_URL` to a separate PostgreSQL 16 test database. SQLite cannot prove row locks, SKIP LOCKED, or contention behavior.
- Import returns 409: the preview may be expired, committed, owned by another session, or stale against file/database state. Select the file and preview again; never reuse the old token.
- Receipt returns 409/422: verify batch, review ID, version, status, and the exact server-provided path. Fetch the current batch again; never treat a conflict as success.

## Repository layout and later stages

```text
api/                         FastAPI, models, migrations, CLIs, and backend tests
web/                         React/Vite Web application and Web tests
local_sync/                  Windows synchronizer, tests, build, and Chinese guide
deploy/                      Production scripts, environment template, operations and rollback checklists
docs/superpowers/specs/      Approved system design
docs/superpowers/plans/      Core, local-sync, and production plans
```

- Design: `docs/superpowers/specs/2026-08-26-collaborative-review-system-design.md`
- Core plan: `docs/superpowers/plans/2026-08-26-collaborative-review-core.md`
- Windows local-sync implementation plan: `docs/superpowers/plans/2026-08-26-local-training-sync.md` (code and frozen-build flow implemented)
- Production and YGF routing plan: `docs/superpowers/plans/2026-08-26-production-deployment.md` (artifacts and isolated gateway commit prepared, not live)

Production Compose, images, backup/restore, first-deploy, preflight, import, and rollback artifacts are prepared and locally verified. The isolated YGF release is also prepared to remove `/project` and attach `/sukaseafood/review` plus `/sukaseafood/api/v1`. This branch has performed no SSH, push, deployment, Caddy reload, production-data import, or public acceptance; every external action still requires the user's explicit authorization. This document contains no real server, SSH, database, production-password, or secret values.
