import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../App";
import {
  authState,
  deferred,
  jsonResponse,
  renderWithAuth,
  renderWithStrictAuth,
} from "../test/helpers";
import {
  hasSeenReviewGuidelines,
  markReviewGuidelinesSeen,
} from "../review/guidelinesSession";

beforeEach(() => sessionStorage.clear());

describe("authentication bootstrap", () => {
  it("shows a stable accessible loading state then restores a refresh session", async () => {
    let resolveMe!: (response: Response) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockReturnValue(
        new Promise<Response>((resolve) => {
          resolveMe = resolve;
        }),
      ),
    );
    renderWithAuth(<App />);

    expect(screen.getByRole("status")).toHaveTextContent("正在恢复会话");
    resolveMe(jsonResponse(authState));
    expect(await screen.findByRole("button", { name: "修改密码" })).toBeInTheDocument();
    expect(screen.getByText("Hassan")).toBeInTheDocument();
  });

  it("treats bootstrap 401 as logged out", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        if (String(input).endsWith("/auth/me")) return jsonResponse({}, 401);
        return jsonResponse(["Hassan", "Mao", "Xinhui", "Wahid", "Sharmaa", "Yiming"].map((name) => ({ name })));
      }),
    );
    renderWithAuth(<App />);

    expect(await screen.findByRole("heading", { name: "登录审核平台" })).toBeInTheDocument();
  });

  it.each(["server", "network"])("shows retry instead of login on %s bootstrap failure", async (kind) => {
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(() =>
        kind === "server" ? Promise.resolve(jsonResponse({}, 503)) : Promise.reject(new TypeError("offline")),
      )
      .mockResolvedValueOnce(jsonResponse(authState));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderWithAuth(<App />);

    expect(await screen.findByRole("alert")).toHaveTextContent("无法连接审核服务");
    expect(screen.queryByRole("heading", { name: "登录审核平台" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重试连接" }));
    expect(await screen.findByRole("button", { name: "修改密码" })).toBeInTheDocument();
  });

  it.each([
    ["missing fields", {}],
    ["unknown identity", { ...authState, name: "Intruder" }],
    ["invalid role", { ...authState, role: "owner" }],
    ["wrong fixed role", { ...authState, role: "admin" }],
    ["empty CSRF", { ...authState, csrf_token: "" }],
    ["invalid id", { ...authState, id: "not-a-uuid" }],
  ])("treats a 200 bootstrap payload with %s as a retryable protocol failure", async (_label, payload) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(payload)));

    renderWithAuth(<App />);

    expect(await screen.findByRole("alert")).toHaveTextContent("无法连接审核服务");
    expect(screen.queryByRole("button", { name: "修改密码" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "登录审核平台" })).not.toBeInTheDocument();
  });

  it("aborts StrictMode replay and ignores its stale 401 after a newer login", async () => {
    const staleBootstrap = deferred<Response>();
    const activeBootstrap = deferred<Response>();
    const bootstrapSignals: AbortSignal[] = [];
    let meCalls = 0;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        bootstrapSignals.push(init?.signal as AbortSignal);
        meCalls += 1;
        return meCalls === 1 ? staleBootstrap.promise : activeBootstrap.promise;
      }
      if (url.endsWith("/auth/names")) return Promise.resolve(jsonResponse({ login_name_mode: "choices", names: fixedNames() }));
      if (url.endsWith("/auth/login")) return Promise.resolve(jsonResponse(authState));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderWithStrictAuth(<App />);
    await waitFor(() => expect(meCalls).toBe(2));
    await act(async () => activeBootstrap.resolve(jsonResponse({}, 401)));
    await user.click(await screen.findByRole("radio", { name: "Hassan" }));
    await user.type(screen.getByLabelText("密码"), "temporary-password");
    await user.click(screen.getByRole("button", { name: "登录" }));
    expect(await screen.findByRole("button", { name: "修改密码" })).toBeInTheDocument();

    await act(async () => staleBootstrap.resolve(jsonResponse({}, 401)));

    expect(bootstrapSignals[0]).toBeInstanceOf(AbortSignal);
    expect(bootstrapSignals[0].aborted).toBe(true);
    expect(screen.getByRole("button", { name: "修改密码" })).toBeInTheDocument();
  });

  it("resets guidelines only after a successful explicit login", async () => {
    markReviewGuidelinesSeen(authState.id);
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) return Promise.resolve(jsonResponse({}, 401));
      if (url.endsWith("/auth/names")) return Promise.resolve(jsonResponse({ login_name_mode: "choices", names: fixedNames() }));
      if (url.endsWith("/auth/login")) return Promise.resolve(jsonResponse(authState));
      if (url.endsWith("/reviews/current")) return Promise.resolve(new Response(null, { status: 204 }));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    }));
    const user = userEvent.setup();
    renderWithAuth(<App />);

    await user.click(await screen.findByRole("radio", { name: "Hassan" }));
    await user.type(screen.getByLabelText("密码"), "temporary-password");
    await user.click(screen.getByRole("button", { name: "登录" }));
    await screen.findByRole("button", { name: "修改密码" });
    expect(hasSeenReviewGuidelines(authState.id)).toBe(false);
  });

  it("preserves guidelines when restoring an existing authenticated session", async () => {
    markReviewGuidelinesSeen(authState.id);
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) return Promise.resolve(jsonResponse(authState));
      if (url.endsWith("/reviews/current")) return Promise.resolve(new Response(null, { status: 204 }));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    }));
    renderWithAuth(<App />);

    await screen.findByRole("button", { name: "修改密码" });
    expect(hasSeenReviewGuidelines(authState.id)).toBe(true);
  });

  it("preserves guidelines after a failed explicit login", async () => {
    markReviewGuidelinesSeen(authState.id);
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) return Promise.resolve(jsonResponse({}, 401));
      if (url.endsWith("/auth/names")) return Promise.resolve(jsonResponse({ login_name_mode: "choices", names: fixedNames() }));
      if (url.endsWith("/auth/login")) return Promise.resolve(jsonResponse({}, 401));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    }));
    const user = userEvent.setup();
    renderWithAuth(<App />);

    await user.click(await screen.findByRole("radio", { name: "Hassan" }));
    await user.type(screen.getByLabelText("密码"), "wrong-password");
    await user.click(screen.getByRole("button", { name: "登录" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("姓名或密码不正确。");
    expect(hasSeenReviewGuidelines(authState.id)).toBe(true);
  });
});

function fixedNames() {
  return ["Hassan", "Mao", "Xinhui", "Wahid", "Sharmaa", "Yiming"].map((name) => ({ name }));
}
