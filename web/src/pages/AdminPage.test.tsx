import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { App } from "../App";
import { authState, deferred, jsonResponse, renderWithAuth, renderWithStrictAuth } from "../test/helpers";
import {
  IDS,
  candidateFixture,
  candidatesFixture,
  currentFixture,
  defaultAdminResponse,
  exportBatch,
  exportsFixture,
  importPreviewFixture,
  maoAuth,
  progressFixture,
  reviewItem,
  reviewsFixture,
  speciesFixture,
  usersFixture,
} from "../test/task12Fixtures";

function mockAdmin(overrides?: (url: string, init?: RequestInit) => Response | Promise<Response> | undefined) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const override = overrides?.(url, init);
    if (override) return Promise.resolve(override);
    return Promise.resolve(defaultAdminResponse(url));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function openTab(name: string) {
  await userEvent.click(await screen.findByRole("tab", { name }));
}

describe("Mao admin role and accessible shell", () => {
  it("redirects a reviewer before any admin request and hides admin navigation", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      calls.push(url);
      if (url.endsWith("/auth/me")) return Promise.resolve(jsonResponse(authState));
      if (url.endsWith("/reviews/current")) return Promise.resolve(new Response(null, { status: 204 }));
      if (url.endsWith("/progress")) return Promise.resolve(jsonResponse(progressFixture));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    }));
    renderWithAuth(<App />, "/admin");

    expect(await screen.findByRole("heading", { name: "图片审核" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "管理后台" })).not.toBeInTheDocument();
    expect(calls.some((url) => url.includes("/admin/"))).toBe(false);
  });

  it("shows exactly seven fixed-Chinese tabs in English locale with roving keyboard selection", async () => {
    const fetchMock = mockAdmin();
    const user = userEvent.setup();
    renderWithAuth(<App />, "/admin");
    const labels = ["审核进度", "候选图片", "鱼种管理", "审核历史", "导入", "训练集同步", "账号"];

    expect(await screen.findAllByRole("tab")).toHaveLength(7);
    for (const label of labels) expect(screen.getByRole("tab", { name: label })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "English" }));
    for (const label of labels) expect(screen.getByRole("tab", { name: label })).toBeVisible();
    const progress = screen.getByRole("tab", { name: "审核进度" });
    progress.focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "候选图片" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveAccessibleName("候选图片");
    await user.keyboard("{End}");
    expect(screen.getByRole("tab", { name: "账号" })).toHaveAttribute("tabindex", "0");
    await user.keyboard("{Home}");
    expect(screen.getByRole("tab", { name: "审核进度" })).toHaveAttribute("aria-selected", "true");
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/admin/candidates?"))).toBe(true);
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/admin/reviews?"))).toBe(false);
  });

  it("aborts stale StrictMode list requests and does not render their foreign state", async () => {
    const stale = deferred<Response>();
    let candidateGets = 0;
    const signals: AbortSignal[] = [];
    mockAdmin((url, init) => {
      if (url.includes("/admin/candidates?")) {
        candidateGets += 1;
        signals.push(init?.signal as AbortSignal);
        if (candidateGets === 1) return stale.promise;
        return jsonResponse(candidatesFixture);
      }
    });
    renderWithStrictAuth(<App />, "/admin");
    await openTab("候选图片");
    expect(await screen.findByText(IDS.candidate)).toBeInTheDocument();
    await act(async () => stale.resolve(jsonResponse({ ...candidatesFixture, items: [{ ...candidateFixture, id: IDS.batch }] })));
    expect(signals.some((signal) => signal.aborted)).toBe(true);
    expect(screen.queryByText(IDS.batch)).not.toBeInTheDocument();
  });
});

