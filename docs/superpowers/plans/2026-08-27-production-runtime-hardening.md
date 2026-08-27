# Production Runtime Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the approved-image boundary, read-only Web container, candidate import handoff, and PostgreSQL restore workflow behave correctly under real production constraints.

**Architecture:** A root contract file becomes the single default image-origin source for API, frozen local sync, and generated Web CSP. The Web container generates a validated Nginx config into `/tmp`; fixed runtime IDs define import ownership; restore uses one database transaction and never restarts the API after failure. Behavior tests start containers and execute scripts with controlled fakes instead of relying only on text inspection.

**Tech Stack:** Python 3.12, POSIX shell, PowerShell 7/Windows PowerShell, Docker 27, Compose 2, Nginx 1.27 Alpine, PostgreSQL 16, pytest.

**Spec:** `docs/superpowers/specs/2026-08-27-final-acceptance-remediation-design.md`

## Global Constraints

- Original and preview image bytes must never transit or persist on the China server.
- Image URLs must use HTTPS port 443, no credentials, no literal IP, no localhost, and an approved exact/suffix hostname at every redirect hop.
- The production Web and API containers remain non-root with read-only root filesystems.
- PostgreSQL is never published on a host port.
- Secrets remain outside Git and are never printed by tests or deployment scripts.
- Every production change begins with a failing behavior test.
- Do not deploy, upload candidates, create secrets, or reload the live gateway in this plan.

---

### Task 1: Establish one canonical image-origin contract

**Files:**
- Create: `contracts/image_origins.txt`
- Create: `api/app/contract_files.py`
- Modify: `api/app/image_origins.py`
- Modify: `api/Dockerfile`
- Modify: `local_sync/src/sukaseafood_sync/image_origins.py`
- Modify: `local_sync/packaging/suka-seafood-sync.spec`
- Create: `api/tests/test_image_origins.py`
- Create: `local_sync/tests/test_image_origins.py`
- Create: `tests/test_image_origin_contract.py`

**Interfaces:**
- Consumes: `IMAGE_ORIGIN_ALLOWLIST` as an optional comma-separated runtime override.
- Produces: `load_default_image_origins() -> tuple[str, ...]` in API and local packages, both backed by `contracts/image_origins.txt`; defaults include exact `us.aws.cdn.hf.co`.

- [ ] **Step 1: Write failing cross-layer and real-manifest tests**

The root test reads `contracts/image_origins.txt`, API defaults, and local defaults, then asserts exact equality. Load the 1,221-row candidate manifest and validate every preview/original URL. Add a redirect-chain fixture from `huggingface.co` to `us.aws.cdn.hf.co`.

```python
assert api_defaults == local_defaults == contract_patterns
assert len(manifest_rows) == 1221
for row in manifest_rows:
    require_approved_image_url(row.preview_url, contract_patterns)
    require_approved_image_url(row.original_url, contract_patterns)
assert require_approved_image_url("https://us.aws.cdn.hf.co/file.jpg", contract_patterns)
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python -m pytest api/tests/test_image_origins.py local_sync/tests/test_image_origins.py tests/test_image_origin_contract.py -q`

Expected: FAIL because the contract artifact does not exist and the Hugging Face CDN host is rejected.

- [ ] **Step 3: Add and load the canonical contract**

Use one normalized pattern per non-comment line:

```text
.inaturalist.org
inaturalist-open-data.s3.amazonaws.com
caos.boldsystems.org
cdn.floridamuseum.ufl.edu
collections.nmnh.si.edu
huggingface.co
us.aws.cdn.hf.co
pictures.snsb.info
specify.saiab.ac.za
www.morphosource.org
.wikimedia.org
.wikimediausercontent.com
.gbif.org
.fishair.org
.fish-vista.org
.fishvista.org
```

The loader searches a testable explicit `SUKASEAFOOD_CONTRACTS_DIR`, then packaged `_MEIPASS/contracts`, then repository ancestors. It rejects an absent, empty, duplicate, or malformed contract file.

```python
def load_default_image_origins() -> tuple[str, ...]:
    path = resolve_contract_file("image_origins.txt")
    values = tuple(
        line.strip() for line in path.read_text("utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    return normalize_image_origin_allowlist(values)
```

Copy `contracts/` into the API image and add it to PyInstaller `datas` so the frozen local executable uses the same artifact.

