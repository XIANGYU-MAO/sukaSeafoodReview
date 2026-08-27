# Collector Command Guidance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate copyable Windows or Unix first/replenishment collection commands and explain how every parameter affects collection quantity and deduplication.

**Architecture:** Keep command generation as a pure TypeScript function tested independently, then render it in `ImportsTab` with existing pill-style controls and the shared accessible `HelpHint` popup. No collector CLI behavior changes are needed.

**Tech Stack:** React 19, TypeScript, Vitest, Testing Library, CSS.

**Spec:** `docs/superpowers/specs/2026-08-27-import-origin-approval-and-collector-guidance-design.md`

## Global Constraints

- Windows and macOS/Linux are buttons, not a dropdown.
- The quantity is a positive safe integer with a practical UI maximum of 10,000.
- Replenishment always includes `--resume`; first collection never includes it.
- Copy uses the command currently displayed.
- Help is available by mouse hover and keyboard focus.

---

### Task 1: Pure command generation

**Files:**
- Create: `web/src/admin/collectorCommand.ts`
- Create: `web/src/admin/collectorCommand.test.ts`

**Interfaces:**
- Produces: `collectorCommand(platform: "windows" | "unix", mode: "first" | "resume", maxPerSpecies: number) -> string`.

- [ ] **Step 1: Write failing pure tests**

```ts
expect(collectorCommand("windows", "first", 100)).toBe(
  "python .\\collect_fish_images.py --config .\\species_config.json --source all --max-per-species 100",
);
expect(collectorCommand("unix", "resume", 200)).toBe(
  "python ./collect_fish_images.py --config ./species_config.json --source all --max-per-species 200 --resume",
);
expect(() => collectorCommand("unix", "first", 0)).toThrow();
```

- [ ] **Step 2: Run RED test**

Run: `npm test -- src/admin/collectorCommand.test.ts`

Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement the pure function**

Build paths from platform, validate `Number.isSafeInteger(maxPerSpecies) && maxPerSpecies >= 1 && maxPerSpecies <= 10_000`, and append ` --resume` only for resume mode.

- [ ] **Step 4: Run the pure test**

Run: `npm test -- src/admin/collectorCommand.test.ts`

Expected: PASS.

### Task 2: Command controls and parameter help

**Files:**
- Modify: `web/src/admin/HelpHint.tsx`
- Modify: `web/src/admin/ImportsTab.tsx`
- Modify: `web/src/pages/AdminPage.review.test.tsx`
- Modify: `web/src/styles/global.css`

**Interfaces:**
- Consumes: `collectorCommand`.
- Produces: button groups `命令系统` and `采集模式`, number field `每个鱼种每个来源最多`, and parameter help tooltip.

- [ ] **Step 1: Write failing interaction tests**

```tsx
await user.click(screen.getByRole("button", { name: "macOS / Linux" }));
await user.click(screen.getByRole("button", { name: "补充采集" }));
await user.clear(screen.getByRole("spinbutton", { name: "每个鱼种每个来源最多" }));
await user.type(screen.getByRole("spinbutton", { name: "每个鱼种每个来源最多" }), "200");
expect(screen.getByText(/python \.\/collect_fish_images\.py.*200 --resume/)).toBeVisible();
await user.hover(screen.getByRole("button", { name: "参数说明：采集命令" }));
expect(screen.getByRole("tooltip")).toHaveTextContent("每个鱼种、每个来源");
expect(screen.getByRole("tooltip")).toHaveTextContent("数量不够");
```

- [ ] **Step 2: Run RED page test**

Run: `npm test -- src/pages/AdminPage.review.test.tsx`

Expected: FAIL because the controls and parameter help are absent.

- [ ] **Step 3: Extend HelpHint context and render controls**

Allow `context: "字段" | "表头" | "参数"`. Initialize Windows/first/100. Use `aria-pressed` pill buttons, a bounded number input, and derive the displayed/copied command from state.

- [ ] **Step 4: Add concise help content**

The popup must explain that `all` selects all four configured sources, the maximum is per species per source, `--resume` merges with `output/candidates.csv`, and quantity shortfalls are solved by increasing the number and running replenishment before reuploading the complete CSV.

- [ ] **Step 5: Run page tests**

Run: `npm test -- src/pages/AdminPage.review.test.tsx`

Expected: PASS.

### Task 3: Frontend verification

**Files:**
- Modify: `web/src/admin/SpeciesTab.tsx`
- Modify: `web/src/pages/AdminPage.test.tsx`
- Modify: `web/src/styles/global.css`

- [ ] **Step 1: Write failing layout-hook tests**

```tsx
expect(screen.getByRole("checkbox", { name: "启用" }).closest(".admin-check-field")).toHaveClass("admin-check-field");
expect(screen.getByRole("group", { name: "鱼种表单操作" })).toHaveClass("equal-action-row");
expect(screen.getByRole("button", { name: "编辑 KEMBUNG" }).closest("td")).toHaveClass("admin-table-action");
```

- [ ] **Step 2: Run the layout test and verify the table action hook is absent**

Run: `npm test -- src/pages/AdminPage.test.tsx`

Expected: FAIL on the missing centered table action hook.

- [ ] **Step 3: Implement compact checkbox and vertical centering**

Keep checkbox, “启用” and help inside `.admin-check-field`. Add `.admin-table-action` to action cells. Ensure `.equal-action-row > :is(a, button)` and `.admin-table-action > button` use inline flex centering with equal minimum height; set table body cells to `vertical-align: middle`.

- [ ] **Step 4: Run the layout test**

Run: `npm test -- src/pages/AdminPage.test.tsx`

Expected: PASS.

### Task 4: Frontend verification

**Files:**
- Modify only directly failing frontend files.

- [ ] **Step 1: Run all frontend tests**

Run: `npm test`

Expected: PASS.

- [ ] **Step 2: Run typecheck and production build**

Run: `npm run typecheck && npm run build`

Expected: PASS.
