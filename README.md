# SukaSeafood collaborative review system

[中文](README_ZH.md)

## Outcome

This repository implements the complete SukaSeafood collaborative-review Web/API and Windows local-sync tool. Six fixed accounts—Hassan, Mao, Xinhui, Wahid, Sharmaa, and Yiming—share one review pool; Mao is the sole administrator. There are no per-person quotas, and a candidate is never assigned to someone who has already reviewed it. Each KEEP, REJECT, or UNSURE choice is written immediately, and the next candidate is requested only after the database confirms the result.

Reviewers can view and edit only their own current history. A top-level Team progress page shows everyone aggregate counts without exposing another member's review details. The login page and authenticated interface switch between Chinese and English; in Chinese, the password action is only “修改密码”. The first visit to review after each successful explicit login shows bilingual review guidelines once, and confirmation suppresses them across refreshes and in-app navigation for that login. Mao has a Chinese-only, seven-tab area titled “管理后台” for progress, candidates, dynamic species, review history, CSV imports, training-sync batches, and accounts. The server stores structured candidate metadata, external URLs, review results, bounded CSV files, and receipts. It never stores, caches, proxies, or downloads original image bytes. The Windows local-sync tool is implemented and has Mao's computer contact approved external sources directly; see [`local_sync/README_ZH.md`](local_sync/README_ZH.md).

## Repository and current development context

As of 2026-08-27, this implementation is merged, pushed, and deployed to production from `main`. The local repository root is `C:\Users\86166\Desktop\sukaSeafoodReview`, the published repository is `https://github.com/XIANGYU-MAO/sukaSeafoodReview.git`, and the live review entry is `https://findai.top/sukaseafood/review/`.

Other developers can obtain the current `main` branch with:

```powershell
git clone https://github.com/XIANGYU-MAO/sukaSeafoodReview.git
Set-Location .\sukaSeafoodReview
```

Run local commands from the cloned repository root. A `.worktrees` directory is never a runtime requirement.

## Architecture and data flow

- `web/`: React 19, TypeScript, and Vite. The production build base and browser router base are fixed at `/sukaseafood/review/`.
- `api/`: FastAPI, async SQLAlchemy, and Alembic. Internal application routes start at `/v1`.
- `collector/`: Mao's Windows-only metadata collector. It reads the current species configuration and writes `collector/output/candidates.csv` for review import.
- `local_sync/`: the separate approved-original downloader, with an independent CLI/Tkinter Windows synchronizer, resumable index, and frozen build; see [`local_sync/README_ZH.md`](local_sync/README_ZH.md).
- `deploy/` and the two Compose files: fixed-path production backup, restore, first-deploy, preflight, import, and rollback artifacts.
- Development browser entry: `http://localhost:5173/sukaseafood/review/`. Vite rewrites only `/sukaseafood/api` to the local FastAPI root, so the Web app continues to use `/sukaseafood/api/v1`.
- Production entry: `https://findai.top/sukaseafood/review/`; the external API prefix is fixed at `/sukaseafood/api/v1`.
- The browser obtains candidate metadata from the API and loads an image directly from its external HTTPS URL. Image bytes neither pass through the China server nor enter its database or filesystem.
- Review submissions carry CSRF and an Idempotency-Key. The API returns a receipt after the database transaction commits; the Web app validates that receipt before requesting the next candidate. The independent Team progress page reads the latest aggregate when opened.

The system exposes no image-upload, original-image proxy, or original-image download API. The YGF gateway now returns 404 for `/project`, `/project/*`, and `/project-assets/*` while preserving its other pages and routing `/sukaseafood` to this review system.

## Prerequisites

- Windows PowerShell 7; Windows PowerShell 5.1 can also run the basic commands below.
- Python 3.12.
- Node.js 22.12 or later, with npm.
- API tests/development may use SQLite; production business data uses PostgreSQL only. The local sync tool has a separate small SQLite recovery index containing candidate synchronization generations, relative paths, hashes, receipt state, and recovery intent; it stores no image bytes, original URLs, or batch tokens.
- Production requires PostgreSQL 16, HTTPS, and `SECURE_COOKIE=true`. Production configuration rejects SQLite and insecure cookies.

To run the real PostgreSQL concurrency tests, prepare a separate PostgreSQL 16 test database that may be erased. Never point tests at production.

## Windows local quick start

All commands begin at the repository root. Create the API virtual environment and copy the safe example first:

