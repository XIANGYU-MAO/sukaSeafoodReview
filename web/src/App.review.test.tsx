import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { authState, jsonResponse, renderWithAuth } from "./test/helpers";

describe("authenticated review integration", () => {
  it("wires the root to the bilingual review page while retaining shell actions", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn()
        .mockResolvedValueOnce(jsonResponse(authState))
        .mockResolvedValueOnce(new Response(null, { status: 204 })),
    );
    const user = userEvent.setup();
    renderWithAuth(<App />);

    expect(await screen.findByRole("heading", { name: "图片审核" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "修改密码 / Change password" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "退出登录" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "English" }));
    expect(screen.getByRole("heading", { name: "Image review" })).toBeInTheDocument();
    expect(screen.getByText("Collaborative review")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Change password" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Log out" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "中文" })).toBeInTheDocument();
  });

  it("localizes the reviewer history placeholder without fabricating records", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(authState)));
    const user = userEvent.setup();
    renderWithAuth(<App />, "/history");

    expect(await screen.findByRole("heading", { name: "历史记录尚未接入" })).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByText(/示例数据/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "English" }));
    expect(screen.getByRole("heading", { name: "History is not connected yet" })).toBeInTheDocument();
    expect(screen.getByText("Collaborative review")).toBeInTheDocument();
  });

  it("localizes logout failure controls across the reviewer shell", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/auth/me")) return Promise.resolve(jsonResponse(authState));
        if (url.endsWith("/reviews/current")) return Promise.resolve(new Response(null, { status: 204 }));
        if (url.endsWith("/auth/logout")) return Promise.resolve(jsonResponse({}, 503));
        return Promise.reject(new Error(`Unexpected request: ${url}`));
      }),
    );
    const user = userEvent.setup();
    renderWithAuth(<App />);
    await screen.findByRole("heading", { name: "图片审核" });
    await user.click(screen.getByRole("button", { name: "English" }));
    await user.click(screen.getByRole("button", { name: "Log out" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Log out failed");
    expect(screen.getByRole("button", { name: "Retry log out" })).toBeInTheDocument();
  });
});
