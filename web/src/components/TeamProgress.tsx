import type { ProgressResponse } from "../api/types";
import { decisionLabel } from "../i18n/catalog";
import { useI18n } from "../i18n/I18nProvider";

export function TeamProgress({ data }: { data: ProgressResponse }) {
  const { locale, t } = useI18n();
  const stats = [
    [t("totalCandidates"), data.total],
    [t("reviewedTotal"), data.reviewed],
    [t("pendingTotal"), data.pending],
    [t("currentlyOpen"), data.currently_open],
  ] as const;
  return (
    <section className="team-progress" aria-labelledby="team-progress-title">
      <div className="team-progress__heading">
        <div>
          <p className="eyebrow">{t("completionPercent")}</p>
          <h2 id="team-progress-title">{t("teamProgress")}</h2>
        </div>
        <strong className="progress-percent">{data.completion_percent}%</strong>
      </div>
      <div className="progress-stat-grid">
        {stats.map(([label, value]) => (
          <div className="progress-stat" key={label}>
            <span>{label}</span>
            <strong className="progress-stat__value">{value}</strong>
          </div>
        ))}
      </div>
      <div className="decision-counts" aria-label={t("overallDecisions")}>
        {(["APPROVED", "REJECTED", "UNSURE"] as const).map((code) => (
          <span key={code}>{decisionLabel(locale, code)}: {data.decision_counts[code]}</span>
        ))}
        <span>{t("todayProgress")}{locale === "zh" ? "：" : ": "}{data.today_count}</span>
      </div>
      <div className="progress-table-wrap">
        <table className="progress-table">
          <thead><tr>
            <th scope="col">{t("member")}</th>
            <th scope="col">{t("memberCompleted")}</th>
            <th scope="col">{t("memberApproved")}</th>
            <th scope="col">{t("memberRejected")}</th>
            <th scope="col">{t("memberUnsure")}</th>
            <th scope="col">{t("memberToday")}</th>
          </tr></thead>
          <tbody>
            {data.members.map((member) => (
              <tr key={member.name}>
                <th scope="row">{member.name}</th>
                <td>{member.completed}</td><td>{member.approved}</td><td>{member.rejected}</td>
                <td>{member.unsure}</td><td>{member.today}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="progress-explanation">{t("progressExplanation")}</p>
    </section>
  );
}