describe("progress/current and candidate safety", () => {
  it("releases with exact version, reason, confirmation and CSRF; conflicts preserve the draft", async () => {
    let mutations = 0;
    const fetchMock = mockAdmin((url, init) => {
      if (url.endsWith(`/admin/current/${IDS.candidate}/release`) && init?.method === "POST") {
        mutations += 1;
        return jsonResponse({ detail: { code: "STALE_CANDIDATE_VERSION", latest: { ...candidateFixture, version: 2 } } }, 409);
      }
    });
    const user = userEvent.setup();
    renderWithAuth(<App />, "/admin");
    expect(await screen.findByText(IDS.candidate)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "释放" }));
    await user.type(screen.getByLabelText("释放原因"), "  成员临时离开  ");
    await user.click(screen.getByRole("button", { name: "确认释放" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("已被更新");
    expect(screen.getByLabelText("释放原因")).toHaveValue("  成员临时离开  ");
    expect(mutations).toBe(1);
    const call = fetchMock.mock.calls.find(([input]) => String(input).endsWith(`/admin/current/${IDS.candidate}/release`));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ version: 1, reason: "成员临时离开" });
    expect(new Headers(call?.[1]?.headers).get("X-CSRF-Token")).toBe("mao-csrf-token");
  });

  it("transfers with the exact version, active reviewer, reason and CSRF", async () => {
    const fetchMock = mockAdmin((url, init) => {
      if (url.endsWith(`/admin/current/${IDS.candidate}/transfer`) && init?.method === "POST") {
        return jsonResponse({
          ...candidateFixture,
          version: 2,
          current_reviewer: { id: IDS.xinhui, display_name: "Xinhui", active: true },
        });
      }
    });
    const user = userEvent.setup();
    renderWithAuth(<App />, "/admin");
    await user.click(await screen.findByRole("button", { name: "转交" }));
    await user.selectOptions(screen.getByLabelText("新审核人"), IDS.xinhui);
    await user.type(screen.getByLabelText("转交原因"), "平衡工作量");
    await user.click(screen.getByRole("button", { name: "确认转交" }));
    expect(await screen.findByRole("status")).toHaveTextContent("转交成功");
    const call = fetchMock.mock.calls.find(([input]) => String(input).endsWith(`/admin/current/${IDS.candidate}/transfer`));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ version: 1, new_reviewer_id: IDS.xinhui, reason: "平衡工作量" });
    expect(new Headers(call?.[1]?.headers).get("X-CSRF-Token")).toBe("mao-csrf-token");
  });

  it("uses URLSearchParams filters, blocks HTTP edits, and requires reviewed-species invalidation assignment", async () => {
    const fetchMock = mockAdmin((url, init) => {
      if (url.endsWith(`/admin/candidates/${IDS.candidate}`) && init?.method === "PATCH") {
        return jsonResponse({
          ...candidateFixture,
          species: { ...currentFixture.items[0].species, id: IDS.species2, code: "SF002", name_zh: "测试鱼二" },
          version: 2,
          current_reviewer: { id: IDS.xinhui, display_name: "Xinhui", active: true },
          current_review: null,
        });
      }
    });
    const user = userEvent.setup();
    renderWithAuth(<App />, "/admin");
    await openTab("候选图片");
    await screen.findByText(IDS.candidate);
    await user.selectOptions(screen.getByRole("combobox", { name: "审核状态" }), "true");
    await user.type(screen.getByRole("searchbox", { name: "候选搜索" }), "obs:1");
    await user.click(screen.getByRole("button", { name: "应用候选筛选" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => {
      const url = String(input);
      return url.includes("/admin/candidates?") && url.includes("reviewed=true") && url.includes("search=obs%3A1");
    })).toBe(true));
    await user.click(screen.getByRole("button", { name: "编辑候选" }));
    await user.clear(screen.getByLabelText("预览图地址"));
    await user.type(screen.getByLabelText("预览图地址"), "http://unsafe.example/fish.jpg");
    await user.type(screen.getByLabelText("候选修改原因"), "修正图片");
    await user.click(screen.getByRole("button", { name: "保存候选" }));
    expect(screen.getByRole("alert")).toHaveTextContent("HTTPS");
    expect(fetchMock.mock.calls.filter(([input]) => String(input).endsWith(`/admin/candidates/${IDS.candidate}`))).toHaveLength(0);
    await user.clear(screen.getByLabelText("预览图地址"));
    await user.type(screen.getByLabelText("预览图地址"), candidateFixture.preview_url);
    await user.selectOptions(screen.getByLabelText("所属鱼种"), IDS.species2);
    await user.click(screen.getByRole("button", { name: "保存候选" }));
    expect(screen.getByRole("alert")).toHaveTextContent("二次确认");
    expect(screen.getByLabelText("新审核人")).toBeInTheDocument();
    expect(screen.getByText(/旧审核记录保留/)).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("新审核人"), IDS.xinhui);
    await user.click(screen.getByRole("button", { name: "确认失效并保存" }));
    expect(await screen.findByRole("status")).toHaveTextContent("候选修改已保存");
    const save = fetchMock.mock.calls.find(([input]) => String(input).endsWith(`/admin/candidates/${IDS.candidate}`));
    expect(JSON.parse(String(save?.[1]?.body))).toEqual({
      version: 1,
      species_id: IDS.species2,
      confirm_review_invalidation: true,
      new_reviewer_id: IDS.xinhui,
      reason: "修正图片",
    });
  });
});

