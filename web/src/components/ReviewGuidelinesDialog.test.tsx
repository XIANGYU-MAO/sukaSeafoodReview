import { useState } from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { I18nProvider, useI18n } from "../i18n/I18nProvider";
import { ReviewGuidelinesDialog } from "./ReviewGuidelinesDialog";

const zhApprove = [
  "鱼种能够确认。",
  "鱼体整体或关键特征清楚可见。",
  "干净的博物馆标本，以及冰上或市场中的真实鱼，都可以通过。",
  "多条同种鱼都清楚、主体明确时可以通过。",
];
const zhReject = [
  "鱼种错误或无法确认。",
  "鱼群中的鱼太小、重叠严重或没有明确主体。",
  "多种鱼混在一起，无法确定目标。",
  "严重模糊、遮挡或鱼体太小。",
  "切块、鱼片、烹饪图片、插画或重复图片。",
];
const enApprove = [
  "The species can be confirmed.",
  "The whole fish or its key identifying features are clearly visible.",
  "Clean museum specimens and real fish shown on ice or at a market are both acceptable.",
  "Multiple fish of the same species are acceptable when they are clear and the subject is obvious.",
];
const enReject = [
  "The species is wrong or cannot be confirmed.",
  "Fish in a school are too small, heavily overlapping, or have no clear subject.",
  "Multiple species are mixed together and the target is unclear.",
  "The fish is severely blurred, occluded, or too small.",
  "The image shows cuts, fillets, cooked fish, artwork, or a duplicate.",
];

function DialogHarness({ onConfirm }: { onConfirm: () => void }) {
  const { locale, toggleLocale } = useI18n();
  const [open, setOpen] = useState(true);
  return (
    <>
      <button type="button" onClick={toggleLocale}>{locale === "zh" ? "English" : "中文"}</button>
      {open ? <ReviewGuidelinesDialog onConfirm={() => { onConfirm(); setOpen(false); }} /> : null}
    </>
  );
}

describe("ReviewGuidelinesDialog", () => {
  it("renders the exact Chinese guidance and only closes through its focused confirmation", async () => {
    const onConfirm = vi.fn();
    const user = userEvent.setup();
    render(<I18nProvider initialLocale="zh"><DialogHarness onConfirm={onConfirm} /></I18nProvider>);

    const dialog = screen.getByRole("dialog", { name: "审核标准" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(within(dialog).getByRole("heading", { name: "通过" })).toBeInTheDocument();
    expect(within(dialog).getByRole("heading", { name: "不通过" })).toBeInTheDocument();
    for (const copy of [...zhApprove, ...zhReject]) expect(within(dialog).getByText(copy)).toBeInTheDocument();

    const confirm = within(dialog).getByRole("button", { name: "我知道了，开始审核" });
    await waitFor(() => expect(confirm).toHaveFocus());
    fireEvent.keyDown(dialog, { key: "Escape" });
    await user.click(screen.getByTestId("review-guidelines-backdrop"));
    expect(screen.getByRole("dialog", { name: "审核标准" })).toBeInTheDocument();
    expect(onConfirm).not.toHaveBeenCalled();

    await user.click(confirm);
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("switches the open dialog to the exact English guidance without confirming", async () => {
    const onConfirm = vi.fn();
    const user = userEvent.setup();
    render(<I18nProvider initialLocale="zh"><DialogHarness onConfirm={onConfirm} /></I18nProvider>);

    await user.click(within(screen.getByRole("dialog", { name: "审核标准" })).getByRole("button", { name: "English" }));
    const dialog = screen.getByRole("dialog", { name: "Review guidelines" });
    expect(within(dialog).getByRole("heading", { name: "Approve" })).toBeInTheDocument();
    expect(within(dialog).getByRole("heading", { name: "Reject" })).toBeInTheDocument();
    for (const copy of [...enApprove, ...enReject]) expect(within(dialog).getByText(copy)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Got it, start reviewing" })).toBeInTheDocument();
    expect(onConfirm).not.toHaveBeenCalled();
  });
});
