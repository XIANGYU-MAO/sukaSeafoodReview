import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, request } from "../api/client";
import {
  DECISION_CODES,
  parseHistoryEditResponse,
  parseHistoryResponse,
  parseLatestReviewResponse,
  type DecisionCode,
  type DecisionPayload,
  type HistoryItem,
  type HistoryResponse,
  type RejectionReasonCode,
} from "../api/types";
import { PillChoiceGroup } from "../components/PillChoiceGroup";
import { ScreenLoader } from "../components/ScreenLoader";
import {
  decisionLabel,
  rejectionReasonLabel,
  REJECTION_REASONS,
  sourceLabel,
} from "../i18n/catalog";
import { useI18n } from "../i18n/I18nProvider";

interface HistoryPageProps {
  csrfToken: string;
  reviewerId: string;
  retryBootstrap: () => Promise<void>;
}

interface HistoryFilters {
  species_code: string;
  source_dataset: string;
  decision: "" | DecisionCode;
  date_from: string;
  date_to: string;
}

interface EditDraft {
  decision: DecisionCode;
  rejectionReason: RejectionReasonCode | null;
  notes: string;
}

type LoadStatus = "loading" | "ready" | "error" | "auth-refresh";
type EditError = "failure" | "stale" | "conflict" | null;

const EMPTY_FILTERS: HistoryFilters = {
  species_code: "",
  source_dataset: "",
  decision: "",
  date_from: "",
  date_to: "",
};
const PAGE_LIMIT = 20;
const MAX_DATE = "9998-12-31";

