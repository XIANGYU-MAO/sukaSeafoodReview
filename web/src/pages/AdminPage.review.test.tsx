import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { App } from "../App";
import { deferred, renderWithAuth } from "../test/helpers";
import {
  IDS,
  candidatesFixture,
  candidateFixture,
  currentFixture,
  defaultAdminResponse,
  exportBatch,
  importPreviewFixture,
  jsonResponse,
  speciesFixture,
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

it("guides collection through four steps with current downloads, copy, and species navigation", async () => {
  mockAdmin(() => undefined);
  const user = userEvent.setup();
  renderWithAuth(<App />, "/admin");

  expect(await screen.findAllByRole("tab")).toHaveLength(7);
  await user.click(screen.getByRole("tab", { name: "采集与导入" }));
  for (const heading of ["1. 管理鱼种", "2. 准备本地采集器", "3. 本地生成 CSV", "4. 预检查并导入"]) {
    expect(screen.getByRole("heading", { name: heading })).toBeInTheDocument();
  }
  expect(screen.getByRole("link", { name: "下载采集器 ZIP" })).toHaveAttribute("href", "/sukaseafood/review/downloads/sukaseafood-collector.zip");
  expect(screen.getByRole("link", { name: "下载最新鱼种配置" })).toHaveAttribute("href", "/sukaseafood/api/v1/admin/collector/config");
  const downloads = screen.getByRole("group", { name: "采集器下载" });
  expect(downloads).toHaveClass("equal-action-row");
  expect(within(downloads).getAllByRole("link")).toHaveLength(2);
  expect(screen.getByRole("list", { name: "当前启用鱼种" })).toHaveTextContent("SF001 · 测试鱼");
  await user.click(screen.getByRole("button", { name: "复制命令" }));
  expect(await screen.findByRole("status")).toHaveTextContent("命令已复制。");
  await user.click(screen.getByRole("button", { name: "前往鱼种管理" }));
  expect(screen.getByRole("tab", { name: "鱼种管理" })).toHaveAttribute("aria-selected", "true");
});

it("blocks configuration download until at least one active species is available", async () => {
  const user = userEvent.setup();
  mockAdmin((url, init) => url.includes("/admin/species?") && !init?.method ? jsonResponse({ total: 0, items: [] }) : undefined);
  renderWithAuth(<App />, "/admin");
  await user.click(await screen.findByRole("tab", { name: "采集与导入" }));

  expect(screen.getByRole("button", { name: "下载最新鱼种配置" })).toBeDisabled();
  expect(screen.getByText("请先在鱼种管理中新增并启用鱼种。")).toBeInTheDocument();
});

it("switches command syntax for Unix replenishment and accepts a CSV dropped onto the upload area", async () => {
  const fetchMock = mockAdmin((url) => url.endsWith("/admin/imports/preview") ? jsonResponse(importPreviewFixture) : undefined);
  const user = userEvent.setup();
  renderWithAuth(<App />, "/admin");
  await openTab("采集与导入");

  await user.click(screen.getByRole("button", { name: "macOS / Linux" }));
  await user.click(screen.getByRole("button", { name: "数量不足时补采" }));
  const minimum = screen.getByRole("spinbutton", { name: "每个鱼种候选数至少达到" });
  fireEvent.change(minimum, { target: { value: "300" } });
  expect(screen.getByRole("list", { name: "鱼种候选缺口" })).toHaveTextContent("SF001测试鱼当前 2还差 298");
  expect(screen.getByRole("list", { name: "鱼种候选缺口" })).toHaveTextContent("SF002其他鱼当前 1还差 299");
  const limit = screen.getByRole("spinbutton", { name: "每个鱼种、每个来源最多采集" });
  fireEvent.change(limit, { target: { value: "250" } });
  expect(screen.getByText(/python3 \.\/collect_fish_images\.py.*--max-per-species 250 --minimum-total-per-species 300 --resume/)).toBeInTheDocument();

  const dropped = new File(["seafood_code\nSF001"], "dropped.csv", { type: "text/csv" });
  const dropZone = screen.getByText("把候选 CSV 拖到这里").closest(".csv-drop-zone");
  expect(dropZone).not.toBeNull();
  fireEvent.drop(dropZone!, { dataTransfer: { files: [dropped] } });
  expect(screen.getByText("已选择：dropped.csv")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "预检查" }));
  await screen.findByRole("button", { name: "提交导入" });
  const previewCall = fetchMock.mock.calls.find(([inputValue]) => String(inputValue).endsWith("/admin/imports/preview"));
  expect(previewCall?.[1]?.body).toBeInstanceOf(FormData);
});