- [ ] **Step 4: Run source and packaging contract tests and verify GREEN**

Run: `python -m pytest api/tests/test_image_origins.py local_sync/tests/test_image_origins.py tests/test_image_origin_contract.py -q`

Expected: all selected tests PASS and the 1,221-row audit reports no rejected direct origin.

- [ ] **Step 5: Commit the canonical image-origin contract**

```bash
git add contracts/image_origins.txt api/app/contract_files.py api/app/image_origins.py api/Dockerfile api/tests/test_image_origins.py local_sync/src/sukaseafood_sync/image_origins.py local_sync/packaging/suka-seafood-sync.spec local_sync/tests/test_image_origins.py tests/test_image_origin_contract.py
git commit -m "fix(security): unify approved image origins"
```

---

### Task 2: Generate CSP and run Nginx on a read-only filesystem

**Files:**
- Create: `web/docker-entrypoint.sh`
- Replace: `web/nginx.conf` with `web/nginx.conf.template`
- Modify: `web/Dockerfile`
- Modify: `docker-compose.production.yml`
- Modify: `docker-compose.yml`
- Modify: `tests/test_compose_config.py`
- Create: `tests/test_web_container_behavior.py`

**Interfaces:**
- Consumes: `IMAGE_ORIGIN_ALLOWLIST`; when unset, the entrypoint reads `/opt/sukaseafood/contracts/image_origins.txt`.
- Produces: `/tmp/nginx.conf`, an exact CSP `img-src` host set, writable Nginx PID/client temp paths under `/tmp`, and optional `REVIEW_API_UPSTREAM` development proxying.

- [ ] **Step 1: Write failing config-generation and container-start tests**

Build the Web image, run it with `--read-only --tmpfs /tmp:rw,size=32m,uid=101,gid=101`, wait for `/healthz`, inspect the response CSP, and assert the exact canonical host set including the Hugging Face CDN.

```python
assert response.status_code == 200
assert "https://us.aws.cdn.hf.co" in response.headers["Content-Security-Policy"]
assert container.wait(timeout=20)["StatusCode"] == 0 after stop
```

Run the entrypoint with `IMAGE_ORIGIN_ALLOWLIST=127.0.0.1` and assert it exits non-zero before Nginx starts.

- [ ] **Step 2: Run the focused behavior test and verify RED**

Run: `python -m pytest tests/test_web_container_behavior.py tests/test_compose_config.py -q`

Expected: FAIL because production has no writable `/tmp` and CSP is hardcoded.

- [ ] **Step 3: Implement deterministic entrypoint generation**

The POSIX entrypoint validates comma-separated or file-backed patterns and converts `.example.org` to both `https://example.org` and `https://*.example.org` CSP sources.

```sh
#!/bin/sh
set -eu
policy="${IMAGE_ORIGIN_ALLOWLIST:-}"
if [ -z "$policy" ]; then
  policy="$(grep -Ev '^[[:space:]]*(#|$)' /opt/sukaseafood/contracts/image_origins.txt | paste -sd, -)"
fi
sources=""
old_ifs="$IFS"; IFS=,
for pattern in $policy; do
  host="$(printf '%s' "$pattern" | tr '[:upper:]' '[:lower:]')"
  printf '%s' "$host" | grep -Eq '^\.?[a-z0-9]([a-z0-9.-]*[a-z0-9])?$' || exit 64
  case "$host" in
    .*) root="${host#.}"; sources="$sources https://$root https://*.$root" ;;
    *) sources="$sources https://$host" ;;
  esac
done
IFS="$old_ifs"
sed "s|@@IMAGE_SOURCES@@|$sources|g" /etc/nginx/nginx.conf.template > /tmp/nginx.conf
exec nginx -c /tmp/nginx.conf -g 'daemon off;'
```

Copy the canonical contract into the Web image. Set `pid /tmp/nginx.pid` and `client_body_temp_path /tmp/client_temp` in the template. Mount `/tmp` tmpfs in production. In development, set `REVIEW_API_UPSTREAM=http://review-api:8000` and attach Web to `review-internal`; generate a `/sukaseafood/api/` proxy only when that variable is nonempty.

- [ ] **Step 4: Run Web container and development proxy tests and verify GREEN**

Run: `python -m pytest tests/test_web_container_behavior.py tests/test_compose_config.py -q`

