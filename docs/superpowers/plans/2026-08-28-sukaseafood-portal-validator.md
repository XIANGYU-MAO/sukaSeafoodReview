# SukaSeafood Portal and CSV Validator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a bilingual SukaSeafood project landing page and the existing browser-local CSV Validator while preserving the current Review and API routes.

**Architecture:** The existing `review-web` image remains the only SukaSeafood web container. A build helper copies the project portal and converts the recovered self-contained Validator into strict-CSP static assets under `/portal/` and `/validator/`; the independent Caddy gateway rewrites public route prefixes to those internal paths.

**Tech Stack:** Static HTML/CSS/JavaScript, Node.js build helper, Vitest + JSDOM, Nginx, Docker Compose, Caddy, Python contract tests.

**Spec:** `docs/superpowers/specs/2026-08-28-sukaseafood-portal-validator-design.md`

## Global Constraints

- Public routes are exactly `/sukaseafood/`, `/sukaseafood/validator/`, `/sukaseafood/review/`, and `/sukaseafood/api/*`.
- Review authentication and backend API behavior must not change.
- CSV contents never leave the browser.
- No third-party runtime assets and no CSP `unsafe-inline` allowances.
- Deploy Review Web before the gateway route change.

---

### Task 1: Package the recovered Validator as strict-CSP static assets

**Files:**
- Restore: `validator.html`
- Create: `web/scripts/build-static-assets.mjs`
- Create: `web/scripts/build-static-assets.test.mjs`
- Modify: `web/package.json`
- Modify: `.gitignore`
- Modify: `web/Dockerfile`
- Modify: `docker-compose.yml`
- Modify: `docker-compose.production.yml`
- Modify: `deploy/scripts/deploy_from_windows.ps1`
- Modify: `tests/test_compose_config.py`

**Interfaces:**
- Consumes: the recovered self-contained root `validator.html`.
- Produces: `buildStaticAssets({ validatorSource, portalSource, publicRoot })`, generated `web/public/validator/index.html`, `validator.css`, and `validator.js`.

- [ ] **Step 1: Write the failing build-helper and Compose contract tests**

  Test a temporary self-contained HTML fixture and assert that generated output has external stylesheet/script references, no inline executable/style blocks, and byte-equivalent extracted CSS/JavaScript. Update the Compose contract to require repository-root build context, `web/Dockerfile`, and deployment archive inclusion of `validator.html`.

- [ ] **Step 2: Run RED**

  Run `npm test -- scripts/build-static-assets.test.mjs` and `python -m pytest tests/test_compose_config.py tests/test_deploy_scripts.py -q`.

  Expected: failures because the helper, root build context, and Validator archive entry do not exist.

- [ ] **Step 3: Restore the captured Validator and implement the minimal build path**

  Recover the exact 35,901-character HTML content from the recorded file deletion, implement deterministic extraction, wire `predev` and `prebuild`, ignore generated public directories, and change both Compose files plus Dockerfile/archive paths so the root source is available during builds.

- [ ] **Step 4: Run GREEN**

  Repeat the two RED commands and run `npm run build`.

  Expected: all targeted tests pass and Vite output contains `dist/validator/index.html`, `validator.css`, and `validator.js`.

### Task 2: Build and verify the bilingual project portal

**Files:**
- Create: `web/static/portal/index.html`
- Create: `web/static/portal/portal.css`
- Create: `web/static/portal/portal.js`
- Create: `web/static/portal/portal.test.mjs`
- Modify: `web/scripts/build-static-assets.mjs`
- Modify: `web/nginx.conf`
- Modify: `tests/test_compose_config.py`

**Interfaces:**
- Consumes: static-build helper from Task 1.
- Produces: a public landing page with locale persistence key `sukaseafood:portal-locale` and links to `/sukaseafood/validator/` and `/sukaseafood/review/`.

- [ ] **Step 1: Write failing portal behavior and Nginx routing tests**

  Execute the real portal script inside JSDOM and assert initial bilingual content, entry URLs, immediate locale switching, and persistence after reinitialization. Extend the Nginx contract to require exact `/portal/` and `/validator/` static locations ahead of the Review SPA fallback.

- [ ] **Step 2: Run RED**

  Run `npm test -- static/portal/portal.test.mjs` and `python -m pytest tests/test_compose_config.py -q`.

  Expected: failures because portal files and Nginx static locations do not exist.

- [ ] **Step 3: Implement the minimal portal and Nginx locations**

  Add accessible semantic HTML, two bilingual tool cards, workflow summary, responsive CSS, a language toggle that updates `lang` and translated nodes, and strict static `try_files` locations for `/portal/` and `/validator/`.

- [ ] **Step 4: Run GREEN and build verification**

  Repeat the RED commands, then run `npm run typecheck` and `npm run build`.

  Expected: tests pass and `dist/portal/` plus `dist/validator/` are complete.

### Task 3: Route, deploy, and verify the public pages

**Files (findai-infra worktree):**
- Modify: `Caddyfile`
- Modify: `deploy/scripts/production_preflight.sh`
- Modify: `tests/test_infra_config.py`
- Modify: `tests/test_deploy_scripts.py`

**Files (review worktree):**
- Modify: `deploy/scripts/production_preflight.sh`
- Modify: `tests/test_public_routes.py`

**Interfaces:**
- Consumes: Review Web internal `/portal/`, `/validator/`, and `/` endpoints.
- Produces: the approved public route contract and content-aware production checks.

- [ ] **Step 1: Write failing adapted-Caddy and preflight tests**

  Assert root canonicalization to `/sukaseafood/`, route ordering API → Validator → Review → portal, internal rewrites `/validator{uri}` and `/portal{uri}`, and content-aware checks for portal, Validator, Review, and API health.

- [ ] **Step 2: Run RED**

  Run `python -m unittest discover -s tests -v` in the infra worktree and targeted Review deployment/public-route tests.

  Expected: failures because root still redirects to Review and Validator has no route.

- [ ] **Step 3: Implement gateway and preflight changes**

  Add canonical redirects, prefix-stripping rewrites to `review-web:8080`, preserve API routing and www-domain behavior, and update both preflight suites.

- [ ] **Step 4: Run GREEN and full local verification**

  Run Review Web tests, typecheck, build, Review Python tests, infra unit tests, Docker Compose config, and Caddy validation.

  Expected: all checks pass without warnings attributable to this change.

- [ ] **Step 5: Commit, push, and deploy in safe order**

  Commit the Review and infra worktrees separately. Push without force. Deploy Review first with its existing Windows script, then deploy the gateway with the infra script.

- [ ] **Step 6: Run public and browser acceptance**

  Verify all canonical redirects, bilingual portal switching, both entry links, Validator local upload/template behavior, existing Review login page, and API health. Record final commit SHAs and live URLs.