it("approves an observed image host and automatically previews the retained CSV again", async () => {
  let previewCount = 0;
  const blocked = {
    ...importPreviewFixture,
    new_rows: 1,
    can_commit: false,
    blocking_errors: 1,
    issues: [{ row: 2, related_row: null, code: "UNAPPROVED_IMAGE_HOST", message: "not approved", blocking: true, host: "data.newmuseum.org" }],
    issue_groups: [{ code: "UNAPPROVED_IMAGE_HOST", message: "not approved", blocking: true, host: "data.newmuseum.org", count: 1, sample_rows: [2], sample_related_rows: [null], omitted_rows: 0 }],
  };
  const fetchMock = mockAdmin((url) => {
    if (url.endsWith("/admin/imports/preview")) {
      previewCount += 1;
      return jsonResponse(previewCount === 1 ? blocked : importPreviewFixture);
    }
    if (url.endsWith("/admin/imports/approve-origin")) return jsonResponse({ hostname: "data.newmuseum.org", created: true });
  });
  const user = userEvent.setup();
  renderWithAuth(<App />, "/admin");
  await openTab("采集与导入");
  await user.upload(screen.getByLabelText("候选 CSV 文件"), new File(["A"], "a.csv", { type: "text/csv" }));
  await user.click(screen.getByRole("button", { name: "预检查" }));
  await user.click(await screen.findByRole("button", { name: "批准此来源并重新预检查" }));

  expect(await screen.findByRole("button", { name: "提交导入" })).toBeEnabled();
  expect(previewCount).toBe(2);
  const approval = fetchMock.mock.calls.find(([inputValue]) => String(inputValue).endsWith("/admin/imports/approve-origin"));
  expect(JSON.parse(String(approval?.[1]?.body))).toEqual({ preview_token: importPreviewFixture.preview_token, hostname: "data.newmuseum.org" });
});

it("requires explicit confirmation before skipping blocking rows", async () => {
  const blocked = {
    ...importPreviewFixture,
    new_rows: 1,
    can_commit: false,
    blocking_errors: 1,
    issues: [{ row: 3, related_row: null, code: "INVALID_LICENSE", message: "invalid", blocking: true, host: null }],
    issue_groups: [{ code: "INVALID_LICENSE", message: "invalid", blocking: true, host: null, count: 1, sample_rows: [3], sample_related_rows: [null], omitted_rows: 0 }],
  };
  const fetchMock = mockAdmin((url) => {
    if (url.endsWith("/admin/imports/preview")) return jsonResponse(blocked);
    if (url.endsWith("/admin/imports/commit")) return jsonResponse({ total: 4, inserted: 1, skipped_exact: 1, skipped_url_duplicates: 1, skipped_blocking: 1, file_sha256: "a".repeat(64) });
  });
  const user = userEvent.setup();
  renderWithAuth(<App />, "/admin");
  await openTab("采集与导入");
  await user.upload(screen.getByLabelText("候选 CSV 文件"), new File(["A"], "a.csv", { type: "text/csv" }));
  await user.click(screen.getByRole("button", { name: "预检查" }));
  await user.click(await screen.findByRole("button", { name: "跳过阻断行并导入有效行" }));
  expect(screen.getByText(/被跳过的行不会进入审核队列/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "确认跳过并导入" }));

  expect(await screen.findByRole("status")).toHaveTextContent("跳过阻断行 1");
  const commit = fetchMock.mock.calls.find(([inputValue]) => String(inputValue).endsWith("/admin/imports/commit"));
  expect(JSON.parse(String(commit?.[1]?.body))).toEqual({ preview_token: importPreviewFixture.preview_token, skip_blocking_rows: true });
});

