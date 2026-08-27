import { useEffect, useRef, useState } from "react";

import type { DecisionPayload, RejectionReasonCode } from "../api/types";
import { rejectionReasonLabel, REJECTION_REASONS } from "../i18n/catalog";
import { useI18n } from "../i18n/I18nProvider";
import { PillChoiceGroup } from "./PillChoiceGroup";

interface DecisionPanelProps {
  onSubmit: (payload: DecisionPayload) => void;
  pending: boolean;
  onPayloadChange?: (payload: DecisionPayload) => void;
  resetSignal?: number;
  selectedPayload?: DecisionPayload | null;
}

export function DecisionPanel({
  onSubmit,
  pending,
  onPayloadChange,
  resetSignal = 0,
  selectedPayload = null,
}: DecisionPanelProps) {
  const { locale, t } = useI18n();
  const [rejectOpen, setRejectOpen] = useState(false);
  const [reason, setReason] = useState<RejectionReasonCode | null>(null);
  const [notes, setNotes] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);
  const [rejectFocusRequest, setRejectFocusRequest] = useState(0);
  const rejectGroup = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setRejectOpen(false);
    setReason(null);
    setNotes("");
    setValidationError(null);
  }, [resetSignal]);

  useEffect(() => {
    if (selectedPayload?.decision === "REJECTED") {
      setRejectOpen(true);
      setReason(selectedPayload.rejection_reason);
      setNotes(selectedPayload.notes ?? "");
      setValidationError(null);
    } else if (selectedPayload) {
      clearRejectDraft();
    }
  }, [selectedPayload?.decision, selectedPayload?.notes, selectedPayload?.rejection_reason]);

  useEffect(() => {
    if (!rejectOpen) return;
    const selected = rejectGroup.current?.querySelector<HTMLButtonElement>(
      '[role="radio"][aria-checked="true"]',
    );
    (selected ?? rejectGroup.current?.querySelector<HTMLButtonElement>('[role="radio"]'))?.focus();
  }, [rejectFocusRequest, rejectOpen]);

  function openReject() {
    setRejectOpen(true);
    setValidationError(null);
    setRejectFocusRequest((request) => request + 1);
  }

  function clearRejectDraft() {
    setRejectOpen(false);
    setReason(null);
    setNotes("");
    setValidationError(null);
  }

  function submitSimpleDecision(decision: "APPROVED" | "UNSURE") {
    clearRejectDraft();
    onSubmit(simplePayload(decision));
  }

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
          submitSimpleDecision("APPROVED");
          break;
        case "u":
          submitSimpleDecision("UNSURE");
          break;
        case "r":
          openReject();
          break;
      }
    }
    document.addEventListener("keydown", handleShortcut);
    return () => document.removeEventListener("keydown", handleShortcut);
  }, [onSubmit, pending]);

  function chooseReason(nextReason: RejectionReasonCode) {
    const payload: DecisionPayload = {
      decision: "REJECTED",
      rejection_reason: nextReason,
      notes: nextReason === "OTHER" ? notes.trim() || null : null,
    };
    setReason(nextReason);
    setValidationError(null);
    if (nextReason !== "OTHER") setNotes("");
    onPayloadChange?.(payload);
    if (nextReason !== "OTHER") onSubmit(payload);
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
    const trimmedNotes = notes.trim();
    if (!trimmedNotes) {
      setValidationError(t("otherNotesRequired"));
      return;
    }
    onSubmit({
      decision: "REJECTED",
      rejection_reason: "OTHER",
      notes: trimmedNotes,
    });
  }

  const rejectSelected =
    selectedPayload?.decision === "REJECTED" || (selectedPayload === null && reason !== null);

  return (
    <section className="decision-panel" aria-label={t("reviewTitle")}>
      <div className="decision-actions">
        <button
          className={`decision-button decision-button--keep${
            selectedPayload?.decision === "APPROVED" ? " decision-button--selected" : ""
          }`}
          type="button"
          disabled={pending}
          aria-label={t("keep")}
          aria-pressed={selectedPayload?.decision === "APPROVED"}
          onClick={() => submitSimpleDecision("APPROVED")}
        >
          <DecisionButtonContent
            label={t("keep")}
            pending={pending && selectedPayload?.decision === "APPROVED"}
            selected={selectedPayload?.decision === "APPROVED"}
            savingLabel={t("saving")}
          />
        </button>
        <button
          className={`decision-button decision-button--reject${rejectSelected ? " decision-button--selected" : ""}`}
          type="button"
          disabled={pending}
          aria-label={t("reject")}
          aria-pressed={rejectSelected}
          onClick={openReject}
        >
          <DecisionButtonContent
            label={t("reject")}
            pending={pending && selectedPayload?.decision === "REJECTED"}
            selected={rejectSelected}
            savingLabel={t("saving")}
          />
        </button>
        <button
          className={`decision-button decision-button--unsure${
            selectedPayload?.decision === "UNSURE" ? " decision-button--selected" : ""
          }`}
          type="button"
          disabled={pending}
          aria-label={t("unsure")}
          aria-pressed={selectedPayload?.decision === "UNSURE"}
          onClick={() => submitSimpleDecision("UNSURE")}
        >
          <DecisionButtonContent
            label={t("unsure")}
            pending={pending && selectedPayload?.decision === "UNSURE"}
            selected={selectedPayload?.decision === "UNSURE"}
            savingLabel={t("saving")}
          />
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
            <>
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
                  }}
                >
                  {t("cancelReject")}
                </button>
              </div>
            </>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function DecisionButtonContent({
  label,
  pending,
  savingLabel,
  selected,
}: {
  label: string;
  pending: boolean;
  savingLabel: string;
  selected: boolean;
}) {
  if (pending) {
    return <span className="decision-button__spinner" role="status" aria-label={savingLabel} />;
  }
  return (
    <>
      {selected ? <span aria-hidden="true">✓ </span> : null}
      {label}
    </>
  );
}

function simplePayload(decision: "APPROVED" | "UNSURE"): DecisionPayload {
  return { decision, rejection_reason: null, notes: null };
}

const INTERACTIVE_ROLES = new Set([
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
  "menuitemcheckbox",
  "menuitemradio",
  "option",
  "radio",
  "radiogroup",
  "searchbox",
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
]);

const NATIVE_INTERACTIVE_SELECTOR = [
  "input",
  "textarea",
  "select",
  "button",
  "a",
  "area",
  "label",
  "summary",
  "audio[controls]",
  "video[controls]",
  "iframe",
  "embed",
  "object",
].join(", ");

function isInteractiveTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  for (let element: Element | null = target; element; element = element.parentElement) {
    if (element.matches(NATIVE_INTERACTIVE_SELECTOR)) return true;
    const contentEditable = element.getAttribute("contenteditable");
    if (contentEditable !== null && contentEditable.toLowerCase() !== "false") return true;
    if (element.hasAttribute("tabindex")) return true;
    const roles = (element.getAttribute("role") ?? "").toLowerCase().split(/\s+/);
    if (roles.some((role) => INTERACTIVE_ROLES.has(role))) return true;
  }
  return false;
}
