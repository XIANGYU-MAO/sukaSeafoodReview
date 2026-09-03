import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { JSDOM } from "jsdom";
import { expect, test } from "vitest";

import {
  applyLocale, errorKeyForStage, isActiveRun, runtimeAssetBase, setI18nText, validateFile,
} from "./cv.js";

const html = readFileSync(resolve("static/portal/cv/index.html"), "utf8");
const css = readFileSync(resolve("static/portal/cv/cv.css"), "utf8");
const js = readFileSync(resolve("static/portal/cv/cv.js"), "utf8");

test("offers same-origin upload, camera and inference controls", () => {
  const document = new JSDOM(html).window.document;
  const input = document.querySelector("#photo-input");

  expect(input?.getAttribute("type")).toBe("file");
  expect(input?.getAttribute("accept")).toBe("image/jpeg,image/png,image/webp");
  expect(input?.getAttribute("capture")).toBe("environment");
  expect(document.querySelector("#photo-preview")).not.toBeNull();
  expect(document.querySelector("#identify-button")?.hasAttribute("disabled")).toBe(true);
  expect(document.querySelector("#result-panel")?.getAttribute("aria-live")).toBe("polite");
  expect(document.querySelector("[data-locale-toggle]")).not.toBeNull();
  expect(document.querySelector("#technology")).not.toBeNull();
  expect(document.querySelector("#configuration")).not.toBeNull();
  expect(document.querySelector('[data-i18n="limitTask"]')).not.toBeNull();
  expect(css).toMatch(/\[hidden\]\s*\{\s*display:\s*none\s*!important;/);
  for (const selector of ["privacy-note", "error-message", "confirmation-note"]) {
    expect(css).toMatch(new RegExp(`\\.${selector}\\s*\\{[^}]*font-size:\\s*1rem`));
  }
  expect(js).toContain('imageSmoothingQuality = "low"');

  const assetUrls = [
    ...Array.from(document.querySelectorAll("script[src]"), (node) => node.getAttribute("src")),
    ...Array.from(document.querySelectorAll("link[href]"), (node) => node.getAttribute("href")),
  ];
  expect(assetUrls).toContain("./vendor/ort.min.js");
  expect(assetUrls.every((url) => url?.startsWith("./") || url?.startsWith("/"))).toBe(true);
});

test("switches all primary controls to English", () => {
  const document = new JSDOM(html).window.document;
  applyLocale("en", document);

  expect(document.documentElement.lang).toBe("en");
  expect(document.querySelector("#page-title")?.textContent).toBe("Fish identification demo");
  expect(document.querySelector("#pick-label")?.textContent).toBe("Upload or take a photo");
  expect(document.querySelector("#identify-button")?.textContent).toBe("Identify fish");
  expect(document.querySelector("[data-locale-toggle]")?.textContent).toBe("中文");
  expect(document.querySelector("nav")?.getAttribute("aria-label")).toBe("Page navigation");
  expect(document.querySelector(".workspace")?.getAttribute("aria-label")).toBe("Fish identification workspace");
  expect(document.querySelector("#photo-input")?.getAttribute("aria-label")).toBe("Upload or take a fish photo");
  expect(document.querySelector("#photo-preview")?.getAttribute("alt")).toBe("Fish photo to identify");
  expect(document.querySelector('[data-i18n="decisionCopy"]')?.textContent).toContain("uncalibrated softmax");
  expect(document.querySelector('[data-i18n="limitTask"]')?.textContent).toContain("single-label classifier");
  expect(css).toContain(".upload-control:has(+ input:focus-visible)");

  const modelState = document.querySelector("#model-state");
  setI18nText(modelState, "modelReady", "en");
  expect(modelState?.dataset.i18n).toBe("modelReady");
  expect(modelState?.textContent).toBe("Model config ready");
  applyLocale("zh", document);
  expect(modelState?.textContent).toBe("模型配置就绪");
});

test("rejects unsupported and oversized photos before inference", () => {
  expect(validateFile({ type: "image/gif", size: 100 }, 1000)).toBe("invalidType");
  expect(validateFile({ type: "image/jpeg", size: 1001 }, 1000)).toBe("tooLarge");
  expect(validateFile({ type: "image/webp", size: 1000 }, 1000)).toBeNull();
  expect(errorKeyForStage("decode")).toBe("decodeFailed");
  expect(errorKeyForStage("model")).toBe("inferenceFailed");
  expect(runtimeAssetBase("https://example.test/portal/cv/cv.js")).toBe("https://example.test/portal/cv/vendor/");
  const run = {};
  expect(isActiveRun(run, run)).toBe(true);
  expect(isActiveRun(run, {})).toBe(false);
});
