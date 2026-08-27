# Gateway Routing and Release Truth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every SukaSeafood path terminate correctly through the shared YGF gateway, establish one trustworthy proxy-address layer, and leave complete bilingual release evidence before integration.

**Architecture:** YGF remains only the domain gateway: exact bare routes redirect once to the review URL, API/review paths proxy to isolated services, and legacy project paths remain terminal 404s. Uvicorn preserves the raw socket peer so the FastAPI application alone decides whether `X-Forwarded-For` is trusted. Local Caddy and real-socket behavior tests prove routing and proxy trust without touching production.

**Tech Stack:** Caddy 2.10 Alpine, Docker/Compose, FastAPI, Uvicorn, HTTPX, pytest, PowerShell, Git worktrees.

**Spec:** `docs/superpowers/specs/2026-08-27-final-acceptance-remediation-design.md`

## Global Constraints

- YGF changes are limited to `server/deploy/Caddyfile`, `docker-compose.caddy.yml`, `server/scripts/production_preflight.sh`, and removal of the already-scoped `server/deploy/ocean-project/**` files.
- Preserve all unrelated YGF routes and services.
- `/project`, `/project/*`, and `/project-assets/*` return 404 on root and `www` domains.
- `/sukaseafood/review*` reaches review Web and `/sukaseafood/api/*` reaches review API.
- Do not call the live site, SSH, deploy, reload Caddy, push, or merge while implementing this plan.
- Every production change begins with a focused failing behavior test.

---

### Task 1: Terminate the bare SukaSeafood route

**Files:**
- Modify in YGF: `server/deploy/Caddyfile`
- Modify in main repo: `tests/test_ygf_gateway_contract.py`
- Modify in main repo: `tests/test_public_routes.py`
- Create in main repo: `tests/test_ygf_gateway_behavior.py`

**Interfaces:**
- Consumes: `DIANSHU_ROOT_DOMAIN` and `DIANSHU_WEB_DOMAIN` Caddy site blocks.
- Produces: one-hop redirects from both exact `/sukaseafood` routes to `https://findai.top/sukaseafood/review`.

- [ ] **Step 1: Write the failing redirect-order contract**

Parse the Caddyfile and assert exact bare matchers occur before wildcard/fallback handling in both site blocks. The behavior test rewrites the actual Caddyfile into a temporary local-HTTP configuration, starts three loopback stub upstreams, and follows both bare routes.

```python
assert '@sukaSeafoodBare path /sukaseafood' in caddy
assert 'redir @sukaSeafoodBare https://findai.top/sukaseafood/review permanent' in caddy
assert caddy.count('redir @sukaSeafoodBare https://findai.top/sukaseafood/review permanent') == 2
assert caddy.index('@sukaSeafoodBare path /sukaseafood') < caddy.index('reverse_proxy admin-web:8080')
assert follow("root.test", "/sukaseafood") == (200, "REVIEW_WEB")
assert follow("www.test", "/sukaseafood") == (200, "REVIEW_WEB")
assert request("root.test", "/sukaseafood/api/v1/health").text == "REVIEW_API"
assert request("root.test", "/project").status_code == 404
assert request("www.test", "/admin").text == "YGF_WEB"
```

- [ ] **Step 2: Run the focused contract and verify RED**

Run from the main worktree with `YGF_WORKTREE` set to `C:\Users\86166\Desktop\ygf-worktrees\sukaseafood-routing`:

`python -m pytest tests/test_ygf_gateway_contract.py tests/test_ygf_gateway_behavior.py -k "bare or route_behavior" -q`

Expected: FAIL because root and `www` currently redirect bare `/sukaseafood` to each other.

- [ ] **Step 3: Add terminal exact-route handlers**

In each site route, put this before the general SukaSeafood/fallback matcher:

```caddyfile
@sukaSeafoodBare path /sukaseafood
redir @sukaSeafoodBare https://findai.top/sukaseafood/review permanent
```

Keep `www` wildcard SukaSeafood redirecting to the root domain, keep root API/review `handle_path` blocks, and keep legacy 404 handlers first.

- [ ] **Step 4: Validate Caddy syntax and verify GREEN**

Run: `docker run --rm -e DIANSHU_TLS_EMAIL=test@example.com -e DIANSHU_API_DOMAIN=api.test -e DIANSHU_WEB_DOMAIN=www.test -e DIANSHU_ROOT_DOMAIN=findai.test -v "C:/Users/86166/Desktop/ygf-worktrees/sukaseafood-routing/server/deploy/Caddyfile:/etc/caddy/Caddyfile:ro" caddy:2.10-alpine caddy validate --config /etc/caddy/Caddyfile`

