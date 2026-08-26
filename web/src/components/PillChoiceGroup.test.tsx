import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PillChoiceGroup } from "./PillChoiceGroup";

const names = ["Hassan", "Mao", "Xinhui", "Wahid", "Sharmaa", "Yiming"] as const;

describe("PillChoiceGroup", () => {
  it("renders a semantic radio group with a visible non-color selection cue", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const { rerender } = render(
      <PillChoiceGroup label="选择姓名" options={names} value={null} onChange={onChange} />,
    );

    expect(screen.getByRole("radiogroup", { name: "选择姓名" })).toBeInTheDocument();
    expect(screen.getAllByRole("radio")).toHaveLength(6);
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(document.querySelector("select")).not.toBeInTheDocument();

    await user.click(screen.getByRole("radio", { name: "Mao" }));
    expect(onChange).toHaveBeenLastCalledWith("Mao");

    rerender(<PillChoiceGroup label="选择姓名" options={names} value="Mao" onChange={onChange} />);
    expect(screen.getByRole("radio", { name: "Mao" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: "Mao" })).toHaveTextContent("✓");
  });

  it("uses wrapped arrow, Home, End, Space, and roving-tabindex keyboard behavior", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const { rerender } = render(
      <PillChoiceGroup label="Names" options={names} value="Hassan" onChange={onChange} />,
    );
    const hassan = screen.getByRole("radio", { name: "Hassan" });

    expect(hassan).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("radio", { name: "Mao" })).toHaveAttribute("tabindex", "-1");
    hassan.focus();
    await user.keyboard("{ArrowLeft}");
    expect(onChange).toHaveBeenLastCalledWith("Yiming");

    rerender(<PillChoiceGroup label="Names" options={names} value="Yiming" onChange={onChange} />);
    expect(screen.getByRole("radio", { name: "Yiming" })).toHaveFocus();
    await user.keyboard("{ArrowRight}");
    expect(onChange).toHaveBeenLastCalledWith("Hassan");
    await user.keyboard("{End}");
    expect(onChange).toHaveBeenLastCalledWith("Yiming");
    await user.keyboard("{Home}");
    expect(onChange).toHaveBeenLastCalledWith("Hassan");
    await user.keyboard("{ArrowDown}");
    expect(onChange).toHaveBeenLastCalledWith("Mao");
    await user.keyboard("{ArrowUp}");
    expect(onChange).toHaveBeenLastCalledWith("Yiming");
    await user.keyboard(" ");
    expect(onChange).toHaveBeenLastCalledWith("Yiming");
  });

  it("does not select or focus disabled pills", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <PillChoiceGroup label="Names" options={names} value="Hassan" onChange={onChange} disabled />,
    );

    const pills = screen.getAllByRole("radio");
    expect(pills).toHaveLength(6);
    pills.forEach((pill) => {
      expect(pill).toBeDisabled();
      expect(pill).toHaveAttribute("tabindex", "-1");
    });
    await user.click(screen.getByRole("radio", { name: "Mao" }));
    expect(onChange).not.toHaveBeenCalled();
  });
});