it("blocks configuration download when every listed species is inactive", async () => {
  const user = userEvent.setup();
  const inactive = { ...speciesFixture.items[0], active: false };
  mockAdmin((url, init) => url.includes("/admin/species?") && !init?.method ? jsonResponse({ total: 1, items: [inactive] }) : undefined);
  renderWithAuth(<App />, "/admin");
  await user.click(await screen.findByRole("tab", { name: "采集与导入" }));

  expect(screen.getByRole("button", { name: "下载最新鱼种配置" })).toBeDisabled();
  expect(screen.getByText("请先在鱼种管理中新增并启用鱼种。")).toBeInTheDocument();
});

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

it("retains the species edit draft and does not refetch when a valid-looking receipt changes its immutable code", async () => {
  let catalogGets = 0;
  const fetchMock = mockAdmin((url, init) => {
    if (url.includes("/admin/species?") && !init?.method) { catalogGets += 1; return undefined; }
    if (url.endsWith(`/admin/species/${IDS.species1}`) && init?.method === "PATCH") {
      return jsonResponse({ ...speciesFixture.items[0], code: "SF999", name_en: "Corrected fish" });
    }
  });
  const user = userEvent.setup();
  renderWithAuth(<App />, "/admin");
  await openTab("鱼种管理");
  await user.click(await screen.findByRole("button", { name: "编辑 SF001" }));
  const name = screen.getByLabelText("英文名");
  await user.clear(name);
  await user.type(name, "Corrected fish");
  await user.type(screen.getByLabelText("鱼种修改原因"), "修正名称");
  const beforeSave = catalogGets;
  await user.click(screen.getByRole("button", { name: "保存鱼种" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("服务返回无效结果");
  expect(screen.getByLabelText("英文名")).toHaveValue("Corrected fish");
  expect(screen.queryByText("鱼种修改已保存")).not.toBeInTheDocument();
  expect(catalogGets).toBe(beforeSave);
  expect(fetchMock.mock.calls.filter(([input]) => String(input).endsWith(`/admin/species/${IDS.species1}`))).toHaveLength(1);
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
  const pageTwo = {
    ...candidatesFixture,
    total: 21,
    items: [{
      ...candidatesFixture.items[0],
      id: "30000000-0000-4000-8000-000000000002",
      species: { ...candidatesFixture.items[0].species, name_zh: "第二页鱼" },
    }],
  };
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
  expect(await screen.findByText("第二页鱼")).toBeInTheDocument();
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
    if (url.endsWith("/admin/imports/commit")) return jsonResponse({ total: 4, inserted: 2, skipped_exact: 1, skipped_url_duplicates: 1, skipped_blocking: 0, file_sha256: "a".repeat(64) });
  });
  const user = userEvent.setup();
  renderWithAuth(<App />, "/admin");
  await openTab("采集与导入");
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
  expect(JSON.parse(String(commit?.[1]?.body))).toEqual({ preview_token: tokenB, skip_blocking_rows: false });
});

it("locks file ownership while commit A is pending and applies its completion exactly once", async () => {
  const committed = deferred<Response>();
  let previews = 0;
  let commits = 0;
  mockAdmin((url) => {
    if (url.endsWith("/admin/imports/preview")) { previews += 1; return jsonResponse(importPreviewFixture); }
    if (url.endsWith("/admin/imports/commit")) { commits += 1; return committed.promise; }
  });
  const user = userEvent.setup();
  renderWithAuth(<App />, "/admin");
  await openTab("采集与导入");
  const input = screen.getByLabelText("候选 CSV 文件");
  await user.upload(input, new File(["A"], "a.csv", { type: "text/csv" }));
  await user.click(screen.getByRole("button", { name: "预检查" }));
  await user.click(await screen.findByRole("button", { name: "提交导入" }));
  await user.click(screen.getByRole("button", { name: "确认提交导入" }));
  await waitFor(() => expect(commits).toBe(1));

  expect(input).toBeDisabled();
  fireEvent.change(input, { target: { files: [new File(["B"], "b.csv", { type: "text/csv" })] } });
  expect(input).toBeDisabled();
  const previewButton = screen.getByRole("button", { name: "处理中…" });
  const commitButton = screen.getByRole("button", { name: "确认提交导入" });
  const cancelButton = screen.getByRole("button", { name: "取消" });
  expect(previewButton).toBeDisabled();
  expect(commitButton).toBeDisabled();
  expect(cancelButton).toBeDisabled();
  fireEvent.click(previewButton);
  fireEvent.click(commitButton);
  fireEvent.click(cancelButton);
  expect(commits).toBe(1);
  expect(previews).toBe(1);

  await act(async () => committed.resolve(jsonResponse({ total: 4, inserted: 2, skipped_exact: 1, skipped_url_duplicates: 1, skipped_blocking: 0, file_sha256: "a".repeat(64) })));
  expect(await screen.findByRole("status")).toHaveTextContent("导入完成：新增 2");
  expect(commits).toBe(1);
  expect(previews).toBe(1);
  expect(screen.queryByRole("button", { name: "提交导入" })).not.toBeInTheDocument();
});

it("ignores a pending import commit completion after unmount", async () => {
  const committed = deferred<Response>();
  let commits = 0;
  mockAdmin((url) => {
    if (url.endsWith("/admin/imports/preview")) return jsonResponse(importPreviewFixture);
    if (url.endsWith("/admin/imports/commit")) { commits += 1; return committed.promise; }
  });
  const user = userEvent.setup();
  const rendered = renderWithAuth(<App />, "/admin");
  await openTab("采集与导入");
  await user.upload(screen.getByLabelText("候选 CSV 文件"), new File(["A"], "a.csv", { type: "text/csv" }));
  await user.click(screen.getByRole("button", { name: "预检查" }));
  await user.click(await screen.findByRole("button", { name: "提交导入" }));
  await user.click(screen.getByRole("button", { name: "确认提交导入" }));
  await waitFor(() => expect(commits).toBe(1));
  rendered.unmount();

  await act(async () => committed.resolve(jsonResponse({ total: 4, inserted: 2, skipped_exact: 1, skipped_url_duplicates: 1, skipped_blocking: 0, file_sha256: "a".repeat(64) })));
  expect(commits).toBe(1);
  expect(document.body).not.toHaveTextContent("导入完成");
});

it("clears commit eligibility after a terminal preview-token conflict", async () => {
  mockAdmin((url) => {
    if (url.endsWith("/admin/imports/preview")) return jsonResponse(importPreviewFixture);
    if (url.endsWith("/admin/imports/commit")) return jsonResponse({ detail: { code: "IMPORT_PREVIEW_STALE" } }, 409);
  });
  const user = userEvent.setup();
  renderWithAuth(<App />, "/admin");
  await openTab("采集与导入");
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
  await openTab("采集与导入");
  await user.upload(screen.getByLabelText("候选 CSV 文件"), new File(["A"], "a.csv", { type: "text/csv" }));
  await user.click(screen.getByRole("button", { name: "预检查" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("CSV 文件过大");
  expect(screen.getByText("总行数")).toBeInTheDocument();
  expect(screen.getByText("4")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "提交导入" })).toBeDisabled();
});

it("explains the local export workflow and accepts a JSON receipt dropped onto its batch", async () => {
  const secondCandidate = "30000000-0000-4000-8000-000000000002";
  const fetchMock = mockAdmin((url, init) => {
    if (url.endsWith(`/admin/exports/${IDS.batch}/receipt-file`) && init?.method === "POST") {
      return jsonResponse({
        batch_id: IDS.batch,
        status: "pending",
        accepted_candidate_ids: [IDS.candidate],
        pending_candidate_ids: [secondCandidate],
      });
    }
  });
  const user = userEvent.setup();
  renderWithAuth(<App />, "/admin");
  await openTab("训练集同步");

  expect(await screen.findByRole("heading", { name: "训练数据同步流程" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "1. 下载任务 CSV" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "2. 在本地下载原图" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "3. 上传 JSON 回执" })).toBeInTheDocument();
  expect(screen.getByText(/CSV 只交给本地下载工具/)).toBeInTheDocument();
  expect(document.body).not.toHaveTextContent("Mao 的");

  await user.hover(screen.getByRole("button", { name: "操作说明：上传下载回执" }));
  expect(await screen.findByRole("tooltip")).toHaveTextContent("网页不会上传图片");
  expect(screen.getByRole("tooltip")).toHaveTextContent("成功项目以后不会重复加入下载批次");

  const receipt = new File([JSON.stringify({
    batch_id: IDS.batch,
    items: [{
      candidate_id: IDS.candidate,
      review_id: IDS.review,
      review_version: 1,
      status: "SUCCEEDED",
      sha256: "b".repeat(64),
      relative_path: "SF001/example.jpg",
      error: null,
    }],
  })], "receipt.json", { type: "application/json" });
  const dropZone = screen.getByText("把 JSON 回执拖到这里").closest(".receipt-drop-zone");
  expect(dropZone).not.toBeNull();
  fireEvent.drop(dropZone!, { dataTransfer: { files: [receipt] } });

  expect(await screen.findByRole("status")).toHaveTextContent("接受 1，待处理 1");
  expect(fetchMock.mock.calls.filter(([requestUrl]) =>
    String(requestUrl).endsWith(`/admin/exports/${IDS.batch}/receipt-file`),
  )).toHaveLength(1);
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

it("accepts a two-item export in two exact partial receipt uploads", async () => {
  const secondCandidate = "30000000-0000-4000-8000-000000000002";
  let receipts = 0;
  const fetchMock = mockAdmin((url, init) => {
    if (url.endsWith(`/admin/exports/${IDS.batch}/receipt-file`) && init?.method === "POST") {
      receipts += 1;
      return receipts === 1
        ? jsonResponse({ batch_id: IDS.batch, status: "pending", accepted_candidate_ids: [IDS.candidate], pending_candidate_ids: [secondCandidate] })
        : jsonResponse({ batch_id: IDS.batch, status: "completed", accepted_candidate_ids: [secondCandidate], pending_candidate_ids: [] });
    }
  });
  const user = userEvent.setup();
  renderWithAuth(<App />, "/admin");
  await openTab("训练集同步");
  const input = await screen.findByLabelText(`上传 ${IDS.batch} 回执`);
  const receipt = (candidateId: string) => new File([JSON.stringify({
    batch_id: IDS.batch,
    items: [{ candidate_id: candidateId, review_id: IDS.review, review_version: 1, status: "SUCCEEDED", sha256: "b".repeat(64), relative_path: `SF001/${candidateId}.jpg`, error: null }],
  })], "receipt.json", { type: "application/json" });

  await user.upload(input, receipt(IDS.candidate));
  expect(await screen.findByRole("status")).toHaveTextContent("接受 1，待处理 1");
  await user.upload(screen.getByLabelText(`上传 ${IDS.batch} 回执`), receipt(secondCandidate));
  expect(await screen.findByRole("status")).toHaveTextContent("接受 1，待处理 0");
  expect(fetchMock.mock.calls.filter(([requestUrl]) => String(requestUrl).endsWith(`/admin/exports/${IDS.batch}/receipt-file`))).toHaveLength(2);
});

it.each([
  ["SF001", "SF002"],
  [null, "SF001"],
])("rejects an export creation receipt outside requested scope %s", async (requestedScope, returnedScope) => {
  let historyGets = 0;
  mockAdmin((url, init) => {
    if (url.includes("/admin/exports?") && !init?.method) { historyGets += 1; return undefined; }
    if (url.endsWith("/admin/exports") && init?.method === "POST") {
      return jsonResponse({ ...exportBatch, species_code: returnedScope, created: true }, 201);
    }
  });
  const user = userEvent.setup();
  renderWithAuth(<App />, "/admin");
  await openTab("训练集同步");
  if (requestedScope) await user.selectOptions(await screen.findByLabelText("范围"), requestedScope);
  const beforeCreate = historyGets;
  await user.click(await screen.findByRole("button", { name: "创建同步批次" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("服务返回无效结果");
  expect(screen.queryByText("已创建新的同步批次")).not.toBeInTheDocument();
  expect(historyGets).toBe(beforeCreate);
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
