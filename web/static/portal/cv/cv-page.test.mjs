import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { JSDOM } from "jsdom";
import { expect, test } from "vitest";

import { applyLocale, errorKeyForStage, validateFile } from "./cv.js";

const html = readFileSync(resolve("static/portal/cv/index.html"), "utf8");
const css = readFileSync(resolve("static/portal/cv/cv.css"), "utf8");

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
  expect(css).toContain(".upload-control:has(+ input:focus-visible)");
});

test("rejects unsupported and oversized photos before inference", () => {
  expect(validateFile({ type: "image/gif", size: 100 }, 1000)).toBe("invalidType");
  expect(validateFile({ type: "image/jpeg", size: 1001 }, 1000)).toBe("tooLarge");
  expect(validateFile({ type: "image/webp", size: 1000 }, 1000)).toBeNull();
  expect(errorKeyForStage("decode")).toBe("decodeFailed");
  expect(errorKeyForStage("model")).toBe("inferenceFailed");
});
