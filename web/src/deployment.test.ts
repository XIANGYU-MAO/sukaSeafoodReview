import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";

import packageJson from "../package.json";
import viteConfig from "../vite.config";
import { API_BASE, WEB_BASE } from "./api/client";
import { APP_PATHS } from "./App";

describe("deployment paths", () => {
  it("builds assets and browser routes below the canonical review path", () => {
    expect(viteConfig.base).toBe("/sukaseafood/review/");
    expect(WEB_BASE).toBe("/sukaseafood/review/");
    expect(API_BASE).toBe("/sukaseafood/api/v1");
    expect(APP_PATHS.every((path) => !path.includes("/project"))).toBe(true);
  });

  it("runs Vite locally and proxies only the canonical API prefix to FastAPI", () => {
    expect(packageJson.scripts.dev).toBe("vite");
    const proxyKey = "^/sukaseafood/api(?:/|$)";
    const proxy = viteConfig.server?.proxy?.[proxyKey];
    expect(proxy).toEqual(expect.objectContaining({ target: "http://127.0.0.1:8000" }));
    expect(typeof proxy).toBe("object");
    if (typeof proxy !== "object" || proxy === null || typeof proxy.rewrite !== "function") {
      throw new Error("Expected a configured development proxy rewrite");
    }
    const matcher = new RegExp(proxyKey);
    expect(matcher.test("/sukaseafood/api/v1/health")).toBe(true);
    expect(matcher.test("/sukaseafood/api")).toBe(true);
    expect(matcher.test("/sukaseafood/apifoo")).toBe(false);
    expect(matcher.test("/sukaseafood/api-v1")).toBe(false);
    expect(proxy.rewrite("/sukaseafood/api/v1/health")).toBe("/v1/health");
    expect(proxy.rewrite("/sukaseafood/api")).toBe("/");
    expect(proxy.rewrite("/sukaseafood/apifoo")).toBe("/sukaseafood/apifoo");
    expect(proxy.rewrite("/sukaseafood/review/")).toBe("/sukaseafood/review/");
  });

  it("keeps the public CV path when adding the trailing slash", () => {
    const nginx = readFileSync("nginx.conf", "utf8");
    expect(nginx).toContain("absolute_redirect off;");
    expect(nginx).toContain("location = /portal/cv {");
    expect(nginx).toContain("return 308 /sukaseafood/cv/;");
  });

  it("allows the local ONNX runtime, module worker, and photo preview", () => {
    const nginx = readFileSync("nginx.conf", "utf8");
    expect(nginx).toContain("application/javascript mjs;");
    expect(nginx).toContain("script-src 'self' 'wasm-unsafe-eval';");
    expect(nginx).toContain("img-src 'self' blob: data: https:;");
  });
});
