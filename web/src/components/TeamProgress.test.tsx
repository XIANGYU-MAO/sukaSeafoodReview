import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { I18nProvider } from "../i18n/I18nProvider";
import { progressFixture } from "../test/task11Fixtures";
import { TeamProgress } from "./TeamProgress";

function renderProgress(locale: "zh" | "en" = "zh") {
  const injected = { ...progressFixture, notes: "private review note" };
  return render(
    <I18nProvider initialLocale={locale}>
      <TeamProgress data={injected} />
    </I18nProvider>,
  );
}

describe("TeamProgress", () => {
  it("renders every aggregate count and all six members without private detail", () => {
    renderProgress();

    expect(screen.getByText("58.33%")).toBeInTheDocument();
    const aggregateCards = document.querySelector<HTMLElement>(".progress-stat-grid");
    expect(aggregateCards).not.toBeNull();
    expect(within(aggregateCards!).getByText("12", { selector: ".progress-stat__value" })).toBeInTheDocument();
    expect(within(aggregateCards!).getByText("7", { selector: ".progress-stat__value" })).toBeInTheDocument();
    expect(within(aggregateCards!).getByText("4", { selector: ".progress-stat__value" })).toBeInTheDocument();
    expect(within(aggregateCards!).getByText("1", { selector: ".progress-stat__value" })).toBeInTheDocument();
    const decisionCards = screen.getByRole("group", { name: "当前数据集结果" });
    expect(within(decisionCards).getByText("已保留").closest("div")).toHaveClass("progress-stat--approved");
    expect(within(decisionCards).getByText("已拒绝").closest("div")).toHaveClass("progress-stat--rejected");
    expect(within(decisionCards).getByText("不确定").closest("div")).toHaveClass("progress-stat--unsure");
    expect(within(decisionCards).getByText("今日审核").closest("div")).toHaveClass("progress-stat--today");
    expect(within(decisionCards).getByText("3", { selector: ".progress-stat__value" })).toBeInTheDocument();
    for (const member of progressFixture.members) {
      const row = screen.getByRole("row", { name: new RegExp(member.name) });
      expect(within(row).getByText(member.name)).toBeInTheDocument();
      for (const count of [member.completed, member.approved, member.rejected, member.unsure, member.today]) {
        expect(within(row).getAllByText(String(count)).length).toBeGreaterThan(0);
      }
    }
    expect(screen.queryByText("private review note")).not.toBeInTheDocument();
    expect(screen.queryByText(/成员数据按提交的审核尝试计数/)).not.toBeInTheDocument();
  });

  it("fully localizes the headings in English without the removed explanation", () => {
    renderProgress("en");
    expect(screen.getByRole("heading", { name: "Team progress" })).toBeInTheDocument();
    expect(screen.queryByText(/Member totals count submitted review attempts/)).not.toBeInTheDocument();
    const decisionCards = screen.getByRole("group", { name: "Current dataset decisions" });
    expect(within(decisionCards).getByText("Reviewed today")).toBeInTheDocument();
    expect(within(decisionCards).getByText("3", { selector: ".progress-stat__value" })).toBeInTheDocument();
  });
});
