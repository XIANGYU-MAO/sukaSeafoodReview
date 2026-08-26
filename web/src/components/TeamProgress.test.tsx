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
    expect(screen.getByText("12", { selector: ".progress-stat__value" })).toBeInTheDocument();
    expect(screen.getByText("7", { selector: ".progress-stat__value" })).toBeInTheDocument();
    expect(screen.getByText("4", { selector: ".progress-stat__value" })).toBeInTheDocument();
    expect(screen.getByText("1", { selector: ".progress-stat__value" })).toBeInTheDocument();
    expect(screen.getByText("今日审核：3")).toBeInTheDocument();
    for (const member of progressFixture.members) {
      const row = screen.getByRole("row", { name: new RegExp(member.name) });
      expect(within(row).getByText(member.name)).toBeInTheDocument();
      for (const count of [member.completed, member.approved, member.rejected, member.unsure, member.today]) {
        expect(within(row).getAllByText(String(count)).length).toBeGreaterThan(0);
      }
    }
    expect(screen.queryByText("private review note")).not.toBeInTheDocument();
    expect(screen.getByText(/成员数据按提交的审核尝试计数/)).toBeInTheDocument();
  });

  it("fully localizes the explanation and headings in English", () => {
    renderProgress("en");
    expect(screen.getByRole("heading", { name: "Team progress" })).toBeInTheDocument();
    expect(screen.getByText(/Member totals count submitted review attempts/)).toBeInTheDocument();
    expect(screen.getByText("Reviewed today: 3")).toBeInTheDocument();
  });
});
