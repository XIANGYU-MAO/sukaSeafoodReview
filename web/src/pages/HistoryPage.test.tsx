import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { I18nProvider } from "../i18n/I18nProvider";
import { deferred, jsonResponse } from "../test/helpers";
import { historyFixture, historyItem, reviewerId } from "../test/task11Fixtures";
import { HistoryPage } from "./HistoryPage";

function renderPage(retryBootstrap = vi.fn(async () => undefined), locale: "zh" | "en" = "zh") {
  return {
    retryBootstrap,
    ...render(
      <I18nProvider initialLocale={locale}>
        <HistoryPage csrfToken="test-csrf-token" reviewerId={reviewerId} retryBootstrap={retryBootstrap} />
      </I18nProvider>,
    ),
  };
}

function editedReview(overrides: Record<string, unknown> = {}) {
  return {
    id: historyItem.id,
    candidate_id: historyItem.candidate_id,
    reviewer_id: reviewerId,
    decision: "APPROVED",
    rejection_reason: null,
    notes: null,
    whole_fish: "YES",
    exact_species_verified: "YES",
    is_current: true,
    version: 4,
    ...overrides,
  };
}

describe("HistoryPage private filters and paging", () => {
  it("centers the initial menu loading indicator in the viewport", () => {
    vi.stubGlobal("fetch", vi.fn(() => deferred<Response>().promise));
    renderPage();

    expect(screen.getByRole("status", { name: "正在载入历史记录…" }))
      .toHaveClass("page-loading-overlay");
  });

  it("uses localized native dropdowns, URLSearchParams, offset reset, paging, and safe dates", async () => {
    const calls: string[] = [];
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      calls.push(url);
      return Promise.resolve(jsonResponse({ ...historyFixture, total: 25 }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByRole("heading", { name: "我的审核历史" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "鱼种" })).toHaveDisplayValue("全部鱼种");
    expect(screen.getByRole("combobox", { name: "来源" })).toHaveTextContent("维基共享资源");
    expect(screen.getByRole("combobox", { name: "结果" })).toHaveTextContent("已保留");
    expect(screen.queryByRole("radiogroup", { name: /鱼种|来源|结果/ })).not.toBeInTheDocument();
    expect(screen.getByLabelText("结束日期")).toHaveAttribute("max", "9998-12-31");
    expect(calls[0]).toBe("/sukaseafood/api/v1/history?limit=20&offset=0");
    expect(calls[0]).not.toContain("reviewer");

    await user.click(screen.getByRole("button", { name: "下一页" }));
    await waitFor(() => expect(calls.at(-1)).toContain("offset=20"));
    await user.selectOptions(screen.getByRole("combobox", { name: "来源" }), "WIKIMEDIA_COMMONS");
    await user.selectOptions(screen.getByRole("combobox", { name: "结果" }), "REJECTED");
    await user.type(screen.getByLabelText("开始日期"), "2026-08-01");
    await user.type(screen.getByLabelText("结束日期"), "2026-08-26");
    await user.click(screen.getByRole("button", { name: "应用筛选" }));
    await waitFor(() => expect(calls.at(-1)).toBe(
      "/sukaseafood/api/v1/history?source_dataset=WIKIMEDIA_COMMONS&decision=REJECTED&date_from=2026-08-01&date_to=2026-08-26&limit=20&offset=0",
    ));
    expect(calls.at(-1)).not.toContain("reviewer");

    await user.clear(screen.getByLabelText("开始日期"));
    await user.type(screen.getByLabelText("开始日期"), "2026-08-27");
    await user.click(screen.getByRole("button", { name: "应用筛选" }));
    expect(screen.getByRole("alert")).toHaveTextContent("开始日期不能晚于结束日期");
    expect(fetchMock).toHaveBeenCalledTimes(3);
    await user.click(screen.getByRole("button", { name: "重置筛选" }));
    await waitFor(() => expect(calls.at(-1)).toBe("/sukaseafood/api/v1/history?limit=20&offset=0"));
  });

  it("ignores out-of-order filter results and recovers an empty page after edits", async () => {
    const stale = deferred<Response>();
    const active = deferred<Response>();
    let historyCalls = 0;
    const signals: AbortSignal[] = [];
    vi.stubGlobal("fetch", vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      historyCalls += 1;
      signals.push(init?.signal as AbortSignal);
      if (historyCalls === 1) return Promise.resolve(jsonResponse({ ...historyFixture, total: 25 }));
      if (historyCalls === 2) return stale.promise;
      if (historyCalls === 3) return active.promise;
      return Promise.resolve(jsonResponse(historyFixture));
    }));
    const user = userEvent.setup();
    renderPage();
    await screen.findByText(/page:1:File:Fish\.jpg/);
    await user.selectOptions(screen.getByRole("combobox", { name: "结果" }), "APPROVED");
    await user.click(screen.getByRole("button", { name: "应用筛选" }));
    await user.selectOptions(screen.getByRole("combobox", { name: "结果" }), "UNSURE");
    await user.click(screen.getByRole("button", { name: "应用筛选" }));
    await act(async () => active.resolve(jsonResponse({ ...historyFixture, items: [{ ...historyItem, decision: "UNSURE", rejection_reason: null }] })));
    expect(await screen.findByText("不确定", { selector: ".history-decision" })).toBeInTheDocument();
    await act(async () => stale.resolve(jsonResponse({ ...historyFixture, items: [{ ...historyItem, decision: "APPROVED", rejection_reason: null }] })));
    expect(signals[1].aborted).toBe(true);
    expect(screen.queryByText("已保留", { selector: ".history-decision" })).not.toBeInTheDocument();
  });

  it("moves to the last valid page when an edit leaves the current page empty", async () => {
    const urls: string[] = [];
    let getCalls = 0;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/history?")) {
        urls.push(url);
        getCalls += 1;
        if (getCalls <= 2) return Promise.resolve(jsonResponse({ ...historyFixture, total: 21 }));
        if (getCalls === 3) return Promise.resolve(jsonResponse({ ...historyFixture, total: 20, items: [] }));
        return Promise.resolve(jsonResponse({ ...historyFixture, total: 20 }));
      }
      return Promise.resolve(jsonResponse(editedReview({ decision: "UNSURE", whole_fish: "REVIEW", exact_species_verified: "REVIEW" })));
    }));
    const user = userEvent.setup();
    renderPage();
    await screen.findByText(/page:1:File:Fish\.jpg/);
    await user.click(screen.getByRole("button", { name: "下一页" }));
    await waitFor(() => expect(urls.at(-1)).toContain("offset=20"));
    await user.click(screen.getByRole("button", { name: "编辑" }));
    await user.click(screen.getByRole("button", { name: "不确定" }));
    await user.click(screen.getByRole("button", { name: "保存修改" }));

    await waitFor(() => expect(urls.at(-1)).toBe("/sukaseafood/api/v1/history?limit=20&offset=0"));
    expect(await screen.findByText(/page:1:File:Fish\.jpg/)).toBeInTheDocument();
  });

  it.each([401, 403])("delegates history status %s to auth bootstrap", async (status) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({}, status)));
    const retryBootstrap = vi.fn(async () => undefined);
    renderPage(retryBootstrap);
    await waitFor(() => expect(retryBootstrap).toHaveBeenCalledTimes(1));
  });
});