describe("species and review administration", () => {
  it("enforces Windows-safe species codes, reserved names, reasons, immutable code and no delete", async () => {
    const fetchMock = mockAdmin((url, init) => {
      if (url.endsWith("/admin/species") && init?.method === "POST") {
        return jsonResponse({ ...speciesFixture.items[0], id: IDS.batch, code: "SF003", candidate_count: 0 }, 201);
      }
    });
    const user = userEvent.setup();
    renderWithAuth(<App />, "/admin");
    await openTab("鱼种管理");
    await screen.findByText("Piscis probatio");
    await user.click(screen.getByRole("button", { name: "新增鱼种" }));
    await user.type(screen.getByLabelText("鱼种代码"), "CON");
    await user.type(screen.getByLabelText("中文名"), "新鱼");
    await user.type(screen.getByLabelText("英文名"), "New fish");
    await user.type(screen.getByLabelText("学名"), "Piscis novus");
    await user.type(screen.getByLabelText("鱼种修改原因"), "新增目录");
    await user.click(screen.getByRole("button", { name: "创建鱼种" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Windows 保留名");
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "DELETE")).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "取消新增" }));
    await user.click(screen.getByRole("button", { name: "编辑 SF001" }));
    expect(screen.queryByRole("textbox", { name: "鱼种代码" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "停用鱼种" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /删除/ })).not.toBeInTheDocument();
  });

  it("creates a safe species and explicitly confirms stopping an existing species", async () => {
    const fetchMock = mockAdmin((url, init) => {
      if (url.endsWith("/admin/species") && init?.method === "POST") {
        return jsonResponse({ ...speciesFixture.items[0], id: IDS.batch, code: "SF003", candidate_count: 0 }, 201);
      }
      if (url.endsWith(`/admin/species/${IDS.species1}`) && init?.method === "PATCH") {
        return jsonResponse({ ...speciesFixture.items[0], active: false });
      }
    });
    const user = userEvent.setup();
    renderWithAuth(<App />, "/admin");
    await openTab("鱼种管理");
    await user.click(await screen.findByRole("button", { name: "新增鱼种" }));
    await user.type(screen.getByLabelText("鱼种代码"), "SF003");
    await user.type(screen.getByLabelText("中文名"), "新鱼");
    await user.type(screen.getByLabelText("英文名"), "New fish");
    await user.type(screen.getByLabelText("学名"), "Piscis novus");
    await user.type(screen.getByLabelText("鱼种修改原因"), "新增目录");
    await user.click(screen.getByRole("button", { name: "创建鱼种" }));
    expect(await screen.findByRole("status")).toHaveTextContent("鱼种已创建");
    await user.click(screen.getByRole("button", { name: "编辑 SF001" }));
    await user.click(screen.getByRole("button", { name: "停用鱼种" }));
    await user.type(screen.getByLabelText("鱼种修改原因"), "目录暂停维护");
    await user.click(screen.getByRole("button", { name: "保存鱼种" }));
    expect(screen.getByRole("alert")).toHaveTextContent("明确确认");
    await user.click(screen.getByRole("button", { name: "确认停用并保存" }));
    expect(await screen.findByRole("status")).toHaveTextContent("鱼种修改已保存");
    const stop = fetchMock.mock.calls.find(([input]) => String(input).endsWith(`/admin/species/${IDS.species1}`));
    expect(JSON.parse(String(stop?.[1]?.body))).toEqual({ active: false, reason: "目录暂停维护" });
  });

  it("filters admin history safely and edits with buttons, rejection pills, exact N+1 receipt", async () => {
    const fetchMock = mockAdmin((url, init) => {
      if (url.endsWith(`/admin/reviews/${IDS.review}`) && init?.method === "PATCH") {
        const body = JSON.parse(String(init.body));
        return jsonResponse({
          id: IDS.review,
          candidate_id: IDS.candidate,
          reviewer_id: IDS.hassan,
          decision: body.decision,
          rejection_reason: body.rejection_reason,
          notes: body.notes,
          whole_fish: "REVIEW",
          exact_species_verified: "REVIEW",
          is_current: true,
          version: 2,
        });
      }
    });
    const user = userEvent.setup();
    renderWithAuth(<App />, "/admin");
    await openTab("审核历史");
    expect(await screen.findByText(IDS.review)).toBeInTheDocument();
    expect(screen.getByLabelText("审核结束日期")).toHaveAttribute("max", "9998-12-31");
    await user.click(screen.getByRole("button", { name: "编辑审核" }));
    expect(screen.queryByRole("combobox", { name: "审核结果" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "拒绝" }));
    await user.click(screen.getByRole("radio", { name: "重复图片" }));
    await user.type(screen.getByLabelText("管理员修改原因"), "纠正判断");
    await user.click(screen.getByRole("button", { name: "保存审核" }));
    expect(await screen.findByText("审核修改已保存")).toBeInTheDocument();
    const call = fetchMock.mock.calls.find(([input]) => String(input).endsWith(`/admin/reviews/${IDS.review}`));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      version: 1,
      decision: "REJECTED",
      rejection_reason: "DUPLICATE",
      notes: null,
      reason: "纠正判断",
    });
  });

  it("reopens only after named confirmation with both versions and an active new reviewer", async () => {
    const fetchMock = mockAdmin((url, init) => {
      if (url.endsWith(`/admin/reviews/${IDS.review}/reopen`) && init?.method === "POST") {
        return jsonResponse({ ...candidateFixture, version: 2, current_review: null, current_started_at: "2026-08-26T03:00:00Z", current_reviewer: { id: IDS.xinhui, display_name: "Xinhui", active: true } });
      }
    });
    const user = userEvent.setup();
    renderWithAuth(<App />, "/admin");
    await openTab("审核历史");
    await user.click(await screen.findByRole("button", { name: "重新开放" }));
    await user.selectOptions(screen.getByLabelText("重新开放给"), IDS.xinhui);
    await user.type(screen.getByLabelText("重新开放原因"), "需要第二意见");
    await user.click(screen.getByRole("button", { name: "确认重新开放" }));
    const call = fetchMock.mock.calls.find(([input]) => String(input).endsWith(`/admin/reviews/${IDS.review}/reopen`));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ candidate_version: 1, review_version: 1, new_reviewer_id: IDS.xinhui, reason: "需要第二意见" });
    expect(await screen.findByText(/旧审核保留为历史/)).toBeInTheDocument();
  });
});

