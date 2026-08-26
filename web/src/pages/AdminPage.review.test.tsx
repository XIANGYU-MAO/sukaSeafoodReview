import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { App } from "../App";
import { deferred, renderWithAuth } from "../test/helpers";
import {
  IDS,
  candidatesFixture,
  defaultAdminResponse,
  exportBatch,
  importPreviewFixture,
  jsonResponse,
  speciesItems,
} from "../test/task12Fixtures";

function mockAdmin(overrides: (url: string, init?: RequestInit) => Response | Promise<Response> | undefined) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const result = overrides(url, init);
    return Promise.resolve(result ?? defaultAdminResponse(url));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function openTab(name: string) {
  await userEvent.click(await screen.findByRole("tab", { name }));
}

it("loads every species directory page with URLSearchParams and exposes the 101st species", async () => {
  const many = Array.from({ length: 101 }, (_, index) => ({
    ...speciesItems[0],
    id: `60000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
    code: `S${String(index + 1).padStart(3, "0")}`,
    name_zh: `鱼种 ${index + 1}`,
    sort_order: index,
    candidate_count: 0,
  }));
  const fetchMock = mockAdmin((url) => {
    if (url.includes("/admin/species?") && url.includes("active=true")) {
      const offset = Number(new URL(url, "https://local.test").searchParams.get("offset"));
      return jsonResponse({ total: 101, items: many.slice(offset, offset + 100) });
    }
  });
  renderWithAuth(<App />, "/admin");
  await openTab("候选图片");

  expect(await screen.findByRole("option", { name: /S101/ })).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([input]) => {
    const url = new URL(String(input), "https://local.test");
    return url.pathname.endsWith("/admin/species") && url.searchParams.get("limit") === "100" && url.searchParams.get("offset") === "100";
  })).toBe(true);
});

it("uses the complete validated source directory instead of only the visible candidate page", async () => {
  mockAdmin(() => undefined);
  renderWithAuth(<App />, "/admin");
  await openTab("候选图片");

  expect(await screen.findByRole("option", { name: "GBIF" })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "维基共享资源" })).toBeInTheDocument();
});

it("marks old rows unavailable during refresh and keeps them disabled after refresh failure until retry", async () => {
  const refresh = deferred<Response>();
  let candidateGets = 0;
  mockAdmin((url) => {
    if (url.includes("/admin/candidates?")) {
      candidateGets += 1;
      return candidateGets === 1 ? jsonResponse(candidatesFixture) : refresh.promise;
    }
  });
  const user = userEvent.setup();
  renderWithAuth(<App />, "/admin");
  await openTab("候选图片");
  await screen.findByRole("button", { name: "编辑候选" });
  await user.type(screen.getByRole("searchbox", { name: "候选搜索" }), "new filter");
  await user.click(screen.getByRole("button", { name: "应用候选筛选" }));

  expect(await screen.findByRole("status")).toHaveTextContent("正在刷新");
  expect(screen.getByRole("button", { name: "编辑候选" })).toBeDisabled();
  await act(async () => refresh.resolve(jsonResponse({ code: "FAIL" }, 500)));
  expect(await screen.findByRole("alert")).toHaveTextContent("刷新失败");
  expect(screen.getByRole("button", { name: "编辑候选" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "重试刷新" })).toBeInTheDocument();
});

it("keeps the prior candidate page unavailable when page loading fails and installs the retried page", async () => {
  let candidateGets = 0;
  const pageTwo = { ...candidatesFixture, total: 21, items: [{ ...candidatesFixture.items[0], id: "30000000-0000-4000-8000-000000000002" }] };
  mockAdmin((url) => {
    if (url.includes("/admin/candidates?")) {
      candidateGets += 1;
      if (candidateGets === 1) return jsonResponse({ ...candidatesFixture, total: 21 });
      if (candidateGets === 2) return jsonResponse({ detail: { code: "FAIL" } }, 500);
      return jsonResponse(pageTwo);
    }
  });
  const user = userEvent.setup();
  renderWithAuth(<App />, "/admin");
  await openTab("候选图片");
  await user.click(await screen.findByRole("button", { name: "下一页" }));

  expect(await screen.findByText("刷新失败，旧数据暂不可操作。")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "编辑候选" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "重试刷新" }));
  expect(await screen.findByText(`候选编号 ${pageTwo.items[0].id}`)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "编辑候选" })).toBeEnabled();
});

it("keeps current rows unavailable after a conflict refetch failure and enables them only after retry", async () => {
  let currentGets = 0;
  mockAdmin((url, init) => {
    if (url.includes("/admin/current?") && !init?.method) {
      currentGets += 1;
      return currentGets === 2 ? jsonResponse({ detail: { code: "FAIL" } }, 500) : undefined;
    }
    if (url.includes("/admin/current/") && init?.method === "POST") {
      return jsonResponse({ detail: { code: "STALE_CANDIDATE_VERSION" } }, 409);
    }
  });
  const user = userEvent.setup();
  renderWithAuth(<App />, "/admin");
  await user.click(await screen.findByRole("button", { name: "释放" }));
  await user.type(screen.getByLabelText("释放原因"), "成员离开");
  await user.click(screen.getByRole("button", { name: "确认释放" }));

  expect(await screen.findByText("刷新失败，旧数据暂不可操作。")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "释放" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "重试刷新" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "释放" })).toBeEnabled());
});

it("aborts preview A when B is selected and only commits B's in-memory token", async () => {
  const previewA = deferred<Response>();
  const signals: AbortSignal[] = [];
  let previews = 0;
  const tokenB = "b".repeat(43);
  const fetchMock = mockAdmin((url, init) => {
    if (url.endsWith("/admin/imports/preview")) {
      previews += 1;
      signals.push(init?.signal as AbortSignal);
      return previews === 1 ? previewA.promise : jsonResponse({ ...importPreviewFixture, preview_token: tokenB });
    }
    if (url.endsWith("/admin/imports/commit")) return jsonResponse({ total: 4, inserted: 2, skipped_exact: 1, possible_url_duplicates: 1, file_sha256: "a".repeat(64) });
  });
  const user = userEvent.setup();
  renderWithAuth(<App />, "/admin");
  await openTab("导入");
  const input = screen.getByLabelText("候选 CSV 文件");
  await user.upload(input, new File(["A"], "a.csv", { type: "text/csv" }));
  await user.click(screen.getByRole("button", { name: "预检查" }));
  await user.upload(input, new File(["B"], "b.csv", { type: "text/csv" }));
  expect(signals[0]?.aborted).toBe(true);
  await user.click(screen.getByRole("button", { name: "预检查" }));
  await user.click(await screen.findByRole("button", { name: "提交导入" }));
  await user.click(screen.getByRole("button", { name: "确认提交导入" }));
  await act(async () => previewA.resolve(jsonResponse(importPreviewFixture)));

  const commit = fetchMock.mock.calls.find(([inputValue]) => String(inputValue).endsWith("/admin/imports/commit"));
  expect(JSON.parse(String(commit?.[1]?.body))).toEqual({ preview_token: tokenB });
});

it("clears commit eligibility after a terminal preview-token conflict", async () => {
  mockAdmin((url) => {
    if (url.endsWith("/admin/imports/preview")) return jsonResponse(importPreviewFixture);
    if (url.endsWith("/admin/imports/commit")) return jsonResponse({ detail: { code: "IMPORT_PREVIEW_STALE" } }, 409);
  });
  const user = userEvent.setup();
  renderWithAuth(<App />, "/admin");
  await openTab("导入");
  await user.upload(screen.getByLabelText("候选 CSV 文件"), new File(["A"], "a.csv", { type: "text/csv" }));
  await user.click(screen.getByRole("button", { name: "预检查" }));
  await user.click(await screen.findByRole("button", { name: "提交导入" }));
  await user.click(screen.getByRole("button", { name: "确认提交导入" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("重新预检查");
  expect(screen.getByRole("button", { name: "提交导入" })).toBeDisabled();
});

it("renders a validated fatal import report without enabling commit", async () => {
  mockAdmin((url) => url.endsWith("/admin/imports/preview") ? jsonResponse({
    detail: { code: "CSV_TOO_LARGE", report: { ...importPreviewFixture, can_commit: false, blocking_errors: 1, preview_token: null } },
  }, 413) : undefined);
  const user = userEvent.setup();
  renderWithAuth(<App />, "/admin");
  await openTab("导入");
  await user.upload(screen.getByLabelText("候选 CSV 文件"), new File(["A"], "a.csv", { type: "text/csv" }));
  await user.click(screen.getByRole("button", { name: "预检查" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("CSV 文件过大");
  expect(screen.getByText("总行数")).toBeInTheDocument();
  expect(screen.getByText("4")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "提交导入" })).toBeDisabled();
});

it("paginates export history and accepts a partial success with another batch item pending", async () => {
  const secondCandidate = "30000000-0000-4000-8000-000000000002";
  const page2 = { ...exportBatch, id: "50000000-0000-4000-8000-000000000002" };
  const fetchMock = mockAdmin((url, init) => {
    if (url.includes("/admin/exports?") && !init?.method) {
      const offset = new URL(url, "https://local.test").searchParams.get("offset");
      return jsonResponse({ total: 21, items: offset === "20" ? [page2] : [exportBatch] });
    }
    if (url.endsWith(`/admin/exports/${IDS.batch}/receipt-file`)) return jsonResponse({ batch_id: IDS.batch, status: "pending", accepted_candidate_ids: [IDS.candidate], pending_candidate_ids: [secondCandidate] });
  });
  const user = userEvent.setup();
  renderWithAuth(<App />, "/admin");
  await openTab("训练集同步");
  await screen.findByText(IDS.batch);
  const valid = new File([JSON.stringify({ batch_id: IDS.batch, items: [{ candidate_id: IDS.candidate, review_id: IDS.review, review_version: 1, status: "SUCCEEDED", sha256: "b".repeat(64), relative_path: "SF001/example.jpg", error: null }] })], "receipt.json", { type: "application/json" });
  await user.upload(screen.getByLabelText(`上传 ${IDS.batch} 回执`), valid);
  expect(await screen.findByRole("status")).toHaveTextContent("接受 1，待处理 1");
  await user.click(screen.getByRole("button", { name: "下一页" }));
  expect(await screen.findByText(page2.id)).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([input]) => new URL(String(input), "https://local.test").searchParams.get("offset") === "20")).toBe(true);
});

it("shows only a safe known export overlap code and count", async () => {
  mockAdmin((url, init) => url.endsWith("/admin/exports") && init?.method === "POST"
    ? jsonResponse({ detail: { code: "EXPORT_SCOPE_OVERLAP", batch_ids: [IDS.batch, IDS.review], secret: "never-render" } }, 409)
    : undefined);
  const user = userEvent.setup();
  renderWithAuth(<App />, "/admin");
  await openTab("训练集同步");
  await user.click(await screen.findByRole("button", { name: "创建同步批次" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("范围重叠");
  expect(screen.getByRole("alert")).toHaveTextContent("2 个批次");
  expect(document.body).not.toHaveTextContent("never-render");
});

it("uses a focused non-modal one-time password region and erases it", async () => {
  mockAdmin((url, init) => url.endsWith(`/admin/users/${IDS.hassan}/reset-password`) && init?.method === "POST"
    ? jsonResponse({ temporary_password: "one-time-secret-password" })
    : undefined);
  const user = userEvent.setup();
  renderWithAuth(<App />, "/admin");
  await openTab("账号");
  await user.click(await screen.findByRole("button", { name: "重置 Hassan 密码" }));
  await user.type(screen.getByLabelText("密码重置原因"), "恢复账号");
  await user.click(screen.getByRole("button", { name: "继续重置 Hassan" }));
  await user.click(screen.getByRole("button", { name: "确认重置 Hassan 密码" }));

  const region = await screen.findByRole("region", { name: "一次性临时密码" });
  expect(region).toHaveFocus();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "我已复制并关闭" }));
  expect(document.body).not.toHaveTextContent("one-time-secret-password");
});
