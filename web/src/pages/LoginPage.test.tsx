import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../App";
import { authState, jsonResponse, renderWithAuth } from "../test/helpers";

const serverNames = ["Hassan", "Mao", "Xinhui", "Wahid", "Sharmaa", "Yiming"];

describe("login page", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/auth/me")) return jsonResponse({ detail: "Unauthorized" }, 401);
        if (url.endsWith("/auth/names")) return jsonResponse(serverNames.map((name) => ({ name })));
        throw new Error(`Unexpected request: ${url}`);
      }),
    );
  });

  it("renders only the six fixed ordered names even when the server payload is unexpected", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/auth/me")) return jsonResponse({}, 401);
        if (url.endsWith("/auth/names")) {
          return jsonResponse([
            { name: "Yiming" },
            { name: "Intruder" },
            { name: "Hassan" },
            { name: "Mao" },
            { name: "Xinhui" },
            { name: "Wahid" },
            { name: "Sharmaa" },
          ]);
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );

    renderWithAuth(<App />);

    const radios = await screen.findAllByRole("radio");
    expect(radios.map((radio) => radio.textContent?.replace("✓", "").trim())).toEqual(serverNames);
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("loads names as a retryable closed list instead of inventing a text identity input", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({}, 401))
      .mockRejectedValueOnce(new TypeError("offline"))
      .mockResolvedValueOnce(jsonResponse(serverNames.map((name) => ({ name }))));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderWithAuth(<App />);

    expect(await screen.findByText("无法载入成员名单")).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: /姓名/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重试载入名单" }));
    expect(await screen.findAllByRole("radio")).toHaveLength(6);
  });

  it("submits the selected fixed name and password once with credentials and no CSRF header", async () => {
    let resolveLogin!: (response: Response) => void;
    const loginResponse = new Promise<Response>((resolve) => {
      resolveLogin = resolve;
    });
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) return jsonResponse({}, 401);
      if (url.endsWith("/auth/names")) return jsonResponse(serverNames.map((name) => ({ name })));
      if (url.endsWith("/auth/login")) return loginResponse;
      throw new Error(`Unexpected request: ${url} ${init?.method}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderWithAuth(<App />);
    await user.click(await screen.findByRole("radio", { name: "Mao" }));
    await user.type(screen.getByLabelText("密码"), "temporary-password");
    await user.click(screen.getByRole("button", { name: "登录" }));
    await user.click(screen.getByRole("button", { name: "正在登录…" }));

    const loginCalls = fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/auth/login"));
    expect(loginCalls).toHaveLength(1);
    expect(loginCalls[0][1]).toEqual(
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        body: JSON.stringify({ name: "Mao", password: "temporary-password" }),
      }),
    );
    expect(new Headers(loginCalls[0][1]?.headers).has("X-CSRF-Token")).toBe(false);
    resolveLogin(jsonResponse({ ...authState, name: "Mao", role: "admin" }));
    expect(await screen.findByText("审核工作区即将上线")).toBeInTheDocument();
  });

  it.each([
    [401, "姓名或密码不正确。"],
    [429, "登录暂时不可用，请稍后再试。"],
    [503, "服务暂时不可用，请重试。"],
  ])("maps login status %s to a safe message", async (status, message) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/auth/me")) return jsonResponse({}, 401);
        if (url.endsWith("/auth/names")) return jsonResponse(serverNames.map((name) => ({ name })));
        if (url.endsWith("/auth/login")) return jsonResponse({ detail: "server detail" }, status);
        throw new Error(`Unexpected request: ${url}`);
      }),
    );
    const user = userEvent.setup();

    renderWithAuth(<App />);
    await user.click(await screen.findByRole("radio", { name: "Hassan" }));
    await user.type(screen.getByLabelText("密码"), "wrong-password");
    await user.click(screen.getByRole("button", { name: "登录" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByRole("alert")).not.toHaveTextContent("server detail");
  });

  it("maps a network login failure to a retryable service message", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({}, 401))
      .mockResolvedValueOnce(jsonResponse(serverNames.map((name) => ({ name }))))
      .mockRejectedValueOnce(new TypeError("offline"));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderWithAuth(<App />);
    await user.click(await screen.findByRole("radio", { name: "Hassan" }));
    await user.type(screen.getByLabelText("密码"), "wrong-password");
    await user.click(screen.getByRole("button", { name: "登录" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("服务暂时不可用，请重试。");
  });

  it("requires both a selected name and password", async () => {
    const user = userEvent.setup();
    renderWithAuth(<App />);
    await screen.findAllByRole("radio");

    await user.click(screen.getByRole("button", { name: "登录" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("请选择姓名并输入密码。");
  });

  it("never writes authentication or password values to browser storage", async () => {
    const localSpy = vi.spyOn(Storage.prototype, "setItem");
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/auth/me")) return jsonResponse({}, 401);
        if (url.endsWith("/auth/names")) return jsonResponse(serverNames.map((name) => ({ name })));
        if (url.endsWith("/auth/login")) return jsonResponse(authState);
        throw new Error(`Unexpected request: ${url}`);
      }),
    );

    renderWithAuth(<App />);
    await user.click(await screen.findByRole("radio", { name: "Hassan" }));
    await user.type(screen.getByLabelText("密码"), "storage-secret-password");
    await user.click(screen.getByRole("button", { name: "登录" }));
    await screen.findByText("审核工作区即将上线");

    expect(localSpy).not.toHaveBeenCalled();
    expect(localStorage).toHaveLength(0);
    expect(sessionStorage).toHaveLength(0);
    await waitFor(() => expect(screen.queryByDisplayValue("storage-secret-password")).not.toBeInTheDocument());
  });
});