```powershell
Set-Location .\api
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple --upgrade pip
.\.venv\Scripts\python.exe -m pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt -r requirements-dev.txt
Set-Location ..
Copy-Item .\api\.env.example .\api\.env
```

`api/.env` is local-only. Replace the three `change-me-*` values with different local random values. Do not commit the file or reuse production values.

Terminal 1: load `.env` into the current PowerShell process, migrate, seed the six accounts, and start the API:

```powershell
Get-Content .\api\.env | Where-Object { $_ -match '^[^#][^=]*=' } | ForEach-Object {
    $envEntry = $_ -split '=', 2
    [Environment]::SetEnvironmentVariable($envEntry[0].Trim(), $envEntry[1].Trim(), 'Process')
}
Set-Location .\api
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m app.commands.seed_users --print-once
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

A fresh database contains the six accounts but an empty species catalog. Mao creates and maintains any current species in the Chinese admin before collecting or importing candidates. The first account seed prints one temporary password for each account exactly once. Store and distribute them securely at once. Running it again against the same database neither prints nor replaces them.

Terminal 2: install Web dependencies and start Vite:

```powershell
Set-Location .\web
npm install --registry=https://registry.npmmirror.com
npm run dev
```

Open `http://localhost:5173/sukaseafood/review/`. The development example intentionally uses `APP_ENV=development`, SQLite, and `SECURE_COOKIE=false`, because ordinary `http://localhost` cannot send a Secure cookie. This setting is for local HTTP development only.

## Accounts, passwords, and sessions

The public name order is fixed: Hassan, Mao, Xinhui, Wahid, Sharmaa, Yiming. Mao is `admin`; all others are `reviewer`. Registration, social login, and arbitrary accounts are unsupported. A first login must change the temporary password. A successful password change revokes all sessions and returns to login; an administrator reset also revokes that reviewer's sessions.

Sessions are stored in the database. The browser receives only an HttpOnly, SameSite=Lax cookie with `Path=/sukaseafood`. A refresh restores the session through `/sukaseafood/api/v1/auth/me`. Production must use `SECURE_COOKIE=true` over HTTPS; production configuration rejects `SECURE_COOKIE=false`. In this flow, login is the unauthenticated entry point. After login, browser-authenticated state mutations use the session and its derived CSRF value, and each concrete review submission also needs its own Idempotency-Key. The local-sync tool submits `/v1/sync/batches/{batch_id}/receipt` with a batch token, not a browser session or CSRF.

The login page uses six pill buttons for the fixed names and can switch directly between Chinese and English. The selected name, password entry, and visible error survive a language switch, and the chosen locale continues after authentication. A successful explicit login starts a new guideline cycle for that reviewer. After the reviewer confirms the Review guidelines on the first visit to review, refreshes and navigation through History or Team progress do not show them again. Restoring an existing cookie session does not reset that marker.

Temporary and reset passwords are shown once. Never put passwords, session cookies, CSRF values, receipt secrets, database URLs, or SSH credentials in Git, screenshots, logs, or issue reports.

## Collection and import

Mao's normal four-step workflow is: (1) manage current species in the Chinese admin, (2) download the collector and current configuration, (3) run `collector/` locally to produce `C:\Users\86166\Desktop\sukaSeafoodReview\collector\output\candidates.csv`, and (4) preview and explicitly commit the CSV. The initial catalog is empty; Mao may create any valid current species before collection.

The collector output may contain any valid number of rows and any supported mix of sources. From a terminal with the API environment loaded, run a read-only dry-run:

```powershell
Set-Location .\api
.\.venv\Scripts\python.exe -m app.commands.import_candidates 'C:\Users\86166\Desktop\sukaSeafoodReview\collector\output\candidates.csv' --dry-run
```

The CLI dry-run writes no candidates and creates no committable preview token. It must report zero blocking errors and `can_commit=true` before Mao explicitly commits. After reviewing it, Mao may choose the same CSV on the Chinese “采集与导入” tab, preview it, and explicitly confirm commit. The production helper uses the same dry-run and `--commit` transactionally, then verifies the returned `file_sha256` against the local CSV and prints `total`, `inserted`, `skipped_exact`, and `possible_url_duplicates`. Exact CLI repeats are idempotent. A Web preview token remains only in the current page's memory and expires; a new file, terminal conflict, or successful commit invalidates it.

## Reviewer workflow

