import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { App } from "../App";
import { authState, jsonResponse, renderWithAuth } from "../test/helpers";

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
    expect(await screen.findByText("审核工作区即将上线")).toBeInTheDocument();
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
    expect(await screen.findByText("审核工作区即将上线")).toBeInTheDocument();
  });
});
