# Final acceptance remediation design

Date: 2026-08-27

Status: approved by user

## Purpose

Close the final independent review findings before integrating the collaborative review system into `main`, pushing it to GitHub, or deploying it. The remediation must preserve the fixed 16-column CSV and seven-field receipt formats, keep images off the China server, retain PostgreSQL online and SQLite local boundaries, and limit YGF changes to the shared `findai.top` gateway.

## Scope and repositories

The application, PostgreSQL schema, React UI, deployment assets, and Windows training-set synchronizer remain in `sukaSeafoodReview`. The YGF repository changes only its Caddy route, gateway network attachment, legacy `/project` removal, and production preflight checks. No review business logic or review data moves into YGF.

The work closes every Important finding and the five Minor findings from the final scoped review. Live deployment, secret creation, production import, Caddy reload, and public acceptance remain separate external actions performed only after the code is approved and the user authorizes them.

## 1. Monotonic synchronization generation

The wire column remains named `review_version` for compatibility, but its documented meaning becomes **candidate synchronization generation**. New exports continue to put `Candidate.version` in that column.

A new Alembic migration will make the semantic transition safe:

1. Acquire a database lock covering export creation and candidate-generation migration.
2. For every candidate, calculate the greatest historical value appearing in its reviews, review revisions where applicable, and export items.
3. Set `Candidate.version` to a value strictly greater than that maximum and its current value, with an explicit check against the database column's integer upper bound.
4. Expire all pending export batches created under the former semantic epoch so they cannot mix old and new generation meanings.
5. Record the transition deterministically and make the migration idempotent through normal Alembic revision tracking.

Because every historical local value originated from a server review/export row, the migrated server generation is greater than every legitimate pre-upgrade local SQLite generation. The local schema does not need destructive migration; the first post-upgrade batch naturally supersedes its old state.

The prior export-chunking migration downgrade will expire or otherwise deterministically reconcile duplicate pending batches per scope before recreating the old unique partial index. A populated upgrade/downgrade test will prove the behavior.

## 2. Same-path original replacement

When the original content changes but the decoded suffix and target path stay the same, the server will retain `previous_relative_path` equal to the target path instead of erasing the relationship.

The Windows synchronizer will support a managed same-path replacement only when all of these are true:

- SQLite says the candidate's latest present generation owns that exact path;
- the on-disk regular file has the exact SHA-256 recorded for that prior generation;
- the incoming generation is strictly newer;
- the downloaded staging file has already passed URL, image-decode, size, hash, and path validation.

Under the existing root lock, the synchronizer will write a recovery intent, move the verified prior file to the batch-specific recovery/removed area without clobbering, atomically install the staged file, update SQLite and canonical state, and clear the intent. Recovery must converge after interruption at every boundary. An unmanaged or modified target remains `TARGET_CONFLICT`; the change does not grant a general overwrite capability.

Tests will cover JPG-to-different-JPG success, user-modified target rejection, cancellation before and after the swap boundary, crash recovery, stale batch replay, and concurrent root writers.

## 3. One image-origin policy across API, local tool, and Web

The audited default policy will add the actual Hugging Face redirect boundary `us.aws.cdn.hf.co` while keeping HTTPS-only, port 443, no credentials, no IP literals, no localhost, and per-redirect-hop validation.

One canonical origin list will be stored as a repository data artifact. API and local-sync defaults will load the same artifact; packaging tests will prove it is included in the frozen Windows build. Production may extend or replace it using the existing environment setting.

The Web Nginx configuration will be generated at container startup from the same normalized production environment input. The generator will reject malformed host patterns rather than emitting permissive CSP. It will write the generated configuration only to a bounded `/tmp` tmpfs, then start Nginx from that file. This also supplies the writable PID and client temporary directories required by the read-only container.

Tests will parse the real 1,221-row manifest, validate every direct origin, follow audited redirect fixtures including the Hugging Face CDN boundary, compare API/local/CSP host sets, and start and health-check the production Web container with `read_only: true`.

## 4. Production filesystem and import ownership

