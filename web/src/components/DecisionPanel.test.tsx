import type { ComponentProps } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { I18nProvider } from "../i18n/I18nProvider";
import { DecisionPanel } from "./DecisionPanel";

function renderPanel(
  overrides: Partial<ComponentProps<typeof DecisionPanel>> = {},
  initialLocale: "zh" | "en" = "zh",
) {
  const onSubmit = vi.fn();
  const result = render(
    <I18nProvider initialLocale={initialLocale}>
      <DecisionPanel onSubmit={onSubmit} pending={false} {...overrides} />
    </I18nProvider>,
  );
  return { ...result, onSubmit };
}

describe("DecisionPanel", () => {
  it("submits KEEP and UNSURE immediately with only stable English API codes", async () => {
    const user = userEvent.setup();
    const { onSubmit } = renderPanel();

    await user.click(screen.getByRole("button", { name: "保留 (K)" }));
    await user.click(screen.getByRole("button", { name: "不确定 (U)" }));

    expect(onSubmit).toHaveBeenNthCalledWith(1, {
      decision: "APPROVED",
      rejection_reason: null,
      notes: null,
    });
    expect(onSubmit).toHaveBeenNthCalledWith(2, {
      decision: "UNSURE",
      rejection_reason: null,
      notes: null,
    });
    expect(screen.queryByRole("button", { name: /保存/ })).not.toBeInTheDocument();
  });

  it("renders the exact retained main or rejection payload as a non-color selected state", () => {
    const approved = renderPanel({
      selectedPayload: { decision: "APPROVED", rejection_reason: null, notes: null },
    });
    expect(screen.getByRole("button", { name: "保留 (K)" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "保留 (K)" })).toHaveTextContent("✓");
    expect(screen.getByRole("button", { name: "不确定 (U)" })).toHaveAttribute("aria-pressed", "false");
    approved.unmount();

    renderPanel(
      {
        selectedPayload: {
          decision: "REJECTED",
          rejection_reason: "OTHER",
          notes: "fin detail",
        },
      },
      "en",
    );
    expect(screen.getByRole("button", { name: "Reject (R)" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("radio", { name: "Other" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("textbox", { name: "Other reason notes" })).toHaveValue("fin detail");
  });

  it("submits a fixed rejection reason immediately from eleven wrapping pills", async () => {
    const user = userEvent.setup();
    const { onSubmit } = renderPanel();

    await user.click(screen.getByRole("button", { name: "拒绝 (R)" }));
    expect(screen.queryByRole("combobox", { name: "拒绝原因" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("radio")).toHaveLength(11);
    expect(screen.getByRole("radio", { name: "不是鱼" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认拒绝" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("radio", { name: "鱼种错误" }));
    expect(onSubmit).toHaveBeenCalledWith({
      decision: "REJECTED",
      rejection_reason: "WRONG_SPECIES",
      notes: null,
    });
  });

  it("inherits roving pill keyboard behavior and requires nonblank OTHER notes", async () => {
    const user = userEvent.setup();
    const { onSubmit } = renderPanel();
    await user.click(screen.getByRole("button", { name: "拒绝 (R)" }));
    const first = screen.getByRole("radio", { name: "鱼种错误" });
    expect(first).toHaveFocus();
    await user.keyboard("{End}");
    expect(screen.getByRole("radio", { name: "其他" })).toHaveAttribute("aria-checked", "true");
    await user.click(screen.getByRole("button", { name: "确认拒绝" }));
    expect(screen.getByRole("alert")).toHaveTextContent("请填写其他原因");
    expect(onSubmit).not.toHaveBeenCalled();

    await user.type(screen.getByRole("textbox", { name: "其他原因备注" }), "  uncommon issue  ");
    await user.click(screen.getByRole("button", { name: "确认拒绝" }));
    expect(onSubmit).toHaveBeenCalledWith({
      decision: "REJECTED",
      rejection_reason: "OTHER",
      notes: "uncommon issue",
    });
  });

  it("replaces only the pending decision button label with a fixed-size spinner", () => {
    renderPanel({
      pending: true,
      selectedPayload: { decision: "APPROVED", rejection_reason: null, notes: null },
    });

    const keep = screen.getByRole("button", { name: "保留 (K)" });
    expect(keep).toBeDisabled();
    expect(keep).not.toHaveTextContent("保留 (K)");
    expect(keep.querySelector(".decision-button__spinner")).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "正在保存…" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "拒绝 (R)" })).toHaveTextContent("拒绝 (R)");
    expect(screen.getByRole("button", { name: "不确定 (U)" })).toHaveTextContent("不确定 (U)");
  });

  it("supports global K/R/U but ignores repeats, modifiers, pending, and interactive targets", () => {
    const { onSubmit, rerender } = renderPanel();

    fireEvent.keyDown(document.body, { key: "k" });
    fireEvent.keyDown(document.body, { key: "u" });
    expect(onSubmit).toHaveBeenCalledTimes(2);
    fireEvent.keyDown(document.body, { key: "k", repeat: true });
    fireEvent.keyDown(document.body, { key: "k", ctrlKey: true });
    fireEvent.keyDown(document.body, { key: "u", altKey: true });

    for (const target of [
      document.createElement("input"),
      document.createElement("textarea"),
      document.createElement("select"),
      document.createElement("button"),
      document.createElement("a"),
    ]) {
      document.body.append(target);
      fireEvent.keyDown(target, { key: "k" });
      target.remove();
    }
    for (const value of ["", "true", "plaintext-only"]) {
      const editable = document.createElement("div");
      const nested = document.createElement("span");
      editable.setAttribute("contenteditable", value);
      editable.append(nested);
      document.body.append(editable);
      fireEvent.keyDown(nested, { key: "k" });
      editable.remove();
    }
    for (const role of [
      "button",
      "checkbox",
      "combobox",
      "grid",
      "gridcell",
      "link",
      "listbox",
      "menu",
      "menubar",
      "menuitem",
      "searchbox",
      "menuitemcheckbox",
      "menuitemradio",
      "option",
      "radio",
      "radiogroup",
      "scrollbar",
      "slider",
      "spinbutton",
      "switch",
      "tab",
      "tablist",
      "textbox",
      "tree",
      "treegrid",
      "treeitem",
    ]) {
      const ariaInteractive = document.createElement("div");
      const nested = document.createElement("span");
      ariaInteractive.setAttribute("role", role);
      ariaInteractive.append(nested);
      document.body.append(ariaInteractive);
      fireEvent.keyDown(nested, { key: "k" });
      ariaInteractive.remove();
    }
    const focusable = document.createElement("div");
    focusable.tabIndex = 0;
    document.body.append(focusable);
    fireEvent.keyDown(focusable, { key: "k" });
    focusable.remove();
    const programmaticallyFocusable = document.createElement("div");
    programmaticallyFocusable.tabIndex = -1;
    const focusableDescendant = document.createElement("span");
    programmaticallyFocusable.append(focusableDescendant);
    document.body.append(programmaticallyFocusable);
    fireEvent.keyDown(focusableDescendant, { key: "k" });
    programmaticallyFocusable.remove();
    expect(onSubmit).toHaveBeenCalledTimes(2);

    rerender(
      <I18nProvider initialLocale="zh">
        <DecisionPanel onSubmit={onSubmit} pending />
      </I18nProvider>,
    );
    fireEvent.keyDown(document.body, { key: "k" });
    expect(onSubmit).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保留 (K)" })).toBeDisabled();
  });

  it("opens and focuses rejection choices with R without hijacking pill keys", () => {
    renderPanel();
    fireEvent.keyDown(document.body, { key: "r" });
    expect(screen.getByRole("radio", { name: "鱼种错误" })).toHaveFocus();
    fireEvent.keyDown(screen.getByRole("radio", { name: "鱼种错误" }), { key: "u" });
    expect(screen.getByRole("radiogroup", { name: "拒绝原因" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("radio", { name: "不是完整鱼体" }));
    const outside = document.createElement("div");
    outside.tabIndex = -1;
    document.body.append(outside);
    outside.focus();
    fireEvent.keyDown(document.body, { key: "r" });
    expect(screen.getByRole("radio", { name: "不是完整鱼体" })).toHaveFocus();
    outside.remove();
  });
});