export function HistoryPage({ csrfToken, reviewerId, retryBootstrap }: HistoryPageProps) {
  const { locale, t } = useI18n();
  const [draftFilters, setDraftFilters] = useState<HistoryFilters>(EMPTY_FILTERS);
  const [appliedFilters, setAppliedFilters] = useState<HistoryFilters>(EMPTY_FILTERS);
  const [offset, setOffset] = useState(0);
  const [history, setHistory] = useState<HistoryResponse | null>(null);
  const [status, setStatus] = useState<LoadStatus>("loading");
  const [filterError, setFilterError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState<EditDraft | null>(null);
  const [editPending, setEditPending] = useState(false);
  const [editError, setEditError] = useState<EditError>(null);
  const [failedPayload, setFailedPayload] = useState<(DecisionPayload & { version: number }) | null>(null);
  const [savedId, setSavedId] = useState<string | null>(null);
  const [readOnlyNoticeId, setReadOnlyNoticeId] = useState<string | null>(null);
  const [previewItem, setPreviewItem] = useState<HistoryItem | null>(null);
  const loadGeneration = useRef(0);
  const loadController = useRef<AbortController | null>(null);
  const editGeneration = useRef(0);
  const editController = useRef<AbortController | null>(null);
  const editPendingRef = useRef(false);

  const loadHistory = useCallback(async () => {
    const generation = loadGeneration.current + 1;
    loadGeneration.current = generation;
    loadController.current?.abort();
    const controller = new AbortController();
    loadController.current = controller;
    setStatus("loading");
    const params = new URLSearchParams();
    for (const key of ["species_code", "source_dataset", "decision", "date_from", "date_to"] as const) {
      if (appliedFilters[key]) params.set(key, appliedFilters[key]);
    }
    params.set("limit", String(PAGE_LIMIT));
    params.set("offset", String(offset));
    try {
      const raw = await request<unknown>(`/history?${params.toString()}`, { signal: controller.signal });
      const validated = parseHistoryResponse(raw, reviewerId);
      if (controller.signal.aborted || generation !== loadGeneration.current) return false;
      if (validated.items.length === 0 && offset > 0 && validated.total <= offset) {
        setOffset(Math.floor(Math.max(0, validated.total - 1) / PAGE_LIMIT) * PAGE_LIMIT);
        return false;
      }
      setHistory(validated);
      setStatus("ready");
      return true;
    } catch (error) {
      if (controller.signal.aborted || generation !== loadGeneration.current) return;
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        setStatus("auth-refresh");
        await retryBootstrap();
      } else {
        setStatus("error");
      }
      return false;
    } finally {
      if (loadController.current === controller) loadController.current = null;
    }
  }, [appliedFilters, offset, reviewerId, retryBootstrap]);

  useEffect(() => {
    void loadHistory();
  }, [loadHistory]);

  useEffect(() => () => {
    loadGeneration.current += 1;
    loadController.current?.abort();
    editGeneration.current += 1;
    editController.current?.abort();
    editPendingRef.current = false;
  }, []);

  useEffect(() => {
    if (!previewItem) return;
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setPreviewItem(null);
    }
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [previewItem]);

  function updateFilter(key: keyof HistoryFilters, value: string) {
    setDraftFilters((current) => ({ ...current, [key]: value } as HistoryFilters));
    setFilterError(null);
  }

  function applyFilters() {
    if (
      (draftFilters.date_from && draftFilters.date_to && draftFilters.date_from > draftFilters.date_to) ||
      (draftFilters.date_to && draftFilters.date_to > MAX_DATE)
    ) {
      setFilterError(t("invalidDateRange"));
      return;
    }
    setFilterError(null);
    setOffset(0);
    setAppliedFilters({ ...draftFilters });
  }

  function resetFilters() {
    setFilterError(null);
    setDraftFilters(EMPTY_FILTERS);
    setOffset(0);
    setAppliedFilters(EMPTY_FILTERS);
  }

  function beginEdit(item: HistoryItem) {
    if (!item.is_current || item.read_only) return;
    editGeneration.current += 1;
    editController.current?.abort();
    setEditingId(item.id);
    setEditDraft(draftFromItem(item));
    setEditError(null);
    setFailedPayload(null);
    setSavedId(null);
  }

  function closeEdit() {
    editGeneration.current += 1;
    editController.current?.abort();
    editPendingRef.current = false;
    setEditPending(false);
    setEditingId(null);
    setEditDraft(null);
    setEditError(null);
    setFailedPayload(null);
  }

  function changeEditDraft(next: EditDraft) {
    setEditDraft(next);
    setEditError(null);
    setFailedPayload(null);
  }

  async function submitEdit(payloadOverride?: DecisionPayload & { version: number }) {
    if (editPendingRef.current || !history || !editingId || !editDraft) return;
    const item = history.items.find((entry) => entry.id === editingId);
    if (!item || item.read_only || !item.is_current) return;
    const notes = editDraft.notes.trim();
    if (!payloadOverride && editDraft.decision === "REJECTED" && editDraft.rejectionReason === "OTHER" && !notes) {
      setEditError("failure");
      return;
    }
    if (!payloadOverride && editDraft.decision === "REJECTED" && editDraft.rejectionReason === null) {
      setEditError("failure");
      return;
    }
    const payload = payloadOverride ?? {
      version: item.version,
      decision: editDraft.decision,
      rejection_reason: editDraft.decision === "REJECTED" ? editDraft.rejectionReason : null,
      notes: editDraft.decision === "REJECTED" && editDraft.rejectionReason === "OTHER" ? notes : null,
    };
    editPendingRef.current = true;
    setEditPending(true);
    setEditError(null);
    const generation = editGeneration.current + 1;
    editGeneration.current = generation;
    editController.current?.abort();
    const controller = new AbortController();
    editController.current = controller;
    try {
      const raw = await request<unknown>(`/history/${item.id}`, {
        method: "PATCH",
        body: payload,
        csrfToken,
        signal: controller.signal,
      });
      if (controller.signal.aborted || generation !== editGeneration.current) return;
      parseHistoryEditResponse(raw, {
        reviewId: item.id,
        candidateId: item.candidate_id,
        reviewerId,
        payload,
      });
      setEditingId(null);
      setEditDraft(null);
      setFailedPayload(null);
      setEditError(null);
      const refreshed = await loadHistory();
      if (refreshed) setSavedId(item.id);
    } catch (error) {
      if (controller.signal.aborted || generation !== editGeneration.current) return;
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        await retryBootstrap();
      } else if (error instanceof ApiError && error.status === 409) {
        handleConflict(error, item);
      } else {
        setFailedPayload(payload);
        setEditError("failure");
      }
    } finally {
      if (editController.current === controller) editController.current = null;
      if (generation === editGeneration.current) {
        editPendingRef.current = false;
        setEditPending(false);
      }
    }
  }

  function handleConflict(error: ApiError, item: HistoryItem) {
    const detail = conflictDetail(error.body);
    if (detail?.code === "REVIEW_NOT_CURRENT") {
      setHistory((current) => current && ({
        ...current,
        items: current.items.map((entry) => entry.id === item.id
          ? { ...entry, is_current: false, read_only: true }
          : entry),
      }));
      setReadOnlyNoticeId(item.id);
      setEditingId(null);
      setEditDraft(null);
      setFailedPayload(null);
      setEditError(null);
      return;
    }
    if (detail?.code === "STALE_REVIEW_VERSION") {
      try {
        const latest = parseLatestReviewResponse(detail.latest, {
          reviewId: item.id,
          candidateId: item.candidate_id,
          reviewerId,
        });
        setHistory((current) => current && ({
          ...current,
          items: current.items.map((entry) => entry.id === item.id ? {
            ...entry,
            decision: latest.decision,
            rejection_reason: latest.rejection_reason,
            notes: latest.notes,
            whole_fish: latest.whole_fish,
            exact_species_verified: latest.exact_species_verified,
            is_current: true,
            read_only: false,
            version: latest.version,
          } : entry),
        }));
        setEditDraft({
          decision: latest.decision,
          rejectionReason: latest.rejection_reason,
          notes: latest.rejection_reason === "OTHER" ? latest.notes ?? "" : "",
        });
        setFailedPayload(null);
        setEditError("stale");
        return;
      } catch {
        // Malformed conflicts remain opaque and retryable.
      }
    }
    setFailedPayload(null);
    setEditError("conflict");
  }

  const total = history?.total ?? 0;
  const pageNumber = Math.floor(offset / PAGE_LIMIT) + 1;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_LIMIT));

  return (
    <main className="history-workspace">
      <div className="history-heading">
        <p className="eyebrow">SukaSeafood</p>
        <h1>{t("historyTitle")}</h1>
      </div>
      <section className="history-filters" aria-label={t("filters")}>
        <label>{t("species")}
          <select aria-label={t("species")} value={draftFilters.species_code} onChange={(event) => updateFilter("species_code", event.target.value)}>
            <option value="">{t("allSpecies")}</option>
            {(history?.filters.species ?? []).map((species) => (
              <option key={species.code} value={species.code}>
                {(locale === "zh" ? species.name_zh : species.name_en)} ({species.scientific_name})
              </option>
            ))}
          </select>
        </label>
        <label>{t("source")}
          <select aria-label={t("source")} value={draftFilters.source_dataset} onChange={(event) => updateFilter("source_dataset", event.target.value)}>
            <option value="">{t("allSources")}</option>
            {(history?.filters.sources ?? []).map((source) => <option key={source} value={source}>{sourceLabel(locale, source)}</option>)}
          </select>
        </label>
        <label>{t("decisionFilter")}
          <select aria-label={locale === "zh" ? "结果" : "Result"} value={draftFilters.decision} onChange={(event) => updateFilter("decision", event.target.value)}>
            <option value="">{t("allDecisions")}</option>
            {DECISION_CODES.map((decision) => <option key={decision} value={decision}>{decisionLabel(locale, decision)}</option>)}
          </select>
        </label>
        <label>{t("startDate")}<input type="date" aria-label={t("startDate")} max={MAX_DATE} value={draftFilters.date_from} onChange={(event) => updateFilter("date_from", event.target.value)} /></label>
        <label>{t("endDate")}<input type="date" aria-label={t("endDate")} max={MAX_DATE} value={draftFilters.date_to} onChange={(event) => updateFilter("date_to", event.target.value)} /></label>
        <div className="history-filter-actions">
          <button className="primary-button compact-button" type="button" onClick={applyFilters}>{t("applyFilters")}</button>
          <button className="secondary-button" type="button" onClick={resetFilters}>{t("resetFilters")}</button>
        </div>
      </section>
      {filterError ? <div className="notice notice--error" role="alert">{filterError}</div> : null}
      {status === "error" ? (
        <div className="notice notice--error" role="alert">
          <span>{t("historyLoadError")}</span>{" "}
          <button className="text-button" type="button" onClick={() => void loadHistory()}>{t("retryHistory")}</button>
        </div>
      ) : null}
      {status === "loading" && history === null || status === "auth-refresh"
        ? <ScreenLoader label={t("loadingHistory")} />
        : null}
      {status !== "auth-refresh" && history?.items.length === 0 ? <p className="empty-history" role="status">{t("emptyHistory")}</p> : null}
      <section className="history-list">
        {history?.items.map((item) => (
          <article className="history-card" key={item.id}>
            <button
              className="history-thumbnail-viewer history-thumbnail-viewer--cropped"
              type="button"
              aria-label={t("viewFullImage")}
              onClick={() => setPreviewItem(item)}
            >
              <img
                className="history-thumbnail"
                src={item.preview_url}
                alt={`${locale === "zh" ? item.species.name_zh : item.species.name_en} (${item.species.scientific_name})`}
                loading="lazy"
                referrerPolicy="no-referrer"
              />
              <span className="history-enlarge-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24" focusable="false">
                  <circle cx="10.5" cy="10.5" r="6.5" />
                  <path d="m15.5 15.5 4 4M7.5 10.5h6M10.5 7.5v6" />
                </svg>
              </span>
            </button>
            <div className="history-card__body">
              <div className="history-card__title">
                <div><span className="species-code">{item.species.code}</span><h2>{locale === "zh" ? item.species.name_zh : item.species.name_en} <em>{item.species.scientific_name}</em></h2></div>
                <strong className={`history-decision history-decision--${item.decision.toLowerCase()}`}>
                  {decisionLabel(locale, item.decision)}
                </strong>
              </div>
              <div className="history-card__facts">
                <div className="history-card__origin">
                  <p className="history-card__source" title={`${sourceLabel(locale, item.source_dataset)} · ${item.source_record_id}`}>
                    {sourceLabel(locale, item.source_dataset)} · {item.source_record_id}
                  </p>
                  <div className="history-links">
                    <a href={item.source_url} target="_blank" rel="noopener noreferrer">{t("sourcePage")}</a>
                    <a href={item.original_url} target="_blank" rel="noopener noreferrer">{t("originalImage")}</a>
                  </div>
                </div>
                <div className="history-card__timestamps">
                  <p><strong>{t("createdAt")}:</strong> <time dateTime={item.created_at}>{displayTimestamp(item.created_at)}</time></p>
                  <p><strong>{t("updatedAt")}:</strong> <time dateTime={item.updated_at}>{displayTimestamp(item.updated_at)}</time></p>
                </div>
              </div>
              <div className="history-card__actions">
                <div className="history-card__messages">
                  {readOnlyNoticeId === item.id ? <p className="read-only-note">{t("noLongerCurrent")}</p> : null}
                  {item.read_only || !item.is_current ? <p className="read-only-note">{t("readOnlyExplanation")}</p> : null}
                  {savedId === item.id ? <div className="notice notice--success" role="status">{t("editSaved")}</div> : null}
                </div>
                {!item.read_only && item.is_current && editingId !== item.id ? (
                  <button className="secondary-button" type="button" onClick={() => beginEdit(item)}>{t("edit")}</button>
                ) : null}
              </div>
              {editingId === item.id && editDraft ? (
                <HistoryEditor
                  draft={editDraft}
                  pending={editPending}
                  error={editError}
                  onChange={changeEditDraft}
                  onSubmit={() => void submitEdit()}
                  onRetry={() => failedPayload && void submitEdit(failedPayload)}
                  onCancel={closeEdit}
                />
              ) : null}
            </div>
          </article>
        ))}
      </section>
      {history && history.total > 0 ? (
        <nav className="history-pagination" aria-label={t("pageSummary")}>
          <button className="secondary-button" type="button" disabled={offset === 0 || status === "loading"} onClick={() => setOffset(Math.max(0, offset - PAGE_LIMIT))}>{t("previousPage")}</button>
          <span>{pageNumber} / {pageCount}</span>
          <button className="secondary-button" type="button" disabled={offset + PAGE_LIMIT >= total || status === "loading"} onClick={() => setOffset(offset + PAGE_LIMIT)}>{t("nextPage")}</button>
        </nav>
      ) : null}
      {previewItem ? (
        <div
          className="history-lightbox"
          role="dialog"
          aria-modal="true"
          aria-label={t("fullImage")}
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setPreviewItem(null);
          }}
        >
          <div className="history-lightbox__content" onMouseDown={(event) => event.stopPropagation()}>
            <button
              className="history-lightbox__close"
              type="button"
              aria-label={t("closeFullImage")}
              onClick={() => setPreviewItem(null)}
            >
              ×
            </button>
            <img
              src={previewItem.original_url}
              alt={`${locale === "zh" ? previewItem.species.name_zh : previewItem.species.name_en} (${previewItem.species.scientific_name})`}
              referrerPolicy="no-referrer"
            />
          </div>
        </div>
      ) : null}
    </main>
  );
}