1. Select a fixed name with a pill button and log in. The login page can switch between Chinese and English; change the temporary password on first login.
2. The first visit to review after each successful login shows Review guidelines once. Approve when the species can be confirmed and the fish or identifying features are clear; clean museum specimens, real fish on ice or at a market, and clear multiple fish of one species are acceptable. Reject an unconfirmed species, tiny or heavily overlapping schools, mixed species with no clear target, severe blur or occlusion, cuts, cooked fish, artwork, and duplicates. Confirmation suppresses the dialog for the rest of that login.
3. The home page restores or obtains one candidate from the shared pool that this reviewer has not reviewed before.
4. A spinner remains visible while the image loads. A failure becomes a finite error state with retry and “image URL unavailable” actions.
5. Inspect bilingual species names, scientific name, source, source record, licence, and safe external links. The application hands URLs to the browser; it does not fetch images.
6. Choose KEEP, REJECT, or UNSURE. REJECT requires a pill-shaped reason, and “Other” requires notes. K/R/U shortcuts do not capture input controls.
7. The Web app submits immediately and waits for a database receipt. Only a receipt with the correct identity, content, and version triggers the next candidate. There is no separate Save button.
8. The top-level History page requests only the signed-in reviewer's rows, while the adjacent Team progress page requests aggregates only. History never sends a reviewer query parameter. Only the current version is editable; older attempts are read-only, and a 409 conflict never overwrites silently.

Aggregate progress contains only counts and six member aggregates—no notes, image URLs, candidate IDs, review IDs, or personal history items. Member work totals count all submitted attempts, while the overall total describes the active dataset; after Mao reopens an item, those totals can legitimately differ.

## Seven-tab Chinese administration

The page has the generic title “管理后台” (Administration), while Mao remains its only authorized account. After Mao logs in, the interface remains Chinese. A reviewer who enters `/admin` is redirected to review before any admin request is made. The seven tabs are:

1. 审核进度 — team aggregates and current assignments.
2. 候选图片 — filters, safe metadata corrections, release, and transfer of unsubmitted current candidates.
3. 鱼种管理 (Species management) — create, edit, deactivate, and reactivate species with immutable Windows-safe codes. The initial catalog is empty: before importing SF006 or any other current species, Mao adds its directory entry here under the safe-code rules.
4. 审核历史 — cross-member filtering, version-protected corrections, and reopening for a specified active reviewer who has never reviewed the candidate.
5. 采集与导入 — the four-step collector workflow, CSV preview, and atomic commit.
6. 训练集同步 — pending counts, immutable incremental batches, small CSV downloads, and JSON receipt-file upload.
7. 账号 — the fixed directory and reviewer password reset; Mao is not reset through the Web UI.

Browser-based admin mutations require Mao's session, CSRF, and the confirmations specified by each API. Only admin data operations whose request models include `reason`—such as candidate, species, review-history, and account changes—require and audit a reason. Import preview/commit, export batches, and receipts follow their own token, confirmation, and authentication contracts and do not invent `reason`. The interface does not render raw server errors, free-text failed-receipt content, import tokens, or dismissed one-time passwords.

## Incremental CSV and local downloader boundary

The server uses one envelope for incremental batches: at most 10,000 rows per batch, at most 20 MiB after serializing the exact 16-column CSV, and at most 20 MiB for an online or offline receipt upload. More than 10,000 eligible operations are split into later, non-overlapping batches; a single row that breaches the byte limit fails before any batch is persisted. ADD, REMOVE, and MOVE rows come from one coherent PostgreSQL snapshot and carry the server-selected exact relative path and a monotonic **candidate synchronization generation**. The fixed wire column remains named `review_version` for compatibility; its value is the candidate's synchronization generation, not a reviewer's edit count. CSV download is an authenticated same-origin, `no-store` attachment; receipt submission is a bounded `application/json` POST.

Alembic revision `20260827_07` starts the new synchronization epoch under the same PostgreSQL serialization boundary used by export creation. It raises each candidate's generation above its current value and every historical value for that candidate in reviews, review revisions, and export items; it refuses integer exhaustion and expires all pending pre-revision batches so one batch cannot mix the former and current meanings. Existing local roots do not need a destructive reset: the first post-upgrade generation is newer than every legitimate pre-upgrade local value.

