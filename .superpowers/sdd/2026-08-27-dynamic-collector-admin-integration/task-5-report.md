# Task 5 report — advanced overrides and four-step admin workflow

## RED

`npm test -- --run src/pages/AdminPage.test.tsx src/pages/AdminPage.review.test.tsx src/admin/types.review.test.ts src/deployment.test.ts` failed before implementation. The new strict parser test rejected the four new response fields under the old exact schema; the new admin tests could not find the renamed `采集与导入` tab, four workflow headings, or the advanced source editor.

## GREEN

Species parsing now requires and validates all four nullable source overrides. The editor sends typed create values, patches only changed overrides, and sends JSON `null` when clearing one. The seven-tab admin shell now labels the import tab `采集与导入`, provides the four-step local collector flow, download controls, copy feedback, and species navigation while retaining the existing preview/commit UI in step 4.

## Verification

- `npx vitest run src/pages/AdminPage.review.test.tsx -t "every listed species is inactive"` — RED before the fix: an all-inactive directory rendered the config link
- `npm test -- --run src/pages/AdminPage.test.tsx src/pages/AdminPage.review.test.tsx src/admin/types.review.test.ts src/deployment.test.ts` — 4 files, 56 tests passed
- `npm run typecheck` — passed
- `git diff --check` — passed
- Final-review RED: `npm test -- --run src/pages/AdminPage.test.tsx src/pages/AdminPage.review.test.tsx src/admin/types.review.test.ts` — 3 files failed, 13 tests failed because API-shaped six-field nested species summaries were parsed as full ten-field species records and Step 1 had no active-species list
- Final-review GREEN: `npm test -- --run src/pages/AdminPage.test.tsx src/pages/AdminPage.review.test.tsx src/admin/types.review.test.ts` — 3 files, 55 tests passed
- Final-review full web: `npm test` — 21 files, 212 tests passed
- Final-review: `npm run typecheck` — passed
- Final-review: `git diff --check` — passed
- Full web RED after Task 7: 210 passed, 1 failed because `App.integration.test.tsx` still expected the retired `导入` tab label
- `npm test -- --run src/App.integration.test.tsx` — 1 file, 5 tests passed
- `npm test` — 21 files, 211 tests passed
- `npm run typecheck` — passed
- `git diff --check` — passed

## Commit

`feat(web): guide dynamic candidate collection`

Follow-up fix: `fix(web): gate collector config on active species`

Follow-up test alignment: `test(web): align collector tab expectation`

Final-review fix: `fix(web): separate species summary contract`

## Concerns

The configuration download is intentionally gated by at least one active species, matching the API export contract. Nested candidate/current/review species are intentionally the API's six-field summaries; full fish records retain all ten fields.
