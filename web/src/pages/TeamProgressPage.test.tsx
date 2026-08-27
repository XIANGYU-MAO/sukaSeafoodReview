import { StrictMode } from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { I18nProvider } from "../i18n/I18nProvider";
import { deferred, jsonResponse } from "../test/helpers";
import { progressFixture } from "../test/task11Fixtures";
import { TeamProgressPage } from "./TeamProgressPage";

function renderPage(retryBootstrap = vi.fn(async () => undefined), strict = false) {
  const page = (
    <I18nProvider initialLocale="zh">
      <TeamProgressPage retryBootstrap={retryBootstrap} />
    </I18nProvider>
  );
  return { retryBootstrap, ...render(strict ? <StrictMode>{page}</StrictMode> : page) };
}

describe("TeamProgressPage", () => {
  it("loads aggregate progress without CSRF", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/progress")) return Promise.resolve(jsonResponse(progressFixture));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    renderPage();

    expect(await screen.findByRole("heading", { name: "团队进度" })).toBeInTheDocument();
    expect(screen.getByText("58.33%")).toBeInTheDocument();
    const progressCall = fetchMock.mock.calls.find(([input]) => String(input).endsWith("/progress"));
    expect(progressCall?.[1]?.method).toBeUndefined();
    expect(new Headers(progressCall?.[1]?.headers).has("X-CSRF-Token")).toBe(false);
  });

  it("offers a finite manual retry after a progress failure", async () => {
    let progressCalls = 0;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/progress")) {
        progressCalls += 1;
        return Promise.resolve(progressCalls === 1 ? jsonResponse({}, 503) : jsonResponse(progressFixture));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    }));
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("无法载入团队进度");
    expect(progressCalls).toBe(1);
    await user.click(screen.getByRole("button", { name: "重试进度" }));
    expect(await screen.findByRole("heading", { name: "团队进度" })).toBeInTheDocument();
    expect(progressCalls).toBe(2);
  });

  it.each([401, 403])("delegates progress status %s to auth bootstrap", async (status) => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/progress")) return Promise.resolve(jsonResponse({}, status));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    }));
    const retryBootstrap = vi.fn(async () => undefined);
    renderPage(retryBootstrap);

    await waitFor(() => expect(retryBootstrap).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("aborts StrictMode replay and ignores its stale result", async () => {
    const stale = deferred<Response>();
    const active = deferred<Response>();
    const signals: AbortSignal[] = [];
    let progressCalls = 0;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
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
