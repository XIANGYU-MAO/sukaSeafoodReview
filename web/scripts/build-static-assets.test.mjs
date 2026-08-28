import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { afterEach, expect, test } from "vitest";

const temporaryDirectories = [];

afterEach(() => {
  for (const directory of temporaryDirectories.splice(0)) {
    rmSync(directory, { recursive: true, force: true });
  }
});

test("builds a self-contained validator into strict-CSP static assets", () => {
  const temporaryDirectory = mkdtempSync(join(tmpdir(), "sukaseafood-static-"));
  temporaryDirectories.push(temporaryDirectory);
  const source = join(temporaryDirectory, "validator.html");
  const output = join(temporaryDirectory, "public");
  writeFileSync(
    source,
    [
      "<!doctype html>",
      '<html lang="en"><head><style>body{color:#123}</style></head>',
      '<body><main id="tool">Validator</main>',
      '<script>window.validatorReady = document.querySelector("#tool").textContent;</script>',
      "</body></html>",
    ].join("\n"),
    "utf8",
  );

  const completed = spawnSync(
    process.execPath,
    [
      resolve("scripts/build-static-assets.mjs"),
      "--validator-source",
      source,
      "--public-root",
      output,
      "--skip-portal",
    ],
    { cwd: resolve("."), encoding: "utf8" },
  );

  expect(completed.status, completed.stderr).toBe(0);
  const html = readFileSync(join(output, "validator/index.html"), "utf8");
  expect(html).toContain('<link rel="stylesheet" href="./validator.css">');
  expect(html).toContain('<script src="./validator.js" defer></script>');
  expect(html).not.toMatch(/<style(?:\s|>)/i);
  expect(html).not.toMatch(/<script>(.|\n)*<\/script>/i);
  expect(readFileSync(join(output, "validator/validator.css"), "utf8")).toBe("body{color:#123}\n");
  expect(readFileSync(join(output, "validator/validator.js"), "utf8")).toBe(
    'window.validatorReady = document.querySelector("#tool").textContent;\n',
  );
});

test("rejects validator HTML with more than one executable script block", () => {
  const temporaryDirectory = mkdtempSync(join(tmpdir(), "sukaseafood-static-"));
  temporaryDirectories.push(temporaryDirectory);
  const source = join(temporaryDirectory, "validator.html");
  writeFileSync(
    source,
    "<!doctype html><style>body{}</style><script>one()</script><script>two()</script>",
    "utf8",
  );

  const completed = spawnSync(
    process.execPath,
    [
      resolve("scripts/build-static-assets.mjs"),
      "--validator-source",
      source,
      "--public-root",
      join(temporaryDirectory, "public"),
      "--skip-portal",
    ],
    { cwd: resolve("."), encoding: "utf8" },
  );

  expect(completed.status).not.toBe(0);
  expect(completed.stderr).toContain("exactly one inline <script>");
});
