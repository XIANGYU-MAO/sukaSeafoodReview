import { describe, expect, it } from "vitest";

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
});
