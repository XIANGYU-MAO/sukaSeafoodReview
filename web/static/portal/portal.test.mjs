import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { JSDOM } from "jsdom";
import { expect, test } from "vitest";

const portalRoot = resolve("static/portal");
const htmlPath = resolve(portalRoot, "index.html");
const scriptPath = resolve(portalRoot, "portal.js");

function loadPortal(storedLocale) {
  expect(existsSync(htmlPath), "portal HTML source must exist").toBe(true);
  expect(existsSync(scriptPath), "portal JavaScript source must exist").toBe(true);
  const dom = new JSDOM(readFileSync(htmlPath, "utf8"), {
    runScripts: "outside-only",
    url: "https://findai.top/sukaseafood/",
  });
  if (storedLocale) {
    dom.window.localStorage.setItem("sukaseafood:portal-locale", storedLocale);
  }
  dom.window.eval(readFileSync(scriptPath, "utf8"));
  dom.window.document.dispatchEvent(new dom.window.Event("DOMContentLoaded"));
  return dom;
}

test("shows the Chinese project introduction and both public tool entrances", () => {
  const dom = loadPortal();
  const document = dom.window.document;

  expect(document.documentElement.lang).toBe("zh-CN");
  expect(document.querySelector("h1")).toHaveTextContent("让海鲜数据真正可用");
  expect(document.querySelector('[data-tool="validator"]')).toHaveAttribute(
    "href",
    "/sukaseafood/validator/",
  );
  expect(document.querySelector('[data-tool="review"]')).toHaveAttribute(
    "href",
    "/sukaseafood/review/",
  );
  expect(document.body).toHaveTextContent("CSV 文件只在你的浏览器中处理，不会上传");
});

test("restores English and persists an immediate switch back to Chinese", () => {
  const dom = loadPortal("en");
  const document = dom.window.document;
  const toggle = document.querySelector("[data-locale-toggle]");

  expect(document.documentElement.lang).toBe("en");
  expect(document.querySelector("h1")).toHaveTextContent("Make seafood data useful");
  expect(toggle).toHaveTextContent("中文");

  toggle.click();

  expect(document.documentElement.lang).toBe("zh-CN");
  expect(document.querySelector("h1")).toHaveTextContent("让海鲜数据真正可用");
  expect(dom.window.localStorage.getItem("sukaseafood:portal-locale")).toBe("zh");
});