function HistoryEditor({
  draft,
  pending,
  error,
  onChange,
  onSubmit,
  onRetry,
  onCancel,
}: {
  draft: EditDraft;
  pending: boolean;
  error: EditError;
  onChange: (draft: EditDraft) => void;
  onSubmit: () => void;
  onRetry: () => void;
  onCancel: () => void;
}) {
  const { locale, t } = useI18n();
  function chooseDecision(decision: DecisionCode) {
    onChange({
      decision,
      rejectionReason: decision === "REJECTED" ? draft.rejectionReason : null,
      notes: decision === "REJECTED" && draft.rejectionReason === "OTHER" ? draft.notes : "",
    });
  }
  const validationMessage =
    draft.decision === "REJECTED" && draft.rejectionReason === null
      ? t("chooseReason")
      : draft.decision === "REJECTED" && draft.rejectionReason === "OTHER" && !draft.notes.trim()
        ? t("otherNotesRequired")
        : null;
  return (
    <section className="history-editor" aria-label={t("editTitle")}>
      <h3>{t("editTitle")}</h3>
      <div className="history-decision-buttons">
        {(["APPROVED", "REJECTED", "UNSURE"] as const).map((decision) => (
          <button key={decision} type="button" disabled={pending} aria-pressed={draft.decision === decision} onClick={() => chooseDecision(decision)}>
            {decision === "APPROVED" ? t("keepShort") : decision === "REJECTED" ? t("rejectShort") : t("unsureShort")}
          </button>
        ))}
      </div>
      {draft.decision === "REJECTED" ? (
        <>
          <PillChoiceGroup
            label={t("rejectionReason")}
            options={REJECTION_REASONS}
            value={draft.rejectionReason}
            disabled={pending}
            onChange={(reason) => onChange({ ...draft, rejectionReason: reason, notes: reason === "OTHER" ? draft.notes : "" })}
            getOptionLabel={(reason) => rejectionReasonLabel(locale, reason)}
          />
          {draft.rejectionReason === "OTHER" ? (
            <label className="input-label">{t("otherNotes")}
              <textarea className="text-input" maxLength={2_000} aria-label={t("otherNotes")} disabled={pending} value={draft.notes} onChange={(event) => onChange({ ...draft, notes: event.target.value })} />
            </label>
          ) : null}
        </>
      ) : null}
      {error === "failure" ? (
        <div className="notice notice--error" role="alert">
          {validationMessage ?? t("editFailed")}
          {!validationMessage ? <button className="text-button" type="button" disabled={pending} onClick={onRetry}>{t("retryEdit")}</button> : null}
        </div>
      ) : null}
      {error === "stale" ? <div className="notice notice--error" role="alert">{t("staleConflict")}</div> : null}
      {error === "conflict" ? <div className="notice notice--error" role="alert">{t("genericConflict")}</div> : null}
      <div className="history-edit-actions">
        <button className="primary-button compact-button" type="button" disabled={pending} onClick={onSubmit}>{pending ? t("saving") : t("saveEdit")}</button>
        <button className="secondary-button" type="button" disabled={pending} onClick={onCancel}>{t("cancelEdit")}</button>
      </div>
    </section>
  );
}

function draftFromItem(item: HistoryItem): EditDraft {
  return {
    decision: item.decision,
    rejectionReason: item.rejection_reason,
    notes: item.rejection_reason === "OTHER" ? item.notes ?? "" : "",
  };
}

function displayTimestamp(value: string): string {
  return value.replace("T", " ").replace(/Z$/, " UTC");
}

function conflictDetail(body: unknown): { code: string; latest?: unknown } | null {
  if (!isRecord(body) || !isRecord(body.detail) || typeof body.detail.code !== "string") return null;
  return { code: body.detail.code, latest: body.detail.latest };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