Run: `docker compose -f docker-compose.yml up -d --build` followed by an HTTP request to `http://127.0.0.1:8080/sukaseafood/api/v1/health`, then `docker compose down`.

Expected: `/healthz` and the development same-origin API path both return the expected content; production Web stays healthy with a read-only root filesystem.

- [ ] **Step 5: Commit Web runtime generation**

```bash
git add web/docker-entrypoint.sh web/nginx.conf.template web/Dockerfile docker-compose.yml docker-compose.production.yml tests/test_compose_config.py tests/test_web_container_behavior.py
git commit -m "fix(web): generate CSP in read-only runtime"
```

---

### Task 3: Make candidate import ownership executable

**Files:**
- Modify: `api/Dockerfile`
- Modify: `deploy/scripts/first_deploy.sh`
- Modify: `deploy/scripts/import_candidates_from_windows.ps1`
- Modify: `deploy/OPERATIONS_ZH.md`
- Modify: `tests/test_import_deploy.py`
- Create: `tests/test_import_permissions.py`

**Interfaces:**
- Consumes: fixed runtime UID/GID `10001:10001` and SSH deployer staging under `/tmp`.
- Produces: API-owned mode-0700 imports directory, API-owned mode-0600 CSV, and writable API-owned JSON reports.

- [ ] **Step 1: Write failing ownership and report tests**

Assert Docker image identity, script commands, and a temporary bind directory behave together.

```python
assert image_user == "10001:10001"
assert stat.S_IMODE(import_dir.stat().st_mode) == 0o700
assert csv.stat().st_uid == 10001 and stat.S_IMODE(csv.stat().st_mode) == 0o600
assert json.loads(report.read_text("utf-8"))["can_commit"] is True
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest tests/test_import_deploy.py tests/test_import_permissions.py -q`

Expected: FAIL because the image uses an unfixed system UID and the root-owned 0700 directory blocks the API user.

- [ ] **Step 3: Implement the privileged handoff**

Create the API identity with fixed IDs:

```dockerfile
RUN groupadd --system --gid 10001 review \
    && useradd --system --uid 10001 --gid 10001 --home-dir /app --create-home review
USER 10001:10001
```

`first_deploy.sh` creates imports as `10001:10001` mode 0700. PowerShell uploads to `/tmp`, validates the remote SHA, then executes only this privileged installation boundary:

```powershell
$Install = "sudo install -d -o 10001 -g 10001 -m 0700 '$RemoteImports' && sudo install -o 10001 -g 10001 -m 0600 '$RemoteTemporary' '$RemoteCsv' && rm -f -- '$RemoteTemporary'"
```

The script validates that the final file has owner/group 10001 and mode 600 before running the container. Reports remain inside the same API-owned directory.

- [ ] **Step 4: Run permission behavior tests and verify GREEN**

Run: `python -m pytest tests/test_import_deploy.py tests/test_import_permissions.py -q`

Expected: all selected tests PASS; the production API image can read the installed CSV and create the report as UID 10001.

- [ ] **Step 5: Commit import ownership**

```bash
git add api/Dockerfile deploy/scripts/first_deploy.sh deploy/scripts/import_candidates_from_windows.ps1 deploy/OPERATIONS_ZH.md tests/test_import_deploy.py tests/test_import_permissions.py
git commit -m "fix(ops): hand imports to non-root api"
```

---

### Task 4: Make PostgreSQL restore atomic and failure-safe

**Files:**
- Modify: `deploy/scripts/restore_postgres.sh`
- Modify: `deploy/OPERATIONS_ZH.md`
- Modify: `deploy/RELEASE_CHECKLIST_ZH.md`
- Modify: `tests/test_deploy_scripts.py`
- Create: `tests/test_restore_behavior.py`

**Interfaces:**
- Consumes: explicit canonical backup path and `--confirm-restore`.
- Produces: atomic `pg_restore`, success-only API restart, and a non-zero failure that leaves API stopped.

- [ ] **Step 1: Write failing success/failure script tests**

Place a fake `docker` executable first on `PATH`, record every argument, and select restore failure with `FAKE_PG_RESTORE_EXIT=9`.

