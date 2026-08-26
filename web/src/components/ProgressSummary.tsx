import { useI18n } from "../i18n/I18nProvider";

export function ProgressSummary({ sessionCompleted }: { sessionCompleted: number }) {
  const { t } = useI18n();
  return (
    <aside className="session-progress" aria-label={t("progress")}>
      {`${t("sessionCompleted")} ${sessionCompleted} ${t("itemUnit")}`}
    </aside>
  );
}
