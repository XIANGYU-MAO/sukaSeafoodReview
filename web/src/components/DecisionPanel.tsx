import { useEffect, useRef, useState } from "react";

import type { DecisionPayload, RejectionReasonCode } from "../api/types";
import { rejectionReasonLabel, REJECTION_REASONS } from "../i18n/catalog";
import { useI18n } from "../i18n/I18nProvider";
import { PillChoiceGroup } from "./PillChoiceGroup";

interface DecisionPanelProps {
  onSubmit: (payload: DecisionPayload) => void;
  pending: boolean;
  onPayloadChange?: (payload: DecisionPayload | null) => void;
  resetSignal?: number;
}

export function DecisionPanel({
  onSubmit,
  pending,
  onPayloadChange,
  resetSignal = 0,
}: DecisionPanelProps) {
  const { locale, t } = useI18n();
  const [rejectOpen, setRejectOpen] = useState(false);
  const [reason, setReason] = useState<RejectionReasonCode | null>(null);
  const [notes, setNotes] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);
  const rejectGroup = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setRejectOpen(false);
    setReason(null);
    setNotes("");
    setValidationError(null);
  }, [resetSignal]);

  useEffect(() => {
    if (rejectOpen) {
      rejectGroup.current?.querySelector<HTMLButtonElement>('[role="radio"]')?.focus();
    }
  }, [rejectOpen]);

  useEffect(() => {
    function handleShortcut(event: KeyboardEvent) {
      if (
        pending ||
        event.repeat ||
        event.altKey ||
        event.ctrlKey ||
        event.metaKey ||
        event.shiftKey ||
        isInteractiveTarget(event.target)
      ) {
        return;
      }
      switch (event.key.toLowerCase()) {
        case "k":
          onSubmit(simplePayload("APPROVED"));
          break;
        case "u":
          onSubmit(simplePayload("UNSURE"));
          break;
        case "r":
          setRejectOpen(true);
          setValidationError(null);
          break;
      }
    }
    document.addEventListener("keydown", handleShortcut);
    return () => document.removeEventListener("keydown", handleShortcut);
  }, [onSubmit, pending]);

  function chooseReason(nextReason: RejectionReasonCode) {
    setReason(nextReason);
    setValidationError(null);
    if (nextReason !== "OTHER") setNotes("");
    onPayloadChange?.({
      decision: "REJECTED",
      rejection_reason: nextReason,
      notes: null,
    });
  }

  function changeNotes(nextNotes: string) {
    setNotes(nextNotes);
    if (reason === "OTHER") {
      onPayloadChange?.({
        decision: "REJECTED",
        rejection_reason: "OTHER",
        notes: nextNotes.trim() || null,
      });
    }
  }

  function confirmReject() {
    if (!reason) {
      setValidationError(t("chooseReason"));
      return;
    }
    const trimmedNotes = notes.trim();
    if (reason === "OTHER" && !trimmedNotes) {
      setValidationError(t("otherNotesRequired"));
      return;
    }
    onSubmit({
      decision: "REJECTED",
      rejection_reason: reason,
      notes: reason === "OTHER" ? trimmedNotes : null,
    });
  }

  return (
    <section className="decision-panel" aria-label={t("reviewTitle")}>
      <div className="decision-actions">
        <button
          className="decision-button decision-button--keep"
          type="button"
          disabled={pending}
          onClick={() => onSubmit(simplePayload("APPROVED"))}
        >
          {pending ? t("saving") : t("keep")}
        </button>
        <button
          className="decision-button decision-button--reject"
          type="button"
          disabled={pending}
          onClick={() => {
            setRejectOpen(true);
            setValidationError(null);
          }}
        >
          {t("reject")}
        </button>
        <button
          className="decision-button decision-button--unsure"
          type="button"
          disabled={pending}
          onClick={() => onSubmit(simplePayload("UNSURE"))}
        >
          {t("unsure")}
        </button>
      </div>
      {rejectOpen ? (
        <div className="rejection-panel" ref={rejectGroup}>
          <PillChoiceGroup
            label={t("rejectionReason")}
            options={REJECTION_REASONS}
            value={reason}
            onChange={chooseReason}
            disabled={pending}
            getOptionLabel={(code) => rejectionReasonLabel(locale, code)}
          />
          {reason === "OTHER" ? (
            <label className="input-label rejection-notes">
              {t("otherNotes")}
              <textarea
                className="text-input"
                aria-label={t("otherNotes")}
                value={notes}
                disabled={pending}
                maxLength={2_000}
                onChange={(event) => changeNotes(event.target.value)}
              />
            </label>
          ) : null}
          {validationError ? <div className="notice notice--error" role="alert">{validationError}</div> : null}
          <div className="rejection-actions">
            <button className="primary-button compact-button" type="button" disabled={pending} onClick={confirmReject}>
              {t("confirmReject")}
            </button>
            <button
              className="secondary-button"
              type="button"
              disabled={pending}
              onClick={() => {
                setRejectOpen(false);
                setValidationError(null);
                onPayloadChange?.(null);
              }}
            >
              {t("cancelReject")}
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function simplePayload(decision: "APPROVED" | "UNSURE"): DecisionPayload {
  return { decision, rejection_reason: null, notes: null };
}

const INTERACTIVE_ROLES = new Set([
  "button",
  "checkbox",
  "combobox",
  "link",
  "listbox",
  "menuitem",
  "option",
  "radio",
  "slider",
  "spinbutton",
  "switch",
  "tab",
  "textbox",
]);

function isInteractiveTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  if (target.closest("input, textarea, select, button, a, [contenteditable='true']")) return true;
  const roleElement = target.closest("[role]");
  return roleElement ? INTERACTIVE_ROLES.has(roleElement.getAttribute("role") ?? "") : false;
}
