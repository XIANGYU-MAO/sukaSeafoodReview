# SukaSeafood CV Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add and publish a bilingual browser-based five-class fish identification demo at `/sukaseafood/cv/`.

**Architecture:** Add one static page below the existing portal and run the frozen ONNX model on-device with ONNX Runtime Web/WASM. Reuse the existing portal build, Nginx container and Caddy catch-all; no new backend or route configuration is required.

**Tech Stack:** HTML, CSS, browser JavaScript modules, Canvas 2D, ONNX Runtime Web 1.20.1, Vitest, existing Vite static-asset build.

**Spec:** `docs/superpowers/specs/2026-09-03-sukaseafood-cv-demo.md`

## Global Constraints

- Publish at `https://findai.top/sukaseafood/cv/` through the existing review application deployment.
- Use model version `cv-i1-5class-20260902T174905Z-36ac9b6a-a53adadffa11` only.
- Require ONNX SHA-256 `69f0820c4e200128fb2dced98dcc79112188265714ad0b0d1df582d1af3f4208`.
- Keep photos in browser memory only and show Top-3 plus mandatory human-confirmation language.
- Display canonical UUIDs as `seafood_item_id`, never as `fish_id`.
- Do not add a backend, database, authentication, camera framework or CDN dependency.

---

### Task 1: Ship the frozen model and same-origin runtime assets

**Files:**
- Modify: `web/package.json`
- Modify: `web/package-lock.json`
- Modify: `web/scripts/build-static-assets.mjs`
- Modify: `web/scripts/build-static-assets.test.mjs`
- Create: `web/static/portal/cv/model.onnx`
- Create: `web/static/portal/cv/class_map.json`
- Create: `web/static/portal/cv/preprocessing.json`
- Create: `web/static/portal/cv/model_card.json`

**Interfaces:**
- Consumes: the verified handoff ZIP and `onnxruntime-web@1.20.1/dist`.
- Produces: `/portal/cv/model.onnx`, JSON metadata,
  `/portal/cv/vendor/ort.min.js`,
  `/portal/cv/vendor/ort-wasm-simd-threaded.mjs`, and
  `/portal/cv/vendor/ort-wasm-simd-threaded.wasm` in the built static output.

- [ ] **Step 1: Add the exact build-time runtime dependency**

Run:

```powershell
npm install --save-dev --save-exact onnxruntime-web@1.20.1
```

- [ ] **Step 2: Write a failing runtime-copy test**

Add a test that creates fake portal and ORT directories, runs the real static
builder with `--ort-source`, and asserts literal runtime bytes appear at
`public/portal/cv/vendor/ort.min.js` and
`public/portal/cv/vendor/ort-wasm-simd-threaded.mjs` and
`public/portal/cv/vendor/ort-wasm-simd-threaded.wasm`.

- [ ] **Step 3: Run the focused test and verify RED**

Run:

```powershell
npx vitest run scripts/build-static-assets.test.mjs
```

Expected: FAIL because `--ort-source` is not recognized and no vendor assets are copied.

- [ ] **Step 4: Implement the minimum static-builder copy**

Add an `ortSource` option, copy only these two files after the portal copy, and
raise an error if either file is absent:

```js
const ORT_ASSETS = [
  "ort.min.js",
  "ort-wasm-simd-threaded.mjs",
  "ort-wasm-simd-threaded.wasm",
];
for (const asset of ORT_ASSETS) {
  cpSync(join(ortSource, asset), join(cvVendorOutput, asset));
}
```

- [ ] **Step 5: Run the focused test and verify GREEN**

Run the same Vitest command; expected: all tests in the file pass.

- [ ] **Step 6: Extract only runtime model assets from the verified ZIP**

Copy `model.onnx`, `class_map.json`, `preprocessing.json`, and
`model_card.json` into `web/static/portal/cv/`; reject traversal entries and
verify the model hash after extraction.

### Task 2: Implement and test inference behavior

**Files:**
- Create: `web/static/portal/cv/cv-core.mjs`
- Create: `web/static/portal/cv/cv-core.test.mjs`

**Interfaces:**
- Produces: `softmax(logits)`, `computeResize(width, height, shortSide)`,
  `toNchw(imageData, mean, std)`, and
  `rankPredictions(logits, classes, threshold, limit)`.