The production Web service will mount `/tmp` as a size-bounded tmpfs owned by the Nginx runtime UID/GID. No writable application files will be added to the image layer.

Candidate import will use an explicit handoff:

1. SCP uploads to a unique `/tmp` file owned by the SSH deployer.
2. A narrow privileged `install` step copies the verified file into `/opt/sukaseafood-review/imports` as the fixed API UID/GID with mode `0600`.
3. The imports directory is owned by that fixed API UID/GID and mode `0700`, allowing the non-root API container to read the CSV and write reports.
4. Temporary uploads are removed on success and best-effort cleanup is attempted on failure.

The Dockerfile will keep the API UID/GID stable and tests will assert the Compose and script agreement. A behavior test will exercise the handoff and report creation with the production image rather than only matching script text.

## 5. Atomic restore and failure isolation

Restore will stop the API before touching PostgreSQL and use `pg_restore --single-transaction --clean --if-exists --exit-on-error` for an all-or-nothing database change. The script will restart the API only after restore success and a schema/application compatibility step succeeds. On any failure it will leave the API stopped, print the explicit recovery command, and return non-zero.

The operations guide will require checking out/deploying the application revision that matches the selected backup before restart, and will distinguish data restore from source rollback. Tests will inject a failing restore command and assert that the API is not restarted; the success path will assert exactly one restart followed by preflight.

## 6. Gateway terminal routing

The root-domain Caddy route will handle exact `/sukaseafood` before the general fallback and redirect it once to `https://findai.top/sukaseafood/review`. The `www` route will redirect exact `/sukaseafood` directly to that same terminal URL. Existing review/API handlers remain ordered before the YGF fallback, and `/project`, `/project/*`, and `/project-assets/*` remain 404 on both domains.

An adapted-Caddy behavior test will run a local Caddy instance with stub upstreams, follow redirects, and verify:

- bare `/sukaseafood` terminates at the review app;
- `/sukaseafood/review` reaches the review stub;
- `/sukaseafood/api/v1/health` reaches the API stub;
- all legacy project paths return 404;
- unrelated YGF fallback paths still reach the existing Web stub.

## 7. Development routing and proxy trust

Development Compose will make its published Web port usable by adding a development-only Nginx proxy for `/sukaseafood/api/` to `review-api:8000`. Production remains routed through Caddy.

Proxy trust will have one authoritative layer. Uvicorn will accept forwarded headers only from the expected directly connected proxy network rather than `*`; application logic will evaluate the resulting trusted peer/header contract consistently. A real-socket integration test will send spoofed and proxy-originated forwarded addresses through Uvicorn and assert the login-throttling identity used by the API.

## 8. Documentation and operational truth

English and Chinese root READMEs, the Chinese local-sync guide, operations guide, release checklist, and final evidence report will use “candidate synchronization generation” consistently. They will state which artifacts were behavior-tested and will not claim that live integration or deployment has occurred.

SQLite will be described only as a local sidecar containing synchronization generations, hashes, paths, and recovery state. PostgreSQL remains the only online application database, and neither database stores original image bytes.

## Test and acceptance strategy

Every production-code change begins with a focused failing regression test and follows red-green-refactor. Required final evidence is:

- API unit tests plus the complete disposable PostgreSQL 16 suite, including populated migrations and upgrade from pre-fix server state;
- local-sync unit, real-filesystem, recovery, stale-replay, cancellation, and real-process concurrency suites, including upgrade from a pre-fix SQLite fixture;
- Web tests, typecheck, production build, and production read-only container health check;
- exact 1,221-row origin audit and Hugging Face redirect contract;
- production Compose config plus behavior tests for import permissions and restore failure;
- adapted-Caddy redirect/proxy/legacy behavior tests in the isolated YGF worktree;
- Windows locked build, frozen self-test, version, and SHA-256 manifest;
- clean worktrees and `git diff --check` in both repositories.

Only after a fresh independent review returns no Critical or Important findings will the application branch be merged into the root `main` checkout and pushed to `origin`. The YGF gateway commit will be integrated separately. Production deployment remains a subsequent explicitly authorized operation.
