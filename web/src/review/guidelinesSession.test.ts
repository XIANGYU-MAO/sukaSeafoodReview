import { beforeEach, describe, expect, it } from "vitest";

import {
  hasSeenReviewGuidelines,
  markReviewGuidelinesSeen,
  resetReviewGuidelines,
} from "./guidelinesSession";

describe("review guideline session marker", () => {
  beforeEach(() => sessionStorage.clear());

  it("tracks and resets each reviewer independently within the browser session", () => {
    expect(hasSeenReviewGuidelines("reviewer-a")).toBe(false);
    markReviewGuidelinesSeen("reviewer-a");
    expect(hasSeenReviewGuidelines("reviewer-a")).toBe(true);
    expect(hasSeenReviewGuidelines("reviewer-b")).toBe(false);
    resetReviewGuidelines("reviewer-a");
    expect(hasSeenReviewGuidelines("reviewer-a")).toBe(false);
  });
});
