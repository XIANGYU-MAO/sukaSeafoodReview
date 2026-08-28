import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { App } from "../App";
import {
  authState,
  deferred,
  jsonResponse,
  renderWithAuth,
  renderWithStrictAuth,
} from "../test/helpers";

describe("authenticated password and logout flows", () => {
  it("gates protected UI until a forced password change succeeds with CSRF", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) return jsonResponse({ ...authState, must_change_password: true });
      if (url.endsWith("/auth/change-password")) return new Response(null, { status: 204 });
      if (url.endsWith("/auth/names")) return jsonResponse({ login_name_mode: "choices", names: fixedNames() });
      throw new Error(`Unexpected request: ${url} ${init?.method}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderWithAuth(<App />);

    expect(await screen.findByRole("heading", { name: "首次登录，请修改密码" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "审核" })).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("当前密码"), "current-password");
    await user.type(screen.getByLabelText("新密码"), "a-long-new-password");
    await user.type(screen.getByLabelText("确认新密码"), "a-long-new-password");
    await user.click(screen.getByRole("button", { name: "修改密码" }));

    const changeCall = fetchMock.mock.calls.find(([input]) => String(input).endsWith("/auth/change-password"));
    expect(changeCall?.[1]).toEqual(
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        body: JSON.stringify({
          current_password: "current-password",
          new_password: "a-long-new-password",
        }),
      }),
    );
    expect(new Headers(changeCall?.[1]?.headers).get("X-CSRF-Token")).toBe("test-csrf-token");
    expect(await screen.findByText("密码已修改，请重新登录。" )).toBeInTheDocument();
  });

  it("validates password confirmation and a client-side minimum before calling the server", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ...authState, must_change_password: true }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderWithAuth(<App />);
    await screen.findByRole("heading", { name: "首次登录，请修改密码" });

    await user.type(screen.getByLabelText("当前密码"), "current");
    await user.type(screen.getByLabelText("新密码"), "short");
    await user.type(screen.getByLabelText("确认新密码"), "different");
    await user.click(screen.getByRole("button", { name: "修改密码" }));

    expect(screen.getByRole("alert")).toHaveTextContent("新密码至少需要 12 个字符");
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await user.clear(screen.getByLabelText("新密码"));
    await user.type(screen.getByLabelText("新密码"), "long-enough-password");
    await user.clear(screen.getByLabelText("确认新密码"));
    await user.type(screen.getByLabelText("确认新密码"), "does-not-match");
    await user.click(screen.getByRole("button", { name: "修改密码" }));
    expect(screen.getByRole("alert")).toHaveTextContent("两次输入的新密码不一致");
    expect(fetchMock).toHaveBeenCalledTimes(1);

    fireEvent.change(screen.getByLabelText("新密码"), { target: { value: "x".repeat(129) } });
    fireEvent.change(screen.getByLabelText("确认新密码"), { target: { value: "x".repeat(129) } });
    await user.click(screen.getByRole("button", { name: "修改密码" }));
    expect(screen.getByRole("alert")).toHaveTextContent("新密码不能超过 128 个字符");
    expect(fetchMock).toHaveBeenCalledTimes(1);

    const unicodePassword = "🐟".repeat(65);
    fireEvent.change(screen.getByLabelText("新密码"), { target: { value: unicodePassword } });
    fireEvent.change(screen.getByLabelText("确认新密码"), { target: { value: unicodePassword } });
    await user.click(screen.getByRole("button", { name: "修改密码" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock.mock.calls[1]?.[1]?.body).toBe(JSON.stringify({
      current_password: "current",
      new_password: unicodePassword,
    }));
  });

  it("offers the same password flow voluntarily from the protected shell", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(authState)));
    const user = userEvent.setup();
    renderWithAuth(<App />);

    await chooseAccountAction(user, "修改密码");
    expect(screen.getByRole("heading", { name: "修改密码" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "返回审核工作区" })).toBeInTheDocument();
  });

  it("logs out with CSRF and clears auth only after success", async () => {
    let resolveLogout!: (response: Response) => void;
    const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) return Promise.resolve(jsonResponse(authState));
      if (url.endsWith("/reviews/current")) return Promise.resolve(new Response(null, { status: 204 }));
      if (url.endsWith("/auth/logout")) {
        return new Promise<Response>((resolve) => {
          resolveLogout = resolve;
        });
      }
      if (url.endsWith("/auth/names")) return Promise.resolve(jsonResponse({ login_name_mode: "choices", names: fixedNames() }));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderWithAuth(<App />);
    await chooseAccountAction(user, "退出登录");

    expect(screen.getByRole("button", { name: "Hassan 账户菜单" })).toBeInTheDocument();
    const logoutCall = fetchMock.mock.calls.find(([input]) => String(input).endsWith("/auth/logout"));
    expect(logoutCall?.[1]).toEqual(expect.objectContaining({ method: "POST", credentials: "include" }));
    expect(new Headers(logoutCall?.[1]?.headers).get("X-CSRF-Token")).toBe("test-csrf-token");

    resolveLogout(new Response(null, { status: 204 }));
    expect(await screen.findByRole("heading", { name: "登录审核平台" })).toBeInTheDocument();
  });

  it("also clears auth on logout 401", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/auth/me")) return Promise.resolve(jsonResponse(authState));
        if (url.endsWith("/reviews/current")) return Promise.resolve(new Response(null, { status: 204 }));
        if (url.endsWith("/auth/logout")) return Promise.resolve(jsonResponse({}, 401));
        if (url.endsWith("/auth/names")) return Promise.resolve(jsonResponse({ login_name_mode: "choices", names: fixedNames() }));
        return Promise.reject(new Error(`Unexpected request: ${url}`));
      }),
    );
    const user = userEvent.setup();
    renderWithAuth(<App />);
    await chooseAccountAction(user, "退出登录");

    expect(await screen.findByRole("heading", { name: "登录审核平台" })).toBeInTheDocument();
  });

  it("stays authenticated after transient logout failure and allows retry", async () => {
    let logoutCalls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/auth/me")) return Promise.resolve(jsonResponse(authState));
        if (url.endsWith("/reviews/current")) return Promise.resolve(new Response(null, { status: 204 }));
        if (url.endsWith("/auth/logout")) {
          logoutCalls += 1;
          return Promise.resolve(logoutCalls === 1 ? jsonResponse({}, 503) : new Response(null, { status: 204 }));
        }
        if (url.endsWith("/auth/names")) return Promise.resolve(jsonResponse({ login_name_mode: "choices", names: fixedNames() }));
        return Promise.reject(new Error(`Unexpected request: ${url}`));
      }),
    );
    const user = userEvent.setup();
    renderWithAuth(<App />);
    await chooseAccountAction(user, "退出登录");

    expect(await screen.findByText("退出失败，请重试。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Hassan 账户菜单" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重试退出" }));
    expect(await screen.findByRole("heading", { name: "登录审核平台" })).toBeInTheDocument();
  });

  it("keeps logout authoritative when an older StrictMode bootstrap succeeds late", async () => {
    const staleBootstrap = deferred<Response>();
    const activeBootstrap = deferred<Response>();
    let meCalls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/auth/me")) {
          meCalls += 1;
          return meCalls === 1 ? staleBootstrap.promise : activeBootstrap.promise;
        }
        if (url.endsWith("/auth/logout")) return Promise.resolve(new Response(null, { status: 204 }));
        if (url.endsWith("/auth/names")) return Promise.resolve(jsonResponse({ login_name_mode: "choices", names: fixedNames() }));
        return Promise.reject(new Error(`Unexpected request: ${url}`));
      }),
    );
    const user = userEvent.setup();

    renderWithStrictAuth(<App />);
    await waitFor(() => expect(meCalls).toBe(2));
    await act(async () => activeBootstrap.resolve(jsonResponse(authState)));
    await chooseAccountAction(user, "退出登录");
    expect(await screen.findByRole("heading", { name: "登录审核平台" })).toBeInTheDocument();

    await act(async () => staleBootstrap.resolve(jsonResponse(authState)));
    expect(screen.getByRole("heading", { name: "登录审核平台" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /账户菜单$/ })).not.toBeInTheDocument();
  });

  it("clears voluntary password navigation across password change and the next login", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) return jsonResponse(authState);
      if (url.endsWith("/auth/change-password")) return new Response(null, { status: 204 });
      if (url.endsWith("/auth/names")) return jsonResponse({ login_name_mode: "choices", names: fixedNames() });
      if (url.endsWith("/auth/login")) {
        return jsonResponse({ ...authState, csrf_token: "next-session-csrf" });
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderWithAuth(<App />);

    await chooseAccountAction(user, "修改密码");
    await user.type(screen.getByLabelText("当前密码"), "current-password");
    await user.type(screen.getByLabelText("新密码"), "a-long-new-password");
    await user.type(screen.getByLabelText("确认新密码"), "a-long-new-password");
    await user.click(screen.getByRole("button", { name: "修改密码" }));
    expect(await screen.findByRole("heading", { name: "登录审核平台" })).toBeInTheDocument();

    await user.click(await screen.findByRole("radio", { name: "Hassan" }));
    await user.type(screen.getByLabelText("密码"), "a-long-new-password");
    await user.click(screen.getByRole("button", { name: "登录" }));

    expect(await screen.findByRole("button", { name: "Hassan 账户菜单" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "修改密码" })).not.toBeInTheDocument();
  });
});

function fixedNames() {
  return ["Hassan", "Mao", "Xinhui", "Wahid", "Sharmaa", "Yiming"].map((name) => ({ name }));
}

async function chooseAccountAction(
  user: ReturnType<typeof userEvent.setup>,
  action: "修改密码" | "退出登录",
) {
  await user.click(await screen.findByRole("button", { name: /账户菜单$/ }));
  await user.click(screen.getByRole("menuitem", { name: action }));
}
