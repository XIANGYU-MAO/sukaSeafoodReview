import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { I18nProvider } from "../i18n/I18nProvider";
import { jsonResponse } from "../test/helpers";
import { reviewerId } from "../test/task11Fixtures";
import { ReviewPage } from "./ReviewPage";
import { markReviewGuidelinesSeen } from "../review/guidelinesSession";

const candidate = {
  id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  species: { code: "SF001", name_zh: "测试鱼", name_en: "Test fish", scientific_name: "Piscis probatio" },
  source_dataset: "INATURALIST",
  source_record_id: "obs:1/photo:10",
  preview_url: "https://images.example.test/preview.jpg",
  original_url: "https://images.example.test/original.jpg",
  source_url: "https://source.example.test/record/1",
  creator: "Ada",
  license: "CC-BY-NC",
  license_url: null,
  attribution: "Ada / iNaturalist",
  location: null,
  observed_on: null,
  metadata: {},
};

beforeEach(() => {
  sessionStorage.clear();
  markReviewGuidelinesSeen(reviewerId);
});

describe("ReviewPage progress isolation", () => {
  it("does not fetch or render team progress on the review route", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/reviews/current")) return Promise.resolve(jsonResponse(candidate));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <I18nProvider initialLocale="zh">
        <ReviewPage
          csrfToken="test-csrf-token"
          reviewerId={reviewerId}
          retryBootstrap={vi.fn(async () => undefined)}
        />
      </I18nProvider>,
    );

    expect(await screen.findByText("Piscis probatio")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "团队进度" })).not.toBeInTheDocument();
    await waitFor(() => {
      expect(fetchMock.mock.calls.every(([input]) => !String(input).endsWith("/progress"))).toBe(true);
    });
  });
});
