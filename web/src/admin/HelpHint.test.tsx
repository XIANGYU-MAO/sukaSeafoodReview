import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";

import { HelpHint } from "./HelpHint";

it("renders the open tooltip at the document root so table overflow cannot clip it", async () => {
  const user = userEvent.setup();
  render(
    <div className="admin-table-wrap">
      <HelpHint context="表头" label="候选数">来源明细</HelpHint>
    </div>,
  );

  await user.hover(screen.getByRole("button", { name: "表头说明：候选数" }));

  const tooltip = screen.getByRole("tooltip");
  expect(tooltip.parentElement).toBe(document.body);
  expect(tooltip).toHaveStyle({ position: "fixed" });
});
