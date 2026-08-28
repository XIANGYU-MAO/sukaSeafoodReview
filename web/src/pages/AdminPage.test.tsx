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

describe("admin authorization and accessible shell", () => {
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

  it("groups exactly seven fixed-Chinese tabs by function while retaining global roving keyboard selection", async () => {
    const fetchMock = mockAdmin();
    const user = userEvent.setup();
    renderWithAuth(<App />, "/admin");
    const labels = ["审核进度", "候选图片", "鱼种管理", "审核历史", "采集与导入", "训练集同步", "账号"];

    expect(await screen.findByRole("heading", { name: "管理后台" })).toBeVisible();
    expect(screen.queryByText("Mao 管理")).not.toBeInTheDocument();
    expect(await screen.findAllByRole("tab")).toHaveLength(7);
    const reviewGroup = screen.getByRole("group", { name: "审核工作" });
    const collectionGroup = screen.getByRole("group", { name: "鱼种与采集" });
    const trainingGroup = screen.getByRole("group", { name: "训练数据" });
    const systemGroup = screen.getByRole("group", { name: "系统管理" });
    for (const label of ["审核进度", "候选图片", "审核历史"]) expect(within(reviewGroup).getByRole("tab", { name: label })).toBeVisible();
    for (const label of ["鱼种管理", "采集与导入"]) expect(within(collectionGroup).getByRole("tab", { name: label })).toBeVisible();
    expect(within(trainingGroup).getByRole("tab", { name: "训练集同步" })).toBeVisible();
    expect(within(systemGroup).getByRole("tab", { name: "账号" })).toBeVisible();
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
  it("bulk-disables the selected candidate source without editing cards one by one", async () => {
    const fetchMock = mockAdmin((url, init) => {
      if (url.endsWith("/admin/candidates/bulk-disable") && init?.method === "POST") {
        return jsonResponse({ matched: 1, disabled: 1, released: 0 });
      }
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    renderWithAuth(<App />, "/admin");
    await openTab("候选图片");

    const disableSource = screen.getByRole("button", { name: "禁用所选来源" });
    const disableSpecies = screen.getByRole("button", { name: "禁用所选鱼种" });
    expect(disableSource).toBeDisabled();
    expect(disableSpecies).toBeDisabled();
    await user.selectOptions(screen.getByRole("combobox", { name: "来源" }), "INATURALIST");
    await user.selectOptions(screen.getByRole("combobox", { name: "鱼种" }), "SF001");
    expect(disableSource).toBeEnabled();
    expect(disableSpecies).toBeEnabled();
    await user.click(disableSource);

    expect(await screen.findByRole("status")).toHaveTextContent("已停用 1 张候选图片");
    const call = fetchMock.mock.calls.find(([input]) => String(input).endsWith("/admin/candidates/bulk-disable"));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      source_dataset: "INATURALIST",
      reason: "管理员批量停用来源：INATURALIST",
    });
    expect(new Headers(call?.[1]?.headers).get("X-CSRF-Token")).toBe("mao-csrf-token");
  });

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
          current_started_at: "2026-08-26T03:00:00Z",
          current_review: null,
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
          current_started_at: "2026-08-26T03:00:00Z",
          current_review: null,
        });
      }
    });
    const user = userEvent.setup();
    renderWithAuth(<App />, "/admin");
    await openTab("候选图片");
    const candidateCard = await screen.findByRole("article");
    expect(candidateCard).toHaveClass("admin-review-card");
    expect(candidateCard.querySelector(".admin-review-card__background")).toHaveAttribute(
      "src",
      candidateFixture.preview_url,
    );
    expect(within(candidateCard).getByText("Hassan")).toHaveClass("admin-review-card__reviewer");
    expect(within(candidateCard).getByRole("link", { name: /来源页/ })).toHaveAttribute("title");
    expect(within(candidateCard).getByText("保留")).toHaveClass("admin-review-result--approved");
    expect(within(candidateCard).getByText("测试鱼")).toBeInTheDocument();
    expect(within(candidateCard).getByRole("link", { name: "打开 iNaturalist 来源页" }))
      .toHaveAttribute("href", candidateFixture.source_url);
    expect(candidateCard).not.toHaveTextContent(IDS.candidate);
    await user.selectOptions(screen.getByRole("combobox", { name: "审核状态" }), "true");
    await user.type(screen.getByRole("searchbox", { name: "候选搜索" }), "obs:1");
    await user.click(screen.getByRole("button", { name: "应用候选筛选" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => {
      const url = String(input);
      return url.includes("/admin/candidates?") && url.includes("reviewed=true") && url.includes("search=obs%3A1");
    })).toBe(true));
    await user.click(screen.getByRole("button", { name: "编辑候选" }));
    const enabledCheckbox = screen.getByRole("checkbox", { name: "启用" });
    expect(enabledCheckbox.closest(".admin-check-field")).not.toBeNull();
    const candidateActions = screen.getByRole("group", { name: "候选表单操作" });
    expect(candidateActions).toHaveClass("equal-action-row");
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
  it("explains species table headings and create fields on hover or focus and groups equal-height form actions", async () => {
    mockAdmin();
    const user = userEvent.setup();
    renderWithAuth(<App />, "/admin");
    await openTab("鱼种管理");
    await screen.findByText("Piscis probatio");

    for (const label of ["代码", "中文名", "英文名", "学名", "排序", "候选数", "状态", "操作"]) {
      expect(screen.getByRole("button", { name: `表头说明：${label}` })).toBeInTheDocument();
    }
    const scientificHeadingHelp = screen.getByRole("button", { name: "表头说明：学名" });
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    await user.hover(scientificHeadingHelp);
    expect(screen.getByRole("tooltip")).toHaveTextContent("拉丁学名");
    await user.unhover(scientificHeadingHelp);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();

    const sourceBreakdown = screen.getByRole("button", { name: "操作说明：候选来源明细：SF001" });
    await user.hover(sourceBreakdown);
    const sourceTooltip = screen.getByRole("tooltip");
    expect(sourceTooltip).toHaveTextContent("合计 2");
    expect(within(sourceTooltip).getByText("iNaturalist").parentElement).toHaveTextContent("iNaturalist1");
    expect(within(sourceTooltip).getByText("GBIF").parentElement).toHaveTextContent("GBIF1");
    expect(within(sourceTooltip).getByText("NOAA 图片库").parentElement).toHaveTextContent("NOAA 图片库0");
    await user.unhover(sourceBreakdown);

    await user.click(screen.getByRole("button", { name: "新增鱼种" }));
    for (const label of ["鱼种代码", "中文名", "英文名", "学名", "排序", "启用", "鱼种修改原因"]) {
      expect(screen.getByRole("button", { name: `字段说明：${label}` })).toBeInTheDocument();
    }
    const scientificFieldHelp = screen.getByRole("button", { name: "字段说明：学名" });
    fireEvent.focus(scientificFieldHelp);
    expect(screen.getByRole("tooltip")).toHaveTextContent("采集器");
    fireEvent.blur(scientificFieldHelp);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();

    await user.click(screen.getByText("高级来源配置（通常不需要填写）"));
    for (const label of ["iNaturalist taxon ID", "GBIF taxon key", "Commons 分类", "Fish-Vista 过滤名称"]) {
      expect(screen.getByRole("button", { name: `字段说明：${label}` })).toBeInTheDocument();
    }
    const actions = screen.getByRole("group", { name: "鱼种表单操作" });
    expect(actions).toHaveClass("equal-action-row");
    expect(within(actions).getByRole("button", { name: "创建鱼种" })).toBeInTheDocument();
    expect(within(actions).getByRole("button", { name: "取消新增" })).toBeInTheDocument();
  });

  it("edits advanced source overrides as typed values and clears an override with JSON null", async () => {
    const configured = {
      ...speciesFixture.items[0],
      inat_taxon_id: 123,
      gbif_taxon_key: 456,
      commons_category: "Category:Test fish",
      fish_vista_filter: "Test fish",
    };
    const fetchMock = mockAdmin((url, init) => {
      if (url.includes("/admin/species?") && !init?.method) return jsonResponse({ total: 1, items: [configured] });
      if (url.endsWith(`/admin/species/${IDS.species1}`) && init?.method === "PATCH") {
        return jsonResponse({ ...configured, inat_taxon_id: null });
      }
    });
    const user = userEvent.setup();
    renderWithAuth(<App />, "/admin");
    await openTab("鱼种管理");
    await user.click(await screen.findByRole("button", { name: "编辑 SF001" }));
    await user.click(screen.getByText("高级来源配置（通常不需要填写）"));
    const inaturalist = screen.getByLabelText("iNaturalist taxon ID");
    expect(inaturalist).toHaveValue("123");
    await user.clear(inaturalist);
    await user.type(screen.getByLabelText("鱼种修改原因"), "改用自动解析");
    await user.click(screen.getByRole("button", { name: "保存鱼种" }));

    const call = fetchMock.mock.calls.find(([input]) => String(input).endsWith(`/admin/species/${IDS.species1}`));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ inat_taxon_id: null, reason: "改用自动解析" });
  });

  it("enforces Windows-safe species codes, reserved names, reasons, immutable code and no delete", async () => {
    const fetchMock = mockAdmin((url, init) => {
      if (url.endsWith("/admin/species") && init?.method === "POST") {
        return jsonResponse({ ...speciesFixture.items[0], id: IDS.batch, code: "SF003", name_zh: "新鱼", name_en: "New fish", scientific_name: "Piscis novus", sort_order: 0, candidate_count: 0 }, 201);
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
        return jsonResponse({
          ...speciesFixture.items[0],
          id: IDS.batch,
          code: "SF003",
          name_zh: "新鱼",
          name_en: "New fish",
          scientific_name: "Piscis novus",
          inat_taxon_id: 123,
          gbif_taxon_key: 456,
          commons_category: "Category:Piscis novus",
          fish_vista_filter: "Piscis novus",
          sort_order: 0,
          candidate_count: 0,
        }, 201);
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
    await user.click(screen.getByText("高级来源配置（通常不需要填写）"));
    await user.type(screen.getByLabelText("iNaturalist taxon ID"), "123");
    await user.type(screen.getByLabelText("GBIF taxon key"), "456");
    await user.type(screen.getByLabelText("Commons 分类"), "Category:Piscis novus");
    await user.type(screen.getByLabelText("Fish-Vista 过滤名称"), "Piscis novus");
    await user.type(screen.getByLabelText("鱼种修改原因"), "新增目录");
    await user.click(screen.getByRole("button", { name: "创建鱼种" }));
    expect(await screen.findByRole("status")).toHaveTextContent("鱼种已创建");
    const create = fetchMock.mock.calls.find(([input, init]) => String(input).endsWith("/admin/species") && init?.method === "POST");
    expect(JSON.parse(String(create?.[1]?.body))).toEqual({ code: "SF003", name_zh: "新鱼", name_en: "New fish", scientific_name: "Piscis novus", inat_taxon_id: 123, gbif_taxon_key: 456, commons_category: "Category:Piscis novus", fish_vista_filter: "Piscis novus", active: true, sort_order: 0, reason: "新增目录" });
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
    const reviewCard = await screen.findByRole("article");
    expect(reviewCard).toHaveClass("admin-review-card");
    expect(reviewCard.querySelector(".admin-review-card__background")).toHaveAttribute(
      "src",
      reviewItem.candidate.preview_url,
    );
    expect(within(reviewCard).getByText("Hassan")).toHaveClass("admin-review-card__reviewer");
    expect(within(reviewCard).getByRole("link", { name: /来源页/ })).toHaveAttribute("title");
    expect(within(reviewCard).getByText("保留")).toHaveClass("admin-review-result--approved");
    expect(within(reviewCard).getByText("测试鱼")).toBeInTheDocument();
    expect(within(reviewCard).getByRole("link", { name: "打开 iNaturalist 来源页" }))
      .toHaveAttribute("href", reviewItem.candidate.source_url);
    expect(reviewCard).not.toHaveTextContent(IDS.review);
    expect(reviewCard).not.toHaveTextContent(IDS.candidate);
    expect(screen.getByLabelText("审核结束日期")).toHaveAttribute("max", "9998-12-31");
    await user.click(screen.getByRole("button", { name: "编辑审核" }));
    expect(screen.queryByRole("combobox", { name: "审核结果" })).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "编辑审核：测试鱼" })).toHaveClass("admin-review-editor");
    expect(screen.getByRole("button", { name: "保留" })).toHaveClass("admin-review-decision-button--approved");
    expect(screen.getByRole("button", { name: "拒绝" })).toHaveClass("admin-review-decision-button--rejected");
    expect(screen.getByRole("button", { name: "不确定" })).toHaveClass("admin-review-decision-button--unsure");
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

  it("uses fixed colors for every admin review result tag", async () => {
    mockAdmin((url, init) => {
      if (url.includes("/admin/reviews?") && !init?.method) {
        return jsonResponse({
          total: 3,
          items: [
            reviewItem,
            {
              ...reviewItem,
              id: "40000000-0000-4000-8000-000000000002",
              decision: "REJECTED",
              rejection_reason: "NOT_A_FISH",
              whole_fish: "NO",
              exact_species_verified: "NO",
            },
            {
              ...reviewItem,
              id: "40000000-0000-4000-8000-000000000003",
              decision: "UNSURE",
              rejection_reason: null,
              whole_fish: "REVIEW",
              exact_species_verified: "REVIEW",
            },
          ],
        });
      }
    });
    renderWithAuth(<App />, "/admin");
    await openTab("审核历史");

    expect(await screen.findByText("保留", { selector: ".admin-review-result" }))
      .toHaveClass("admin-review-result--approved");
    expect(screen.getByText("拒绝", { selector: ".admin-review-result" }))
      .toHaveClass("admin-review-result--rejected");
    expect(screen.getByText("不确定", { selector: ".admin-review-result" }))
      .toHaveClass("admin-review-result--unsure");
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
    await openTab("采集与导入");
    const file = new File(["seafood_code\nSF001"], "candidates.csv", { type: "text/csv" });
    await user.upload(screen.getByLabelText("候选 CSV 文件"), file);
    await user.click(screen.getByRole("button", { name: "预检查" }));
    expect(await screen.findByText("新增")).toBeInTheDocument();
    expect(screen.getByText("同鱼种重复地址")).toBeInTheDocument();
    expect(screen.getByText("第 3 行与第 2 行重复")).toBeInTheDocument();
    expect(screen.getByText("第 4 行与第 2 行重复")).toBeInTheDocument();
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
        return jsonResponse({ total: 4, inserted: 2, skipped_exact: 1, skipped_url_duplicates: 1, skipped_blocking: 0, file_sha256: "a".repeat(64) });
      }
    });
    const user = userEvent.setup();
    renderWithAuth(<App />, "/admin");
    await openTab("采集与导入");
    await user.upload(screen.getByLabelText("候选 CSV 文件"), new File(["seafood_code\nSF001"], "candidates.csv", { type: "text/csv" }));
    await user.click(screen.getByRole("button", { name: "预检查" }));
    await user.click(await screen.findByRole("button", { name: "提交导入" }));
    await user.click(screen.getByRole("button", { name: "确认提交导入" }));
    expect(await screen.findByRole("status")).toHaveTextContent("导入完成：新增 2");
    const call = fetchMock.mock.calls.find(([input]) => String(input).endsWith("/admin/imports/commit"));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ preview_token: importPreviewFixture.preview_token, skip_blocking_rows: false });
    expect(document.body).not.toHaveTextContent(importPreviewFixture.preview_token);
  });

  it("distinguishes no-work/reused/new export, exposes exact authenticated CSV link, and validates matching bounded receipt JSON", async () => {
    let creations = 0;
    const fetchMock = mockAdmin((url, init) => {
      if (url.endsWith("/admin/exports") && init?.method === "POST") {
        creations += 1;
        return creations === 1
          ? jsonResponse({ code: "NO_WORK", created: false, batch: null })
          : jsonResponse({ ...exportBatch, species_code: null, created: creations === 3 }, creations === 3 ? 201 : 200);
      }
      if (url.endsWith(`/admin/exports/${IDS.batch}/receipt-file`) && init?.method === "POST") {
        return jsonResponse({ batch_id: IDS.batch, status: "completed", accepted_candidate_ids: [IDS.candidate], pending_candidate_ids: [] });
      }
    });
    const user = userEvent.setup();
    renderWithAuth(<App />, "/admin");
    await openTab("训练集同步");
    expect(await screen.findByText("SF002：0")).toBeInTheDocument();
    const csvLink = screen.getByRole("link", { name: "下载任务 CSV" });
    expect(csvLink).toHaveAttribute("href", `/sukaseafood/api/v1/admin/exports/${IDS.batch}.csv`);
    expect(csvLink).toHaveAttribute("download", `sukaseafood-export-${IDS.batch}.csv`);
    expect(screen.getByText(/本地下载工具/)).toBeInTheDocument();
    expect(screen.getByText(/下载失败或尚未处理的项目仍可继续同步/)).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("Mao 的");
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
    const maoRow = screen.getByRole("row", { name: /Mao 管理员 启用/ });
    const hassanRow = screen.getByRole("row", { name: /Hassan 审核员 启用/ });
    expect(maoRow).toHaveClass("admin-account-row");
    expect(hassanRow).toHaveClass("admin-account-row");
    expect(within(maoRow).getByText("管理员账号只能通过服务器命令重置")).toHaveClass(
      "admin-account-action-placeholder",
    );
    expect(document.body).not.toHaveTextContent("Mao 只能");
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
