import { describe, expect, it } from "vitest";

import {
  DECISIONS,
  REJECTION_REASONS,
  SOURCES,
  STATUSES,
  decisionLabel,
  rejectionReasonLabel,
  sourceLabel,
  statusLabel,
} from "./catalog";

describe("bilingual stable-code catalog", () => {
  it("translates every known decision, status, source, and rejection reason in both locales", () => {
    for (const locale of ["zh", "en"] as const) {
      for (const code of DECISIONS) expect(decisionLabel(locale, code)).not.toBe(code);
      for (const code of STATUSES) expect(statusLabel(locale, code)).not.toBe(code);
      for (const code of SOURCES) expect(sourceLabel(locale, code)).not.toBe(code);
      for (const code of REJECTION_REASONS) expect(rejectionReasonLabel(locale, code)).not.toBe(code);
    }
  });

  it("has Chinese display labels for the exact backend/import code sets", () => {
    expect(SOURCES).toEqual(["FISH_VISTA", "GBIF", "INATURALIST", "WIKIMEDIA_COMMONS"]);
    expect(REJECTION_REASONS).toEqual([
      "WRONG_SPECIES",
      "NOT_WHOLE_FISH",
      "COOKED_OR_PROCESSED",
      "TOO_OCCLUDED",
      "TOO_SMALL_OR_BLURRY",
      "DUPLICATE",
      "ARTWORK_OR_DIAGRAM",
      "LICENSE_OR_SOURCE_CONCERN",
      "IMAGE_URL_UNAVAILABLE",
      "OTHER",
    ]);
    expect(sourceLabel("zh", "WIKIMEDIA_COMMONS")).toBe("维基共享资源");
    expect(rejectionReasonLabel("zh", "IMAGE_URL_UNAVAILABLE")).toBe("图片链接失效");
  });

  it("renders unknown future codes as localized bounded text", () => {
    const hostile = `<img src=x onerror=alert(1)>${"Z".repeat(100)}`;
    expect(sourceLabel("zh", hostile)).toMatch(/^未知来源/);
    expect(statusLabel("en", hostile)).toMatch(/^Unknown status/);
    expect(sourceLabel("zh", hostile).length).toBeLessThan(60);
    expect(statusLabel("en", hostile).length).toBeLessThan(60);
  });
});