The independent `local_sync` package, CLI/Tkinter UI, and Windows executable are implemented. Mao's computer contacts each approved `original_url` directly, validates every redirect, verifies image content and hashes, uses `.part` plus atomic rename for idempotent resume, and moves REMOVE targets into recoverable `_removed` paths. Exact hosts/domain suffixes are configurable with `IMAGE_ORIGIN_ALLOWLIST` on the server and `SUKASEAFOOD_IMAGE_ORIGIN_ALLOWLIST` in the local tool. Localhost, IP literals, and unapproved sources are rejected. A configured proxy is trusted only to connect to an already approved hostname; the downloader sends no cookies or credentials to image sources. The China server never issues image HEAD/GET requests and has no image cache or proxy.

If cancellation or connectivity interrupts submission, safely completed operations remain in the local index and the tool writes `download_receipt-{batch_id}.json` as an offline receipt for later submission. Replaying an older candidate synchronization generation cannot overwrite a newer file, index row, or canonical manifest row. Local SQLite schema v3 records synchronization generations, hashes, paths, and bounded replacement-recovery intent. A same-path replacement is allowed only when SQLite owns the exact existing path and its on-disk SHA-256 still matches the prior generation; otherwise the tool leaves the file untouched and reports a conflict. An interruption recovers from the verified staged image and `_removed/{batch_id}/` backup, or restores the old verified image when the new stage is unavailable. See [`local_sync/README_ZH.md`](local_sync/README_ZH.md) for commands and recovery; never fabricate successful receipts manually.

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

Production assets remain under `/sukaseafood/review/assets/`. The review system is deployed, and production SSH deployment and public acceptance are complete: the page, API health endpoint, collector ZIP, six-account login selector, and retired `/project` path have been verified publicly.

## Troubleshooting

- Local login remains at 401: confirm both API and Vite are running, use `http://localhost:5173/sukaseafood/review/`, and keep local `.env` at `APP_ENV=development` plus `SECURE_COOKIE=false`. Reload `.env` in the same terminal before restarting the API.
- Production cookie configuration prevents startup: production supports only HTTPS with `SECURE_COOKIE=true`; do not disable secure cookies to bypass the check.
- A 401 means there is no valid session or it has been revoked. A 403 usually means the role, first-password gate, or CSRF boundary is unmet. Refresh and log in again; never copy CSRF from another session.
- An external image is blocked or broken: inspect browser network access, the source host, HTTPS, and content blockers. The server will not proxy it. Use page retry or “image URL unavailable”; Mao can correct the URL if necessary.
- Source collection receives 429: Wikimedia/GBIF/iNaturalist collection and retry belong to the local `collector/`, not this review server. A 429 from the login API is its own authentication limit and should also be retried later.
- PostgreSQL integration tests are skipped: set `TEST_POSTGRES_URL` to a separate PostgreSQL 16 test database. SQLite cannot prove row locks, SKIP LOCKED, or contention behavior.
- Import returns 409: the preview may be expired, committed, owned by another session, or stale against file/database state. Select the file and preview again; never reuse the old token.
- Receipt returns 409/422: verify batch, review ID, version, status, and the exact server-provided path. Fetch the current batch again; never treat a conflict as success.

## Repository layout and later stages

```text
api/                         FastAPI, models, migrations, CLIs, and backend tests
web/                         React/Vite Web application and Web tests
collector/                   Windows metadata collector and its tests
local_sync/                  Separate approved-original downloader, tests, build, and Chinese guide
deploy/                      Production scripts, environment template, operations and rollback checklists
docs/superpowers/specs/      Approved system design
docs/superpowers/plans/      Core, local-sync, and production plans
```

- Design: `docs/superpowers/specs/2026-08-26-collaborative-review-system-design.md`
- Current collector authority: `docs/superpowers/specs/2026-08-27-dynamic-collector-admin-integration-design.md`
- Dynamic collector integration plan: `docs/superpowers/plans/2026-08-27-dynamic-collector-admin-integration.md`
- Core plan: `docs/superpowers/plans/2026-08-26-collaborative-review-core.md`
- Windows local-sync implementation plan: `docs/superpowers/plans/2026-08-26-local-training-sync.md` (code and frozen-build flow implemented)
- Production and YGF routing plan: `docs/superpowers/plans/2026-08-26-production-deployment.md` (artifacts and isolated gateway commit prepared, not live)

Production Compose, images, backup/restore, first-deploy, preflight, import, and rollback artifacts are prepared and locally verified. The isolated YGF release is also prepared to remove `/project` and attach `/sukaseafood/review` plus `/sukaseafood/api/v1`. This branch has performed no SSH, push, deployment, Caddy reload, production-data import, or public acceptance; every external action still requires the user's explicit authorization. This document contains no real server, SSH, database, production-password, or secret values.