Run: `python -m pytest tests/test_ygf_gateway_contract.py tests/test_ygf_gateway_behavior.py -q`

Expected: Caddy validates and all gateway contracts PASS.

- [ ] **Step 5: Commit in the YGF repository**

YGF repository:

```bash
git add server/deploy/Caddyfile
git commit -m "fix(gateway): terminate bare sukaseafood route"
```

Main repository:

```bash
git add tests/test_ygf_gateway_contract.py tests/test_ygf_gateway_behavior.py tests/test_public_routes.py
git commit -m "test(gateway): exercise terminal sukaseafood routes"
```

---

### Task 2: Bound production redirect preflight

**Files:**
- Modify in main repo: `tests/test_ygf_gateway_contract.py`
- Modify in YGF: `server/scripts/production_preflight.sh`

**Interfaces:**
- Consumes: the corrected terminal route from Task 1.
- Produces: production preflight that follows at most three redirects and requires the exact terminal review URL.

- [ ] **Step 1: Write the failing bounded-preflight contract**

Assert both bare-domain checks use `--location --max-redirs 3`, discard the body, capture `%{url_effective}`, and compare exactly with the review URL.

```python
assert script.count("--max-redirs 3") >= 2
assert script.count("%{url_effective}") >= 2
assert 'test "$BARE_FINAL" = "https://findai.top/sukaseafood/review"' in script
assert 'test "$WWW_BARE_FINAL" = "https://findai.top/sukaseafood/review"' in script
```

- [ ] **Step 2: Run the preflight contract and verify RED**

Run: `python -m pytest tests/test_ygf_gateway_contract.py -k "bounded_preflight" -q`

Expected: FAIL because the current preflight does not inspect either bare route or bound redirects.

- [ ] **Step 3: Extend YGF preflight with bounded redirect following**

Add exact bare checks without changing existing YGF checks:

```sh
BARE_FINAL="$(curl --fail --silent --show-error --location --max-redirs 3 --write-out '%{url_effective}' --output /dev/null https://findai.top/sukaseafood)"
test "$BARE_FINAL" = "https://findai.top/sukaseafood/review"
WWW_BARE_FINAL="$(curl --fail --silent --show-error --location --max-redirs 3 --write-out '%{url_effective}' --output /dev/null https://www.findai.top/sukaseafood)"
test "$WWW_BARE_FINAL" = "https://findai.top/sukaseafood/review"
```

- [ ] **Step 4: Run local Caddy behavior and contract suites and verify GREEN**

Run: `python -m pytest tests/test_ygf_gateway_behavior.py tests/test_ygf_gateway_contract.py -q`

Run: `bash -n C:/Users/86166/Desktop/ygf-worktrees/sukaseafood-routing/server/scripts/production_preflight.sh`

Expected: all tests PASS and shell syntax is valid.

- [ ] **Step 5: Commit bounded preflight in each repository**

Main repository:

```bash
git add tests/test_ygf_gateway_contract.py
git commit -m "test(gateway): require bounded redirect preflight"
```

YGF repository:

```bash
git add server/scripts/production_preflight.sh
git commit -m "test(gateway): bound sukaseafood redirects"
```

---

### Task 3: Make FastAPI the sole forwarded-address authority

**Files:**
- Modify: `api/Dockerfile`
- Modify: `docker-compose.yml`
- Modify: `docker-compose.production.yml`
- Modify: `api/tests/test_auth.py`
- Create: `api/tests/integration/test_proxy_socket.py`
- Modify: `api/tests/conftest.py`

**Interfaces:**
- Consumes: raw `request.client.host`, `X-Forwarded-For`, and `TRUSTED_PROXY_CIDRS`.
- Produces: Uvicorn with proxy-header rewriting disabled and application-only `resolve_client_address()` trust decisions.

- [ ] **Step 1: Write the failing real-socket proxy test**

Start Uvicorn on a loopback ephemeral port with a temporary migrated SQLite database and `TRUSTED_PROXY_CIDRS=127.0.0.1/32`. Send repeated failed logins with distinct forwarded addresses and assert the limiter keys on the forwarded address only because the raw peer is trusted. Start a second instance with `TRUSTED_PROXY_CIDRS=192.0.2.0/24` and assert spoofed XFF values share the raw loopback limiter identity.

```python
assert await failed_login(port, "198.51.100.10") == 401
assert await failed_login(port, "198.51.100.11") == 401
assert await limited_login(port, repeated_xff="198.51.100.10") == 429
assert await spoofed_series(untrusted_port, distinct_xff=True) == [401, 401, 401, 401, 401, 429]
```

