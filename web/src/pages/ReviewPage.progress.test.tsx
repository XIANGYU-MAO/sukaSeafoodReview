import { StrictMode } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { I18nProvider } from "../i18n/I18nProvider";
import { deferred, jsonResponse } from "../test/helpers";
import { progressFixture, reviewerId } from "../test/task11Fixtures";
import { ReviewPage } from "./ReviewPage";

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

function renderPage(retryBootstrap = vi.fn(async () => undefined), strict = false) {
  const page = (
    <I18nProvider initialLocale="zh">
      <ReviewPage csrfToken="test-csrf-token" reviewerId={reviewerId} retryBootstrap={retryBootstrap} />
    </I18nProvider>
  );
  return { retryBootstrap, ...render(strict ? <StrictMode>{page}</StrictMode> : page) };
}

describe("ReviewPage team progress", () => {
  it("loads aggregate progress without CSRF alongside the current image", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/reviews/current")) return Promise.resolve(jsonResponse(candidate));
      if (url.endsWith("/progress")) return Promise.resolve(jsonResponse(progressFixture));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    renderPage();

    expect(await screen.findByText("Piscis probatio")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "团队进度" })).toBeInTheDocument();
    const progressCall = fetchMock.mock.calls.find(([input]) => String(input).endsWith("/progress"));
    expect(progressCall?.[1]?.method).toBeUndefined();
    expect(new Headers(progressCall?.[1]?.headers).has("X-CSRF-Token")).toBe(false);
  });

  it("refreshes progress only after a validated decision receipt", async () => {
    let currentCalls = 0;
    let progressCalls = 0;
    let decisionCalls = 0;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/reviews/current")) {
        currentCalls += 1;
        return Promise.resolve(currentCalls === 1 ? jsonResponse(candidate) : new Response(null, { status: 204 }));
      }
      if (url.endsWith("/progress")) {
        progressCalls += 1;
        return Promise.resolve(jsonResponse({ ...progressFixture, reviewed: progressCalls === 1 ? 7 : 8 }));
      }
      if (url.endsWith("/decision")) {
        decisionCalls += 1;
        if (decisionCalls === 1) return Promise.resolve(jsonResponse({}, 201));
        const body = JSON.parse(String(init?.body));
        return Promise.resolve(jsonResponse({
          id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          candidate_id: candidate.id,
          reviewer_id: reviewerId,
          ...body,
          whole_fish: "YES",
          exact_species_verified: "YES",
          is_current: true,
          version: 1,
        }, 201));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue("10000000-0000-4000-8000-000000000001");
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Piscis probatio");
    await screen.findByRole("heading", { name: "团队进度" });

    await user.click(screen.getByRole("button", { name: "保留 (K)" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("保存结果无法确认");
    expect(progressCalls).toBe(1);
    await user.click(screen.getByRole("button", { name: "重试保存" }));
    await screen.findByText("暂时没有待审核图片。稍后重试即可。");
    await waitFor(() => expect(progressCalls).toBe(2));
  });

  it("keeps review usable when progress fails and offers a finite retry", async () => {
    let progressCalls = 0;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/reviews/current")) return Promise.resolve(jsonResponse(candidate));
      if (url.endsWith("/progress")) {
        progressCalls += 1;
        return Promise.resolve(progressCalls === 1 ? jsonResponse({}, 503) : jsonResponse(progressFixture));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    }));
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText("Piscis probatio")).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent("无法载入团队进度");
    expect(screen.getByRole("button", { name: "保留 (K)" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "重试进度" }));
    expect(await screen.findByRole("heading", { name: "团队进度" })).toBeInTheDocument();
  });

  it.each([401, 403])("delegates progress status %s to auth bootstrap", async (status) => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/reviews/current")) return Promise.resolve(jsonResponse(candidate));
      if (url.endsWith("/progress")) return Promise.resolve(jsonResponse({}, status));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    }));
    const retryBootstrap = vi.fn(async () => undefined);
    renderPage(retryBootstrap);
    await waitFor(() => expect(retryBootstrap).toHaveBeenCalledTimes(1));
    expect(screen.getByText("Piscis probatio")).toBeInTheDocument();
  });

  it("aborts StrictMode progress replay and ignores its stale result", async () => {
    const stale = deferred<Response>();
    const active = deferred<Response>();
    const signals: AbortSignal[] = [];
    let progressCalls = 0;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/reviews/current")) return Promise.resolve(jsonResponse(candidate));
      if (url.endsWith("/progress")) {
        signals.push(init?.signal as AbortSignal);
        progressCalls += 1;
        return progressCalls === 1 ? stale.promise : active.promise;
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    }));
    renderPage(undefined, true);
    await waitFor(() => expect(progressCalls).toBe(2));
    await act(async () => active.resolve(jsonResponse(progressFixture)));
    expect(await screen.findByText("58.33%")).toBeInTheDocument();
    await act(async () => stale.resolve(jsonResponse({ ...progressFixture, completion_percent: 1 })));
    expect(signals[0].aborted).toBe(true);
    expect(screen.getByText("58.33%")).toBeInTheDocument();
    expect(screen.queryByText("1%")).not.toBeInTheDocument();
  });
});