- Consumers: `cv.js` in Task 3.

- [ ] **Step 1: Write failing pure-behavior tests**

Use hand-derived literals to verify:

```js
expect(softmax([0, 0])).toEqual([0.5, 0.5]);
expect(computeResize(400, 200, 256)).toEqual({ width: 512, height: 256 });
expect(Array.from(toNchw({ width: 1, height: 1, data: [255, 0, 127, 255] }, [0, 0, 0], [1, 1, 1])))
  .toEqual([1, 0, 127 / 255]);
```

Also verify descending Top-3 order, the 0.3 boundary and preservation of the
literal `seafood_item_id` UUID.

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
npx vitest run static/portal/cv/cv-core.test.mjs
```

Expected: FAIL because `cv-core.mjs` does not exist.

- [ ] **Step 3: Implement the four pure functions**

Use stable softmax (`logit - max`), validate tensor dimensions/channel data,
use floor for aspect-ratio resize, and return `{status, predictions}` where
status is `CANDIDATES` when Top-1 is at least the threshold and
`LOW_CONFIDENCE` otherwise.

- [ ] **Step 4: Run the test and verify GREEN**

Run the same focused test; expected: all cases pass without warnings.

### Task 3: Build the bilingual interaction surface

**Files:**
- Create: `web/static/portal/cv/index.html`
- Create: `web/static/portal/cv/cv.css`
- Create: `web/static/portal/cv/cv.js`
- Create: `web/static/portal/cv/cv-page.test.mjs`

**Interfaces:**
- Consumes: `window.ort`, `cv-core.mjs`, `model.onnx`, and the three JSON files.
- Produces: the user flow defined in the specification.

- [ ] **Step 1: Write a failing page-contract test**

Parse the real HTML in JSDOM and assert the user-visible contract: file input,
`accept="image/jpeg,image/png,image/webp"`, `capture="environment"`, language
toggle, preview, identify button, result region and technical/configuration
sections.

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
npx vitest run static/portal/cv/cv-page.test.mjs
```

Expected: FAIL because `index.html` does not exist.

- [ ] **Step 3: Implement the page and styles**

Create semantic HTML and responsive CSS with the approved ice-market console
direction. Keep the upload control and result region in the first viewport.

- [ ] **Step 4: Implement the browser adapter**

Configure one-thread WASM and one lazily created session:

```js
ort.env.wasm.wasmPaths = "./vendor/";
ort.env.wasm.numThreads = 1;
const session = await ort.InferenceSession.create("./model.onnx", {
  executionProviders: ["wasm"],
  graphOptimizationLevel: "all",
});
```

Validate type and 10 MiB size, decode with EXIF orientation, resize/crop through
Canvas 2D, normalize with `toNchw`, run `session.run`, render Top-3, translate
all visible states, and revoke old object URLs.

- [ ] **Step 5: Run the page test and verify GREEN**

Run the focused test; expected: all cases pass.

### Task 4: Validate and publish

**Files:**
- Verify: complete `web/` build output and Git diff.

- [ ] **Step 1: Run all local checks**

```powershell
npm test
npm run typecheck
npm run build
```

Expected: 0 failed tests, TypeScript exit 0, Vite build exit 0.

- [ ] **Step 2: Verify the built route and artifacts**

Serve `web/dist` locally, request `/portal/cv/`, model, JSON and WASM URLs, and
verify HTTP 200 plus the frozen ONNX hash.

- [ ] **Step 3: Verify model inference independently**

Run the repository's Python ONNX adapter against the two supplied smoke photos
and confirm the expected leading candidates remain Bawal Hitam and Kembung.

- [ ] **Step 4: Review, commit and integrate**

Review the branch diff for scope and secrets, commit the validated source,
merge into local `main`, rerun the deployment preflight, and push `main` because
the production script archives Git HEAD.

- [ ] **Step 5: Publish through the existing application script**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/deploy_from_windows.ps1 -WhatIf
powershell -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/deploy_from_windows.ps1
```

Verify `https://findai.top/sukaseafood/cv/` and its model/runtime assets return
HTTP 200 before reporting completion.
