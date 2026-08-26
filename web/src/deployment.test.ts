import { describe, expect, it } from "vitest";

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
    const proxy = viteConfig.server?.proxy?.["/sukaseafood/api"];
    expect(proxy).toEqual(expect.objectContaining({ target: "http://127.0.0.1:8000" }));
    expect(typeof proxy).toBe("object");
    if (typeof proxy !== "object" || proxy === null || typeof proxy.rewrite !== "function") {
      throw new Error("Expected a configured development proxy rewrite");
    }
    expect(proxy.rewrite("/sukaseafood/api/v1/health")).toBe("/v1/health");
    expect(proxy.rewrite("/sukaseafood/review/")).toBe("/sukaseafood/review/");
  });
});
