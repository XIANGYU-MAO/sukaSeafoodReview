import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { authState, jsonResponse, renderWithAuth } from "./test/helpers";
import { historyFixture, progressFixture } from "./test/task11Fixtures";
import { markReviewGuidelinesSeen } from "./review/guidelinesSession";

beforeEach(() => {
  sessionStorage.clear();
  markReviewGuidelinesSeen(authState.id);
});

describe("authenticated review integration", () => {
  it("wires the root to the bilingual review page while retaining shell actions", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) return Promise.resolve(jsonResponse(authState));
      if (url.endsWith("/reviews/current")) return Promise.resolve(new Response(null, { status: 204 }));
      if (url.endsWith("/progress")) return Promise.resolve(jsonResponse(progressFixture));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    }));
    const user = userEvent.setup();
    renderWithAuth(<App />);

    expect(await screen.findByRole("heading", { name: "图片审核" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "修改密码" })).toBeInTheDocument();
    expect(screen.queryByText("Change password")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "退出登录" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "English" }));
    expect(screen.getByRole("heading", { name: "Image review" })).toBeInTheDocument();
    expect(screen.getByText("Collaborative review")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Change password" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Log out" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "中文" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "History" })).toHaveAttribute("href", "/history");
  });

  it("routes /history to the real private page with localized active navigation", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) return Promise.resolve(jsonResponse(authState));
      if (url.includes("/history?")) return Promise.resolve(jsonResponse(historyFixture));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    }));
    const user = userEvent.setup();
    renderWithAuth(<App />, "/history");

    expect(await screen.findByRole("heading", { name: "我的审核历史" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "历史记录" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "审核" })).not.toHaveAttribute("aria-current");
    await user.click(screen.getByRole("button", { name: "English" }));
    expect(screen.getByRole("heading", { name: "My review history" })).toBeInTheDocument();
    expect(screen.getByText("Collaborative review")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "History" })).toHaveAttribute("aria-current", "page");
  });

  it("localizes logout failure controls across the reviewer shell", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/auth/me")) return Promise.resolve(jsonResponse(authState));
        if (url.endsWith("/reviews/current")) return Promise.resolve(new Response(null, { status: 204 }));
        if (url.endsWith("/progress")) return Promise.resolve(jsonResponse(progressFixture));
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