```python
failed = run_restore(fake_env | {"FAKE_PG_RESTORE_EXIT": "9"})
assert failed.returncode == 9
assert "stop review-api" in log
assert "up -d review-api" not in log
assert "--single-transaction" in log

succeeded = run_restore(fake_env | {"FAKE_PG_RESTORE_EXIT": "0"})
assert succeeded.returncode == 0
assert log.count("up -d review-api") == 1
assert "production_preflight.sh" in log
```

- [ ] **Step 2: Run the behavior test and verify RED**

Run: `python -m pytest tests/test_restore_behavior.py tests/test_deploy_scripts.py -q`

Expected: FAIL because the current EXIT trap restarts the API after failure and `--single-transaction` is absent.

- [ ] **Step 3: Implement success-only restart**

Remove the restart trap. Keep API stopped throughout restore and compatibility validation.

```sh
"${COMPOSE[@]}" stop review-api
if ! "${COMPOSE[@]}" exec -T review-postgres pg_restore \
  --username=review --dbname=review --clean --if-exists \
  --no-owner --no-privileges --exit-on-error --single-transaction \
  "/backups/$basename"; then
  echo "restore failed; review-api remains stopped" >&2
  echo "after correcting the database or application revision, run: ${COMPOSE[*]} up -d review-api" >&2
  exit 1
fi
"${COMPOSE[@]}" run --rm review-api python -m alembic current --check-heads
"${COMPOSE[@]}" up -d review-api
"$REMOTE_ROOT/deploy/scripts/production_preflight.sh"
```

Document that the operator must select the application revision matching the backup before restarting; source rollback and data restore are separate decisions.

- [ ] **Step 4: Run restore behavior tests and verify GREEN**

Run: `python -m pytest tests/test_restore_behavior.py tests/test_deploy_scripts.py -q`

Expected: all selected tests PASS; failure leaves the API stopped and success restarts once after compatibility validation.

- [ ] **Step 5: Commit atomic restore**

```bash
git add deploy/scripts/restore_postgres.sh deploy/OPERATIONS_ZH.md deploy/RELEASE_CHECKLIST_ZH.md tests/test_deploy_scripts.py tests/test_restore_behavior.py
git commit -m "fix(ops): isolate failed database restore"
```

---

### Task 5: Verify production runtime contracts together

**Files:**
- Modify: `tests/test_compose_config.py`
- Modify: `tests/test_first_deploy.py`
- Modify: `tests/test_public_routes.py`
- Modify: `.superpowers/sdd/2026-08-26-local-training-sync/final-fix-report.md`

**Interfaces:**
- Consumes: Tasks 1-4 artifacts.
- Produces: one reproducible production-runtime acceptance command set and a report that distinguishes local/container verification from live deployment.

- [ ] **Step 1: Add the failing aggregate acceptance assertions**

```python
assert production["services"]["review-web"]["read_only"] is True
assert any(entry.startswith("/tmp:") for entry in production["services"]["review-web"]["tmpfs"])
assert "us.aws.cdn.hf.co" in generated_csp
assert postgres.get("ports") in (None, [])
assert production["services"]["review-api"]["user"] == "10001:10001"
```

- [ ] **Step 2: Run aggregate tests and verify RED if any contract is still missing**

Run: `python -m pytest tests/test_compose_config.py tests/test_first_deploy.py tests/test_public_routes.py -q`

Expected: PASS only when all production-runtime contracts are represented; otherwise the missing assertion fails before documentation is updated.

- [ ] **Step 3: Run full container verification**

Run: `docker compose --env-file deploy/.env.example -f docker-compose.production.yml config --quiet`

Run: `docker compose --env-file deploy/.env.example -f docker-compose.production.yml build review-api review-web`

Run: the disposable Compose acceptance fixture from `tests/test_web_container_behavior.py` and `tests/test_import_permissions.py`.

Expected: config, builds, read-only Web health, API identity, and import report creation all PASS.

- [ ] **Step 4: Update the evidence report with exact outputs**

Record only the commands actually run, their pass counts, image IDs, and the statement “no SSH, production import, Caddy reload, or live-route mutation performed”. Remove the previous “no open issue” assertion.

- [ ] **Step 5: Commit production-runtime evidence**

```bash
git add tests/test_compose_config.py tests/test_first_deploy.py tests/test_public_routes.py .superpowers/sdd/2026-08-26-local-training-sync/final-fix-report.md
git commit -m "test(ops): verify production runtime behavior"
```