describe("HistoryPage links, privacy, and editing", () => {
  it("keeps approved, rejected, and unsure result tags visually distinct", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({
      ...historyFixture,
      total: 3,
      items: [
        { ...historyItem, decision: "APPROVED", rejection_reason: null },
        {
          ...historyItem,
          id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
          candidate_id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
          decision: "REJECTED",
          rejection_reason: "NOT_A_FISH",
          whole_fish: "NO",
          exact_species_verified: "NO",
        },
        {
          ...historyItem,
          id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
          candidate_id: "ffffffff-ffff-4fff-8fff-ffffffffffff",
          decision: "UNSURE",
          rejection_reason: null,
          whole_fish: "REVIEW",
          exact_species_verified: "REVIEW",
        },
      ],
    })));
    renderPage();

    expect(await screen.findByText("已保留", { selector: ".history-decision" }))
      .toHaveClass("history-decision--approved");
    expect(screen.getByText("已拒绝", { selector: ".history-decision" }))
      .toHaveClass("history-decision--rejected");
    expect(screen.getByText("不确定", { selector: ".history-decision" }))
      .toHaveClass("history-decision--unsure");
  });

  it("shows direct safe links/lazy thumbnail and never exposes editing for stale rows", async () => {
    const staleItem = {
      ...historyItem,
      id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      is_current: false,
      read_only: true,
      version: 1,
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ...historyFixture, total: 2, items: [historyItem, staleItem] }));
    vi.stubGlobal("fetch", fetchMock);
    renderPage();

    const rows = await screen.findAllByRole("article");
    expect(rows).toHaveLength(2);
    expect(screen.getAllByRole("img")[0]).toHaveAttribute("src", historyItem.preview_url);
    expect(screen.getAllByRole("img")[0]).toHaveAttribute("loading", "lazy");
    expect(screen.getAllByRole("link", { name: "来源页面" })[0]).toHaveAttribute("href", historyItem.source_url);
    expect(screen.getAllByRole("link", { name: "原图" })[0]).toHaveAttribute("href", historyItem.original_url);
    expect(within(rows[0]).getByRole("button", { name: "编辑" })).toBeInTheDocument();
    expect(within(rows[1]).queryByRole("button", { name: "编辑" })).not.toBeInTheDocument();
    expect(within(rows[1]).getByText("此记录不是当前结果，只能查看。")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalledWith(historyItem.preview_url, expect.anything());
  });

  it("keeps the existing card layout and opens the complete image in a dismissible lightbox", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(historyFixture)));
    const user = userEvent.setup();
    renderPage();

    const card = await screen.findByRole("article");
    expect(card).toHaveClass("history-card");
    expect(card.querySelector(".history-card__body")).toBeInTheDocument();
    const opener = within(card).getByRole("button", { name: "查看完整图片" });
    expect(opener).toHaveClass("history-thumbnail-viewer");
    expect(within(opener).getByRole("img")).toHaveAttribute("src", historyItem.preview_url);
    expect(opener.querySelector(".history-enlarge-icon")).toBeInTheDocument();

    await user.click(opener);
    const dialog = screen.getByRole("dialog", { name: "完整图片" });
    expect(within(dialog).getByRole("img")).toHaveAttribute("src", historyItem.original_url);
    await user.click(within(dialog).getByRole("img"));
    expect(dialog).toBeInTheDocument();
    fireEvent.mouseDown(dialog);
    expect(screen.queryByRole("dialog", { name: "完整图片" })).not.toBeInTheDocument();

    await user.click(opener);
    await user.click(screen.getByRole("button", { name: "关闭完整图片" }));
    expect(screen.queryByRole("dialog", { name: "完整图片" })).not.toBeInTheDocument();

    await user.click(opener);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "完整图片" })).not.toBeInTheDocument();
  });

  it("sends one versioned CSRF PATCH and claims success only after validation and refetch", async () => {
    const save = deferred<Response>();
    let getCalls = 0;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/history?")) {
        getCalls += 1;
        return Promise.resolve(jsonResponse(getCalls === 1 ? historyFixture : {
          ...historyFixture,
          items: [{ ...historyItem, decision: "APPROVED", rejection_reason: null, notes: null, version: 4, updated_at: "2026-08-26T12:45:00Z" }],
        }));
      }
      if (url.endsWith(`/history/${historyItem.id}`)) return save.promise;
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "编辑" }));
    await user.click(screen.getByRole("button", { name: "保留" }));
    const submit = screen.getByRole("button", { name: "保存修改" });
    fireEvent.click(submit);
    fireEvent.click(submit);

    const patchCalls = fetchMock.mock.calls.filter(([input]) => String(input).endsWith(`/history/${historyItem.id}`));
    expect(patchCalls).toHaveLength(1);
    expect(JSON.parse(String(patchCalls[0][1]?.body))).toEqual({
      version: 3,
      decision: "APPROVED",
      rejection_reason: null,
      notes: null,
    });
    expect(new Headers(patchCalls[0][1]?.headers).get("X-CSRF-Token")).toBe("test-csrf-token");
    expect(screen.queryByText("修改已保存")).not.toBeInTheDocument();
    await act(async () => save.resolve(jsonResponse(editedReview())));
    expect(await screen.findByText("修改已保存")).toBeInTheDocument();
    expect(await screen.findByText(/2026-08-26/)).toBeInTheDocument();
  });

  it("requires OTHER notes and preserves the exact draft across transient failure", async () => {
    let patchCalls = 0;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/history?")) return Promise.resolve(jsonResponse(historyFixture));
      if (url.endsWith(`/history/${historyItem.id}`)) {
        patchCalls += 1;
        return patchCalls === 1
          ? Promise.reject(new TypeError("offline"))
          : Promise.resolve(jsonResponse(editedReview({
              decision: "REJECTED",
              rejection_reason: "OTHER",
              notes: "unusual damage",
              whole_fish: "REVIEW",
              exact_species_verified: "REVIEW",
            })));
      }
      return Promise.reject(new Error(`Unexpected request: ${url} ${init?.method}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "编辑" }));
    await user.click(screen.getByRole("button", { name: "拒绝" }));
    await user.click(screen.getByRole("radio", { name: "其他" }));
    await user.click(screen.getByRole("button", { name: "保存修改" }));
    expect(screen.getByRole("alert")).toHaveTextContent("请填写其他原因");
    expect(patchCalls).toBe(0);
    await user.type(screen.getByRole("textbox", { name: "其他原因备注" }), "  unusual damage  ");
    await user.click(screen.getByRole("button", { name: "保存修改" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("修改失败");
    expect(screen.getByRole("textbox", { name: "其他原因备注" })).toHaveValue("  unusual damage  ");
    await user.click(screen.getByRole("button", { name: "重试修改" }));
    await waitFor(() => expect(patchCalls).toBe(2));
    const bodies = fetchMock.mock.calls
      .filter(([input]) => String(input).endsWith(`/history/${historyItem.id}`))
      .map(([, init]) => init?.body);
    expect(new Set(bodies)).toHaveLength(1);
    expect(JSON.parse(String(bodies[0]))).toEqual({
      version: 3,
      decision: "REJECTED",
      rejection_reason: "OTHER",
      notes: "unusual damage",
    });
  });

  it("keeps the draft and never claims success for a malformed success response", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/history?")) return Promise.resolve(jsonResponse(historyFixture));
      return Promise.resolve(jsonResponse({ version: 4 }));
    }));
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "编辑" }));
    await user.click(screen.getByRole("button", { name: "不确定" }));
    await user.click(screen.getByRole("button", { name: "保存修改" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("修改失败");
    expect(screen.getByRole("button", { name: "不确定" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByText("修改已保存")).not.toBeInTheDocument();
  });

  it.each([
    ["another valid review ID", { id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd" }],
    ["a nonincremented version", { version: 5 }],
  ])("rejects a successful edit receipt for %s without refetching or losing the draft", async (_label, overrides) => {
    let historyGets = 0;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/history?")) {
        historyGets += 1;
        return Promise.resolve(jsonResponse(historyFixture));
      }
      return Promise.resolve(jsonResponse(editedReview({
        decision: "UNSURE",
        whole_fish: "REVIEW",
        exact_species_verified: "REVIEW",
        ...overrides,
      })));
    }));
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "编辑" }));
    await user.click(screen.getByRole("button", { name: "不确定" }));
    await user.click(screen.getByRole("button", { name: "保存修改" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("修改失败");
    expect(screen.getByRole("button", { name: "不确定" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "重试修改" })).toBeInTheDocument();
    expect(screen.queryByText("修改已保存")).not.toBeInTheDocument();
    expect(historyGets).toBe(1);
  });

  it("replaces a valid stale conflict with latest state and requires an explicit new submit", async () => {
    let patchCalls = 0;
    const latest = editedReview({ decision: "UNSURE", whole_fish: "REVIEW", exact_species_verified: "REVIEW" });
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/history?")) return Promise.resolve(jsonResponse(historyFixture));
      patchCalls += 1;
      return Promise.resolve(patchCalls === 1
        ? jsonResponse({ detail: { code: "STALE_REVIEW_VERSION", latest } }, 409)
        : jsonResponse(editedReview({ version: 5 })));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "编辑" }));
    await user.click(screen.getByRole("button", { name: "保留" }));
    await user.click(screen.getByRole("button", { name: "保存修改" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("记录已被更新");
    expect(screen.getByRole("button", { name: "不确定" })).toHaveAttribute("aria-pressed", "true");
    expect(patchCalls).toBe(1);
    await user.click(screen.getByRole("button", { name: "保留" }));
    await user.click(screen.getByRole("button", { name: "保存修改" }));
    const bodies = fetchMock.mock.calls
      .filter(([input]) => String(input).endsWith(`/history/${historyItem.id}`))
      .map(([, init]) => JSON.parse(String(init?.body)));
    expect(bodies.map((body) => body.version)).toEqual([3, 4]);
  });

  it.each([
    ["read-only conflict", { detail: { code: "REVIEW_NOT_CURRENT" } }],
    ["malformed conflict", { detail: { code: "STALE_REVIEW_VERSION", latest: { secret: "do not render" } } }],
  ])("handles %s without unsafe retry", async (label, conflict) => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/history?")) return Promise.resolve(jsonResponse(historyFixture));
      return Promise.resolve(jsonResponse(conflict, 409));
    }));
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "编辑" }));
    await user.click(screen.getByRole("button", { name: "不确定" }));
    await user.click(screen.getByRole("button", { name: "保存修改" }));

    if (label === "read-only conflict") {
      expect(await screen.findByText("此记录已不再是当前结果，只能查看。")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "保存修改" })).not.toBeInTheDocument();
    } else {
      expect(await screen.findByRole("alert")).toHaveTextContent("发生版本冲突，请重试");
      expect(screen.getByRole("button", { name: "不确定" })).toHaveAttribute("aria-pressed", "true");
      expect(screen.queryByText("do not render")).not.toBeInTheDocument();
    }
  });
});
