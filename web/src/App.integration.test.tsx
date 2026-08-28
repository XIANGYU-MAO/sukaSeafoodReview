import { act, fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { authState, deferred, jsonResponse, renderWithStrictAuth } from "./test/helpers";
import { historyFixture, progressFixture, reviewerId } from "./test/task11Fixtures";
import { defaultAdminResponse, maoAuth } from "./test/task12Fixtures";
import { markReviewGuidelinesSeen } from "./review/guidelinesSession";

const fixedNames = ["Hassan", "Mao", "Xinhui", "Wahid", "Sharmaa", "Yiming"].map((name) => ({ name }));
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
  location: "Ningbo",
  observed_on: "2026-08-20",
  metadata: {},
};

function pathOf(input: RequestInfo | URL): string {
  return new URL(String(input), "https://review.test").pathname;
}

beforeEach(() => sessionStorage.clear());

describe("production-shell integration", () => {
  it("logs in from a fresh 401, confirms KEEP in the database, then opens private history", async () => {
    let accepted = false;
    let currentCalls = 0;
    const decisionReceipt = deferred<Response>();
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const path = pathOf(input);
      if (path.endsWith("/auth/me")) return Promise.resolve(jsonResponse({}, 401));
      if (path.endsWith("/auth/names")) return Promise.resolve(jsonResponse({ login_name_mode: "choices", names: fixedNames }));
      if (path.endsWith("/auth/login")) {
        expect(init).toEqual(expect.objectContaining({
          method: "POST",
          credentials: "include",
          body: JSON.stringify({ name: "Hassan", password: "temporary-password" }),
        }));
        return Promise.resolve(jsonResponse(authState));
      }
      if (path.endsWith("/reviews/current")) {
        currentCalls += 1;
        expect(init?.method).toBe("POST");
        expect(new Headers(init?.headers).get("X-CSRF-Token")).toBe("test-csrf-token");
        return Promise.resolve(accepted ? new Response(null, { status: 204 }) : jsonResponse(candidate));
      }
      if (path.endsWith(`/reviews/${candidate.id}/decision`)) {
        expect(init?.method).toBe("POST");
        expect(init?.body).toBe(JSON.stringify({ decision: "APPROVED", rejection_reason: null, notes: null }));
        expect(new Headers(init?.headers).get("X-CSRF-Token")).toBe("test-csrf-token");
        expect(new Headers(init?.headers).get("Idempotency-Key")).toMatch(
          /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
        );
        return decisionReceipt.promise;
      }
      if (path.endsWith("/history")) {
        const query = new URL(url, "https://review.test").searchParams;
        expect(query.has("reviewer")).toBe(false);
        return Promise.resolve(jsonResponse(historyFixture));
      }
      return Promise.reject(new Error(`Unexpected request: ${url} ${init?.method ?? "GET"}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderWithStrictAuth(<App />);

    await user.click(await screen.findByRole("radio", { name: "Hassan" }));
    await user.type(screen.getByLabelText("密码"), "temporary-password");
    await user.click(screen.getByRole("button", { name: "登录" }));
    await user.click(await screen.findByRole("button", { name: "我知道了，开始审核" }));

    const image = await screen.findByRole("img", { name: "测试鱼 (Piscis probatio)" });
    expect(screen.getByRole("status", { name: "正在加载图片" })).toBeInTheDocument();
    expect(screen.getByText("iNaturalist")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "切换到 English" }));
    expect(screen.getByRole("button", { name: "Keep (K)" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Reject (R)" }));
    expect(screen.getByRole("radio", { name: "Wrong species" })).toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: "Other" }));
    await user.click(screen.getByRole("button", { name: "Cancel rejection" }));
    fireEvent.load(image);
    const currentCallsBeforeSave = currentCalls;
    await user.click(screen.getByRole("button", { name: "Keep (K)" }));
    expect(currentCalls).toBe(currentCallsBeforeSave);
    expect(screen.getByRole("status", { name: "Saving…" })).toBeInTheDocument();

    accepted = true;
    await act(async () => decisionReceipt.resolve(jsonResponse({
          id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          candidate_id: candidate.id,
          reviewer_id: reviewerId,
          decision: "APPROVED",
          rejection_reason: null,
          notes: null,
          whole_fish: "YES",
          exact_species_verified: "YES",
          is_current: true,
          version: 1,
        }, 201)));

    expect(await screen.findByRole("status")).toHaveTextContent("No images are waiting right now");
    await user.click(screen.getByRole("link", { name: "History" }));
    expect(await screen.findByRole("heading", { name: "My review history" })).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([input]) => pathOf(input).endsWith("/history"))).toBe(true);
  });

  it("places Team progress immediately after History in the top navigation", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const path = pathOf(input);
      if (path.endsWith("/auth/me")) return Promise.resolve(jsonResponse(authState));
      if (path.endsWith("/reviews/current")) return Promise.resolve(new Response(null, { status: 204 }));
      return Promise.reject(new Error(`Unexpected request: ${String(input)}`));
    }));
    renderWithStrictAuth(<App />);

    const nav = await screen.findByRole("navigation", { name: "协作审核" });
    expect(Array.from(nav.querySelectorAll("a")).map((link) => link.textContent)).toEqual([
      "审核", "历史记录", "团队记录",
    ]);
  });

  it("hides team progress from reviewers and redirects a direct progress URL when disabled", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = pathOf(input);
      if (path.endsWith("/auth/me")) {
        return Promise.resolve(jsonResponse({ ...authState, team_progress_visible: false }));
      }
      if (path.endsWith("/reviews/current")) return Promise.resolve(new Response(null, { status: 204 }));
      return Promise.reject(new Error(`Unexpected request: ${String(input)}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithStrictAuth(<App />, "/progress");

    const nav = await screen.findByRole("navigation", { name: "协作审核" });
    expect(within(nav).queryByRole("link", { name: "团队记录" })).not.toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "图片审核" })).toBeInTheDocument();
    expect(fetchMock.mock.calls.every(([input]) => !pathOf(input).endsWith("/progress"))).toBe(true);
  });

  it("hides the standalone team progress page from an admin when disabled while keeping admin navigation", async () => {
    markReviewGuidelinesSeen(maoAuth.id);
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = pathOf(input);
      if (path.endsWith("/auth/me")) {
        return Promise.resolve(jsonResponse({ ...maoAuth, team_progress_visible: false }));
      }
      if (path.endsWith("/reviews/current")) return Promise.resolve(new Response(null, { status: 204 }));
      if (path.endsWith("/progress")) return Promise.resolve(jsonResponse(progressFixture));
      return Promise.reject(new Error(`Unexpected request: ${String(input)}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithStrictAuth(<App />, "/progress");

    const nav = await screen.findByRole("navigation", { name: "协作审核" });
    expect(within(nav).queryByRole("link", { name: "团队记录" })).not.toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "管理后台" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "图片审核" })).toBeInTheDocument();
    expect(fetchMock.mock.calls.every(([input]) => !pathOf(input).endsWith("/progress"))).toBe(true);
  });

  it("puts account actions in the username menu and renders language switching as an icon button", async () => {
    markReviewGuidelinesSeen(authState.id);
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const path = pathOf(input);
      if (path.endsWith("/auth/me")) return Promise.resolve(jsonResponse(authState));
      if (path.endsWith("/reviews/current")) return Promise.resolve(new Response(null, { status: 204 }));
      return Promise.reject(new Error(`Unexpected request: ${String(input)}`));
    }));
    const user = userEvent.setup();
    renderWithStrictAuth(<App />);

    const accountButton = await screen.findByRole("button", { name: "Hassan 账户菜单" });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "修改密码" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "退出登录" })).not.toBeInTheDocument();

    const languageButton = screen.getByRole("button", { name: "切换到 English" });
    expect(languageButton.querySelector("svg")).toBeInTheDocument();
    expect(languageButton).not.toHaveTextContent("English");
    await user.click(languageButton);
    expect(screen.getByRole("button", { name: "Switch to 中文" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Hassan Account menu" }));
    const menu = screen.getByRole("menu", { name: "Hassan" });
    expect(within(menu).getByRole("menuitem", { name: "Change password" })).toBeInTheDocument();
    expect(within(menu).getByRole("menuitem", { name: "Log out" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("opens aggregate progress at /progress without exposing review history details", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = pathOf(input);
      if (path.endsWith("/auth/me")) return Promise.resolve(jsonResponse(authState));
      if (path.endsWith("/progress")) return Promise.resolve(jsonResponse(progressFixture));
      return Promise.reject(new Error(`Unexpected request: ${String(input)}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithStrictAuth(<App />, "/progress");

    expect(await screen.findByRole("heading", { name: "团队进度" })).toBeInTheDocument();
    for (const name of fixedNames.map(({ name }) => name)) {
      expect(screen.getByRole("rowheader", { name })).toBeInTheDocument();
    }
    expect(screen.queryByText("Piscis probatio")).not.toBeInTheDocument();
    expect(screen.queryByText("page:1:File:Fish.jpg")).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.every(([input]) => !pathOf(input).endsWith("/history"))).toBe(true);
  });

  it("restores an authenticated refresh directly to the current candidate", async () => {
    markReviewGuidelinesSeen(authState.id);
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = pathOf(input);
      if (path.endsWith("/auth/me")) return Promise.resolve(jsonResponse(authState));
      if (path.endsWith("/reviews/current")) return Promise.resolve(jsonResponse(candidate));
      if (path.endsWith("/progress")) return Promise.resolve(jsonResponse(progressFixture));
      return Promise.reject(new Error(`Unexpected request: ${String(input)} ${init?.method ?? "GET"}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithStrictAuth(<App />);

    expect(await screen.findByRole("img", { name: "测试鱼 (Piscis probatio)" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "登录审核平台" })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.every(([input]) => !pathOf(input).endsWith("/auth/names"))).toBe(true);
  });

  it("logs Mao in from a fresh 401, exposes generic admin navigation, and shows seven Chinese tabs", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = pathOf(input);
      if (path.endsWith("/auth/me")) return Promise.resolve(jsonResponse({}, 401));
      if (path.endsWith("/auth/names")) return Promise.resolve(jsonResponse({ login_name_mode: "choices", names: fixedNames }));
      if (path.endsWith("/auth/login")) {
        expect(init).toEqual(expect.objectContaining({
          method: "POST",
          credentials: "include",
          body: JSON.stringify({ name: "Mao", password: "temporary-password" }),
        }));
        return Promise.resolve(jsonResponse(maoAuth));
      }
      if (path.endsWith("/reviews/current")) return Promise.resolve(new Response(null, { status: 204 }));
      if (path.endsWith("/progress")) return Promise.resolve(jsonResponse(progressFixture));
      try {
        return Promise.resolve(defaultAdminResponse(String(input)));
      } catch {
        return Promise.reject(new Error(`Unexpected request: ${String(input)} ${init?.method ?? "GET"}`));
      }
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderWithStrictAuth(<App />);

    await user.click(await screen.findByRole("radio", { name: "Mao" }));
    await user.type(screen.getByLabelText("密码"), "temporary-password");
    await user.click(screen.getByRole("button", { name: "登录" }));
    await user.click(await screen.findByRole("link", { name: "管理后台" }));

    expect(await screen.findByRole("heading", { name: "管理后台" })).toBeInTheDocument();
    expect(screen.queryByText("Mao 管理")).not.toBeInTheDocument();
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "审核进度", "候选图片", "审核历史", "鱼种管理", "采集与导入", "训练集同步", "账号", "访问设置",
    ]);
    expect(fetchMock.mock.calls.some(([input]) => pathOf(input).endsWith("/auth/names"))).toBe(true);
    expect(fetchMock.mock.calls.some(([input]) => pathOf(input).endsWith("/auth/login"))).toBe(true);
  });

  it("redirects a reviewer away from /admin before any admin request", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = pathOf(input);
      if (path.endsWith("/auth/me")) return Promise.resolve(jsonResponse(authState));
      if (path.endsWith("/reviews/current")) return Promise.resolve(new Response(null, { status: 204 }));
      if (path.endsWith("/progress")) return Promise.resolve(jsonResponse(progressFixture));
      return Promise.reject(new Error(`Unexpected request: ${String(input)} ${init?.method ?? "GET"}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithStrictAuth(<App />, "/admin");

    expect(await screen.findByRole("heading", { name: "图片审核" })).toBeInTheDocument();
    expect(fetchMock.mock.calls.every(([input]) => !pathOf(input).includes("/admin/"))).toBe(true);
  });

  it("gates all work for a first-password change and returns to login after revocation", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = pathOf(input);
      if (path.endsWith("/auth/me")) {
        return Promise.resolve(jsonResponse({ ...authState, must_change_password: true }));
      }
      if (path.endsWith("/auth/change-password")) {
        expect(init?.method).toBe("POST");
        expect(new Headers(init?.headers).get("X-CSRF-Token")).toBe("test-csrf-token");
        expect(init?.body).toBe(JSON.stringify({
          current_password: "temporary-password",
          new_password: "new-password-12345",
        }));
        return Promise.resolve(new Response(null, { status: 204 }));
      }
      if (path.endsWith("/auth/names")) return Promise.resolve(jsonResponse({ login_name_mode: "choices", names: fixedNames }));
      return Promise.reject(new Error(`Unexpected request: ${String(input)} ${init?.method ?? "GET"}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderWithStrictAuth(<App />);

    expect(await screen.findByRole("heading", { name: "首次登录，请修改密码" })).toBeInTheDocument();
    expect(fetchMock.mock.calls.every(([input]) => !pathOf(input).includes("/reviews/") && !pathOf(input).includes("/admin/"))).toBe(true);
    await user.type(screen.getByLabelText("当前密码"), "temporary-password");
    await user.type(screen.getByLabelText("新密码"), "new-password-12345");
    await user.type(screen.getByLabelText("确认新密码"), "new-password-12345");
    await user.click(screen.getByRole("button", { name: "修改密码" }));

    expect(await screen.findByRole("heading", { name: "登录审核平台" })).toBeInTheDocument();
    expect(screen.getByText("密码已修改，请重新登录。")).toBeInTheDocument();
  });
});
