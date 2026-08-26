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
    expect(screen.getByRole("button", { name: "中文" })).toBeInTheDocument();
  });

  it("keeps history and admin as explicit placeholders without fabricated records", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(authState)));
    renderWithAuth(<App />, "/history");

    expect(await screen.findByRole("heading", { name: "历史记录尚未接入" })).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByText(/示例数据/)).not.toBeInTheDocument();
  });
});