describe("import, export and one-time password workflows", () => {
  it("previews CSV as FormData without a manual boundary, keeps token secret, invalidates it for a new file and gates commit", async () => {
    let previewCount = 0;
    const fetchMock = mockAdmin((url, init) => {
      if (url.endsWith("/admin/imports/preview")) {
        previewCount += 1;
        return jsonResponse(importPreviewFixture);
      }
    });
    const user = userEvent.setup();
    renderWithAuth(<App />, "/admin");
    await openTab("导入");
    const file = new File(["seafood_code\nSF001"], "candidates.csv", { type: "text/csv" });
    await user.upload(screen.getByLabelText("候选 CSV 文件"), file);
    await user.click(screen.getByRole("button", { name: "预检查" }));
    expect(await screen.findByText("新增：2")).toBeInTheDocument();
    expect(screen.getByText("可能重复地址：1")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(importPreviewFixture.preview_token);
    const call = fetchMock.mock.calls.find(([input]) => String(input).endsWith("/admin/imports/preview"));
    expect(call?.[1]?.body).toBeInstanceOf(FormData);
    expect(new Headers(call?.[1]?.headers).has("Content-Type")).toBe(false);
    expect(new Headers(call?.[1]?.headers).get("X-CSRF-Token")).toBe("mao-csrf-token");
    await user.upload(screen.getByLabelText("候选 CSV 文件"), new File(["x"], "new.csv", { type: "text/csv" }));
    expect(screen.getByRole("button", { name: "提交导入" })).toBeDisabled();
    expect(previewCount).toBe(1);
  });

  it("commits only the in-memory preview token after explicit confirmation and clears it", async () => {
    const fetchMock = mockAdmin((url, init) => {
      if (url.endsWith("/admin/imports/preview")) return jsonResponse(importPreviewFixture);
      if (url.endsWith("/admin/imports/commit") && init?.method === "POST") {
        return jsonResponse({ total: 4, inserted: 2, skipped_exact: 1, possible_url_duplicates: 1, file_sha256: "a".repeat(64) });
      }
    });
    const user = userEvent.setup();
    renderWithAuth(<App />, "/admin");
    await openTab("导入");
    await user.upload(screen.getByLabelText("候选 CSV 文件"), new File(["seafood_code\nSF001"], "candidates.csv", { type: "text/csv" }));
    await user.click(screen.getByRole("button", { name: "预检查" }));
    await user.click(await screen.findByRole("button", { name: "提交导入" }));
    await user.click(screen.getByRole("button", { name: "确认提交导入" }));
    expect(await screen.findByRole("status")).toHaveTextContent("导入完成：新增 2");
    const call = fetchMock.mock.calls.find(([input]) => String(input).endsWith("/admin/imports/commit"));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ preview_token: importPreviewFixture.preview_token });
    expect(document.body).not.toHaveTextContent(importPreviewFixture.preview_token);
  });

  it("distinguishes no-work/reused/new export, exposes exact authenticated CSV link, and validates matching bounded receipt JSON", async () => {
    let creations = 0;
    const fetchMock = mockAdmin((url, init) => {
      if (url.endsWith("/admin/exports") && init?.method === "POST") {
        creations += 1;
        return creations === 1
          ? jsonResponse({ code: "NO_WORK", created: false, batch: null })
          : jsonResponse({ ...exportBatch, created: creations === 3 }, creations === 3 ? 201 : 200);
      }
      if (url.endsWith(`/admin/exports/${IDS.batch}/receipt-file`) && init?.method === "POST") {
        return jsonResponse({ batch_id: IDS.batch, status: "pending", accepted_candidate_ids: [IDS.candidate], pending_candidate_ids: [] });
      }
    });
    const user = userEvent.setup();
    renderWithAuth(<App />, "/admin");
    await openTab("训练集同步");
    expect(await screen.findByText("SF002：0")).toBeInTheDocument();
    const csvLink = screen.getByRole("link", { name: "下载 CSV" });
    expect(csvLink).toHaveAttribute("href", `/sukaseafood/api/v1/admin/exports/${IDS.batch}.csv`);
    expect(csvLink).toHaveAttribute("download", `sukaseafood-export-${IDS.batch}.csv`);
    expect(screen.getByText(/Windows 本地下载工具/)).toBeInTheDocument();
    expect(screen.getByText(/失败或待处理项目仍可进入后续批次/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "创建同步批次" }));
    expect(await screen.findByRole("status")).toHaveTextContent("没有待同步项目");
    await user.click(screen.getByRole("button", { name: "创建同步批次" }));
    expect(await screen.findByRole("status")).toHaveTextContent("现有未完成批次");
    await user.click(screen.getByRole("button", { name: "创建同步批次" }));
    expect(await screen.findByRole("status")).toHaveTextContent("新的同步批次");
    const bad = new File([JSON.stringify({ batch_id: IDS.hassan, items: [] })], "download_receipt.json", { type: "application/json" });
    await user.upload(screen.getByLabelText(`上传 ${IDS.batch} 回执`), bad);
    expect(screen.getByRole("alert")).toHaveTextContent("批次不匹配");
    const valid = new File([JSON.stringify({ batch_id: IDS.batch, items: [{ candidate_id: IDS.candidate, review_id: IDS.review, review_version: 1, status: "SUCCEEDED", sha256: "b".repeat(64), relative_path: "SF001/example.jpg", error: null }] })], "download_receipt.json", { type: "application/json" });
    await user.upload(screen.getByLabelText(`上传 ${IDS.batch} 回执`), valid);
    expect(await screen.findByRole("status")).toHaveTextContent("接受 1，待处理 0");
    const receipt = fetchMock.mock.calls.find(([input]) => String(input).endsWith(`/admin/exports/${IDS.batch}/receipt-file`));
    expect(JSON.parse(String(receipt?.[1]?.body))).toEqual(JSON.parse(await valid.text()));
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("images.example.test"))).toBe(false);
  });

  it("resets only a named reviewer after two steps and erases the temporary password on dismissal", async () => {
    const temporaryPassword = "one-time-secret-password";
    const fetchMock = mockAdmin((url, init) => {
      if (url.endsWith(`/admin/users/${IDS.hassan}/reset-password`) && init?.method === "POST") {
        return jsonResponse({ temporary_password: temporaryPassword });
      }
    });
    const user = userEvent.setup();
    renderWithAuth(<App />, "/admin");
    await openTab("账号");
    expect(await screen.findAllByRole("row")).toHaveLength(7);
    expect(screen.queryByRole("button", { name: "重置 Mao 密码" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重置 Hassan 密码" }));
    await user.type(screen.getByLabelText("密码重置原因"), "账号恢复");
    await user.click(screen.getByRole("button", { name: "继续重置 Hassan" }));
    expect(screen.getByText(/确认重置 Hassan/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认重置 Hassan 密码" }));
    expect(await screen.findByText(temporaryPassword)).toBeInTheDocument();
    const call = fetchMock.mock.calls.find(([input]) => String(input).endsWith(`/admin/users/${IDS.hassan}/reset-password`));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ reason: "账号恢复" });
    await user.click(screen.getByRole("button", { name: "我已复制并关闭" }));
    expect(document.body).not.toHaveTextContent(temporaryPassword);
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it.each([401, 403])("delegates admin status %s to auth bootstrap", async (status) => {
    let meCalls = 0;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        meCalls += 1;
        return Promise.resolve(jsonResponse(meCalls === 1 ? maoAuth : {}, meCalls === 1 ? 200 : 401));
      }
      if (url.endsWith("/progress")) return Promise.resolve(jsonResponse({}, status));
      return Promise.resolve(defaultAdminResponse(url));
    }));
    renderWithAuth(<App />, "/admin");
    await waitFor(() => expect(meCalls).toBeGreaterThan(1));
  });

  it("rejects a malformed or foreign-identity success without claiming mutation success", async () => {
    mockAdmin((url, init) => {
      if (url.endsWith(`/admin/current/${IDS.candidate}/release`) && init?.method === "POST") {
        return jsonResponse({ ...candidateFixture, id: IDS.batch, version: 2, current_reviewer: null, current_started_at: null });
      }
    });
    const user = userEvent.setup();
    renderWithAuth(<App />, "/admin");
    await screen.findByText(IDS.candidate);
    await user.click(screen.getByRole("button", { name: "释放" }));
    await user.type(screen.getByLabelText("释放原因"), "成员离开");
    await user.click(screen.getByRole("button", { name: "确认释放" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("服务返回无效结果");
    expect(screen.queryByText("释放成功")).not.toBeInTheDocument();
  });
});
