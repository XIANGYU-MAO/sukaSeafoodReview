import { useEffect, useRef } from "react";

import type { MessageKey } from "../i18n/catalog";
import { useI18n } from "../i18n/I18nProvider";

interface ReviewGuidelinesDialogProps {
  onConfirm: () => void;
}

const APPROVE_KEYS = [
  "guidelinesApproveSpecies",
  "guidelinesApproveVisible",
  "guidelinesApproveRealFish",
  "guidelinesApproveMultiple",
] as const satisfies readonly MessageKey[];

const REJECT_KEYS = [
  "guidelinesRejectSpecies",
  "guidelinesRejectSchool",
  "guidelinesRejectMixed",
  "guidelinesRejectVisibility",
  "guidelinesRejectProcessed",
] as const satisfies readonly MessageKey[];

export function ReviewGuidelinesDialog({ onConfirm }: ReviewGuidelinesDialogProps) {
  const { locale, t, toggleLocale } = useI18n();
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    confirmRef.current?.focus();
  }, []);

  return (
    <div className="guidelines-backdrop" data-testid="review-guidelines-backdrop">
      <section
        className="guidelines-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="review-guidelines-title"
      >
        <div className="guidelines-dialog__toolbar">
          <button className="secondary-button language-toggle" type="button" onClick={toggleLocale}>
            {locale === "zh" ? "English" : "中文"}
          </button>
        </div>
        <h2 id="review-guidelines-title">{t("guidelinesTitle")}</h2>
        <div className="guidelines-dialog__sections">
          <section className="guidelines-list guidelines-list--approve">
            <h3>{t("guidelinesApproveLabel")}</h3>
            <ul>{APPROVE_KEYS.map((key) => <li key={key}>{t(key)}</li>)}</ul>
          </section>
          <section className="guidelines-list guidelines-list--reject">
            <h3>{t("guidelinesRejectLabel")}</h3>
            <ul>{REJECT_KEYS.map((key) => <li key={key}>{t(key)}</li>)}</ul>
          </section>
        </div>
        <button ref={confirmRef} className="primary-button guidelines-confirm" type="button" onClick={onConfirm}>
          {t("guidelinesConfirm")}
        </button>
      </section>
    </div>
  );
}
