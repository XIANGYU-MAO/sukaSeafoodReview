# Task 5 report — advanced overrides and four-step admin workflow

## RED

`npm test -- --run src/pages/AdminPage.test.tsx src/pages/AdminPage.review.test.tsx src/admin/types.review.test.ts src/deployment.test.ts` failed before implementation. The new strict parser test rejected the four new response fields under the old exact schema; the new admin tests could not find the renamed `采集与导入` tab, four workflow headings, or the advanced source editor.

## GREEN

Species parsing now requires and validates all four nullable source overrides. The editor sends typed create values, patches only changed overrides, and sends JSON `null` when clearing one. The seven-tab admin shell now labels the import tab `采集与导入`, provides the four-step local collector flow, download controls, copy feedback, and species navigation while retaining the existing preview/commit UI in step 4.

## Verification

- `npm test -- --run src/pages/AdminPage.test.tsx src/pages/AdminPage.review.test.tsx src/admin/types.review.test.ts src/deployment.test.ts` — 4 files, 55 tests passed
- `npm run typecheck` — passed
- `git diff --check` — passed

## Commit

`feat(web): guide dynamic candidate collection`

## Concerns

None.
