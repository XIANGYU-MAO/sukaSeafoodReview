import type { ComponentProps } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { I18nProvider } from "../i18n/I18nProvider";
import { DecisionPanel } from "./DecisionPanel";

function renderPanel(
  overrides: Partial<ComponentProps<typeof DecisionPanel>> = {},
) {
  const onSubmit = vi.fn();
  const result = render(
    <I18nProvider initialLocale="zh">
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

  it("requires one rejection reason from ten wrapping pills instead of a combobox", async () => {
    const user = userEvent.setup();
    const { onSubmit } = renderPanel();

    await user.click(screen.getByRole("button", { name: "拒绝 (R)" }));
    expect(screen.queryByRole("combobox", { name: "拒绝原因" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("radio")).toHaveLength(10);
    await user.click(screen.getByRole("button", { name: "确认拒绝" }));
    expect(screen.getByRole("alert")).toHaveTextContent("请选择拒绝原因");
    expect(onSubmit).not.toHaveBeenCalled();

    await user.click(screen.getByRole("radio", { name: "鱼种错误" }));
    await user.click(screen.getByRole("button", { name: "确认拒绝" }));
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
    const editable = document.createElement("div");
    editable.setAttribute("contenteditable", "true");
    document.body.append(editable);
    fireEvent.keyDown(editable, { key: "k" });
    editable.remove();
    const ariaInteractive = document.createElement("div");
    ariaInteractive.setAttribute("role", "checkbox");
    document.body.append(ariaInteractive);
    fireEvent.keyDown(ariaInteractive, { key: "k" });
    ariaInteractive.remove();
    expect(onSubmit).toHaveBeenCalledTimes(2);

    rerender(
      <I18nProvider initialLocale="zh">
        <DecisionPanel onSubmit={onSubmit} pending />
      </I18nProvider>,
    );
    fireEvent.keyDown(document.body, { key: "k" });
    expect(onSubmit).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: "正在保存…" })).toBeDisabled();
  });

  it("opens and focuses rejection choices with R without hijacking pill keys", () => {
    renderPanel();
    fireEvent.keyDown(document.body, { key: "r" });
    expect(screen.getByRole("radio", { name: "鱼种错误" })).toHaveFocus();
    fireEvent.keyDown(screen.getByRole("radio", { name: "鱼种错误" }), { key: "u" });
    expect(screen.getByRole("radiogroup", { name: "拒绝原因" })).toBeInTheDocument();
  });
});