- [ ] **Step 2: Run the socket test and verify RED**

Run: `python -m pytest tests/integration/test_proxy_socket.py -q`

Expected: FAIL because Uvicorn currently rewrites the peer with `--proxy-headers --forwarded-allow-ips *` before application validation.

- [ ] **Step 3: Disable Uvicorn proxy rewriting**

Use the application layer exclusively:

```dockerfile
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
```

Keep production `TRUSTED_PROXY_CIDRS` equal to the inspected `sukaseafood-edge` subnet and development loopback trust explicit. Do not broaden it to `0.0.0.0/0` or `::/0`.

- [ ] **Step 4: Run unit and real-socket auth tests and verify GREEN**

Run: `python -m pytest tests/test_auth.py tests/integration/test_proxy_socket.py -q`

Expected: all selected tests PASS; untrusted XFF cannot choose the limiter identity.

- [ ] **Step 5: Commit proxy trust correction**

```bash
git add api/Dockerfile docker-compose.yml docker-compose.production.yml api/tests/test_auth.py api/tests/integration/test_proxy_socket.py api/tests/conftest.py
git commit -m "fix(api): preserve peer for proxy trust"
```

---

### Task 4: Close bilingual release documentation and audit both repositories

**Files:**
- Modify: `README.md`
- Modify: `README_ZH.md`
- Modify: `deploy/OPERATIONS_ZH.md`
- Modify: `deploy/RELEASE_CHECKLIST_ZH.md`
- Modify: `local_sync/README_ZH.md`
- Modify: `.superpowers/sdd/2026-08-26-local-training-sync/progress.md`
- Modify: `.superpowers/sdd/2026-08-26-local-training-sync/final-fix-report.md`
- Modify: `api/tests/test_readme_contract.py`
- Create: `tests/test_release_truth.py`

**Interfaces:**
- Consumes: exact results from all three implementation plans.
- Produces: truthful operator instructions, complete review ledger, and no claim that GitHub integration or production deployment already happened.

- [ ] **Step 1: Write failing release-truth assertions**

```python
for document in (readme_en, readme_zh, operations_zh, release_report):
    assert "no open issue" not in document.lower()
assert "候选图片同步代次" in readme_zh
assert "PostgreSQL" in readme_zh and "SQLite" in readme_zh
assert "未执行 SSH、线上导入或 Caddy reload" in release_report
assert "/sukaseafood/review" in operations_zh
assert "/project" in operations_zh and "404" in operations_zh
```

- [ ] **Step 2: Run documentation tests and verify RED**

Run: `python -m pytest api/tests/test_readme_contract.py tests/test_release_truth.py -q`

Expected: FAIL on stale terminology or overstated closure.

- [ ] **Step 3: Update documents and the SDD ledger**

Record the original final review, the rejected first final-fix wave, every new remediation commit, exact test evidence, and all rulings. State clearly:

```text
Application code: sukaSeafoodReview repository.
YGF role: shared findai.top Caddy/Compose/preflight only.
Online database: PostgreSQL.
Local sidecar: SQLite metadata/state only; no image bytes or receipt token.
Live deployment: not yet executed.
```

- [ ] **Step 4: Run complete final verification**

Main repository:

```bash
python -m pytest api/tests -q
npm test --prefix web
npm run typecheck --prefix web
npm run build --prefix web
python -m pytest -q local_sync/tests
python -m pytest -q tests
git diff --check
git status --short
```

With disposable PostgreSQL 16, rerun all API tests with `TEST_POSTGRES_DSN` set and require no skips. Rebuild the Windows onedir executable from the locked environment, run `--version` and `--self-test`, and compare SHA-256 to `local_sync/dist/SHA256SUMS.txt`.

YGF isolated worktree:

```bash
docker compose --env-file server/.env -f docker-compose.cloud.yml -f docker-compose.caddy.yml config --quiet
bash -n server/scripts/production_preflight.sh
git diff --check
git status --short
```

Expected: all tests/builds/validations PASS and both worktrees are clean after evidence commits.

- [ ] **Step 5: Commit final truth and request one independent review**

```bash
git add README.md README_ZH.md deploy/OPERATIONS_ZH.md deploy/RELEASE_CHECKLIST_ZH.md local_sync/README_ZH.md .superpowers/sdd/2026-08-26-local-training-sync/progress.md .superpowers/sdd/2026-08-26-local-training-sync/final-fix-report.md api/tests/test_readme_contract.py tests/test_release_truth.py
git commit -m "docs: record final acceptance evidence"
```

Request one read-only review of the complete remediation ranges in both repositories. Do not merge or push unless that review reports no Critical or Important findings.
