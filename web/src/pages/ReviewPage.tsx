import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, request } from "../api/client";
import {
  parseCandidateResponse,
  parseReviewResponse,
  type CandidateResponse,
  type DecisionPayload,
} from "../api/types";
import { DecisionPanel } from "../components/DecisionPanel";
import { ImageStage } from "../components/ImageStage";
import { ProgressSummary } from "../components/ProgressSummary";
import { ReviewGuidelinesDialog } from "../components/ReviewGuidelinesDialog";
import { sourceLabel } from "../i18n/catalog";
import { useI18n } from "../i18n/I18nProvider";
import {
  hasSeenReviewGuidelines,
  markReviewGuidelinesSeen,
} from "../review/guidelinesSession";

interface ReviewPageProps {
  csrfToken: string;
  reviewerId: string;
  retryBootstrap: () => Promise<void>;
}

type CurrentStatus = "loading" | "ready" | "empty" | "error" | "auth-refresh";
type DecisionError = "ambiguous" | "rejected" | "conflict" | null;

interface DecisionOperation {
  candidateId: string;
  key: string;
  payload: DecisionPayload;
  fingerprint: string;
}

const DEFINITIVE_DECISION_REJECTION_STATUSES = new Set([400, 404, 410, 413, 415, 422]);

export function ReviewPage({ csrfToken, reviewerId, retryBootstrap }: ReviewPageProps) {
  const { locale, t } = useI18n();
  const [candidate, setCandidate] = useState<CandidateResponse | null>(null);
  const [currentStatus, setCurrentStatus] = useState<CurrentStatus>("loading");
  const [pending, setPending] = useState(false);
  const [decisionError, setDecisionError] = useState<DecisionError>(null);
  const [operation, setOperation] = useState<DecisionOperation | null>(null);
  const [retryPayload, setRetryPayload] = useState<DecisionPayload | null>(null);
  const [sessionCompleted, setSessionCompleted] = useState(0);
  const [panelResetSignal, setPanelResetSignal] = useState(0);
  const [showGuidelines, setShowGuidelines] = useState(
    () => !hasSeenReviewGuidelines(reviewerId),
  );
  const currentGeneration = useRef(0);
  const currentController = useRef<AbortController | null>(null);
  const decisionGeneration = useRef(0);
  const decisionController = useRef<AbortController | null>(null);
  const pendingRef = useRef(false);
  const operationRef = useRef<DecisionOperation | null>(null);

  const replaceOperation = useCallback((next: DecisionOperation | null) => {
    operationRef.current = next;
    setOperation(next);
  }, []);

  const loadCurrent = useCallback(async () => {
    const generation = currentGeneration.current + 1;
    currentGeneration.current = generation;
    currentController.current?.abort();
    const controller = new AbortController();
    currentController.current = controller;
    setCandidate(null);
    setCurrentStatus("loading");
    try {
      const raw = await request<unknown>("/reviews/current", {
        method: "POST",
        csrfToken,
        signal: controller.signal,
      });
      if (controller.signal.aborted || generation !== currentGeneration.current) return;
      if (raw === undefined) {
        setCurrentStatus("empty");
        return;
      }
      const nextCandidate = parseCandidateResponse(raw);
      if (controller.signal.aborted || generation !== currentGeneration.current) return;
      setCandidate(nextCandidate);
      setCurrentStatus("ready");
    } catch (error) {
      if (controller.signal.aborted || generation !== currentGeneration.current) return;
      setCandidate(null);
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        setCurrentStatus("auth-refresh");
        await retryBootstrap();
      } else {
        setCurrentStatus("error");
      }
    } finally {
      if (currentController.current === controller) currentController.current = null;
    }
  }, [csrfToken, retryBootstrap]);

  useEffect(() => {
    void loadCurrent();
    return () => {
      currentGeneration.current += 1;
      currentController.current?.abort();
      currentController.current = null;
      decisionGeneration.current += 1;
      decisionController.current?.abort();
      decisionController.current = null;
      pendingRef.current = false;
    };
  }, [loadCurrent]);

  const submitDecision = useCallback(async (payload: DecisionPayload) => {
    if (pendingRef.current || !candidate) return;
    const fingerprint = JSON.stringify(payload);
    const previous = operationRef.current;
    const nextOperation =
      previous?.candidateId === candidate.id && previous.fingerprint === fingerprint
        ? previous
        : {
            candidateId: candidate.id,
            key: crypto.randomUUID(),
            payload,
            fingerprint,
          };
    replaceOperation(nextOperation);
    pendingRef.current = true;
    setPending(true);
    setDecisionError(null);
    setRetryPayload(null);
    const generation = decisionGeneration.current + 1;
    decisionGeneration.current = generation;
    decisionController.current?.abort();
    const controller = new AbortController();
    decisionController.current = controller;

    try {
      const raw = await request<unknown>(`/reviews/${nextOperation.candidateId}/decision`, {
        method: "POST",
        body: nextOperation.payload,
        csrfToken,
        signal: controller.signal,
        headers: { "Idempotency-Key": nextOperation.key },
      });
      if (controller.signal.aborted || generation !== decisionGeneration.current) return;
      parseReviewResponse(raw, {
        candidateId: nextOperation.candidateId,
        reviewerId,
        payload: nextOperation.payload,
      });
      replaceOperation(null);
      setRetryPayload(null);
      setDecisionError(null);
      setSessionCompleted((count) => count + 1);
      setPanelResetSignal((signal) => signal + 1);
      setCandidate(null);
      await loadCurrent();
    } catch (error) {
      if (controller.signal.aborted || generation !== decisionGeneration.current) return;
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        setDecisionError(null);
        setRetryPayload(null);
        await retryBootstrap();
      } else if (error instanceof ApiError && error.status === 409) {
        replaceOperation(null);
        setRetryPayload(null);
        setDecisionError("conflict");
        setPanelResetSignal((signal) => signal + 1);
        await loadCurrent();
      } else if (
        error instanceof ApiError &&
        DEFINITIVE_DECISION_REJECTION_STATUSES.has(error.status)
      ) {
        replaceOperation(null);
        setRetryPayload(nextOperation.payload);
        setDecisionError("rejected");
      } else {
        setRetryPayload(nextOperation.payload);
        setDecisionError("ambiguous");
      }
    } finally {
      if (decisionController.current === controller) decisionController.current = null;
      if (generation === decisionGeneration.current) {
        pendingRef.current = false;
        setPending(false);
      }
    }
  }, [candidate, csrfToken, loadCurrent, replaceOperation, retryBootstrap, reviewerId]);

  function handlePayloadChange(nextPayload: DecisionPayload) {
    const active = operationRef.current;
    const preserved = active?.payload ?? retryPayload;
    if (!preserved) return;
    if (JSON.stringify(nextPayload) !== JSON.stringify(preserved)) {
      replaceOperation(null);
      setRetryPayload(null);
      setDecisionError(null);
    }
  }

  function confirmGuidelines() {
    markReviewGuidelinesSeen(reviewerId);
    setShowGuidelines(false);
  }

  if (showGuidelines) {
    return (
      <main className="review-workspace">
        <div className="review-heading">
          <div>
            <p className="eyebrow">SukaSeafood</p>
            <h1>{t("reviewTitle")}</h1>
          </div>
        </div>
        <ReviewGuidelinesDialog onConfirm={confirmGuidelines} />
      </main>
    );
  }

  const selectedPayload = operation?.payload ?? retryPayload;

  const commonName = candidate
    ? locale === "zh"
      ? candidate.species.name_zh
      : candidate.species.name_en
    : "";
  const imageAlt = candidate
    ? `${commonName} (${candidate.species.scientific_name})`
    : "";

  return (
    <main className="review-workspace">
      <div className="review-heading">
        <div>
          <p className="eyebrow">SukaSeafood</p>
          <h1>{t("reviewTitle")}</h1>
        </div>
        <ProgressSummary sessionCompleted={sessionCompleted} />
      </div>

      {decisionError === "ambiguous" || decisionError === "rejected" ? (
        <div className="notice notice--error review-notice" role="alert">
          <span>{decisionError === "rejected" ? t("decisionRejected") : t("decisionAmbiguous")}</span>
          <div className="inline-actions">
            {operation || retryPayload ? (
              <button
                className="text-button"
                type="button"
                disabled={pending}
                onClick={() => void submitDecision((operation ?? { payload: retryPayload! }).payload)}
              >
                {t("retrySave")}
              </button>
            ) : null}
            <button
              className="text-button"
              type="button"
              disabled={pending}
              onClick={() => {
                setDecisionError(null);
              }}
            >
              {t("cancelRetry")}
            </button>
          </div>
        </div>
      ) : null}
      {decisionError === "conflict" ? (
        <div className="notice notice--error review-notice" role="alert">{t("assignmentConflict")}</div>
      ) : null}

      {currentStatus === "loading" ? (
        <div className="review-state" aria-busy="true">
          <span className="spinner" aria-hidden="true" />
          <p role="status">{t("loadingCurrent")}</p>
        </div>
      ) : null}
      {currentStatus === "auth-refresh" ? (
        <div className="review-state" aria-busy="true"><span className="spinner" aria-hidden="true" /></div>
      ) : null}
      {currentStatus === "empty" ? (
        <div className="review-state"><p role="status">{t("emptyPool")}</p></div>
      ) : null}
      {currentStatus === "error" ? (
        <div className="review-state">
          <div className="notice notice--error" role="alert">{t("currentError")}</div>
          <button className="primary-button compact-button" type="button" onClick={() => void loadCurrent()}>
            {t("retryLoad")}
          </button>
        </div>
      ) : null}

      {currentStatus === "ready" && candidate ? (
        <article className="candidate-card">
          <section className="candidate-visual">
            <ImageStage
              key={candidate.id}
              previewUrl={candidate.preview_url}
              originalUrl={candidate.original_url}
              sourceUrl={candidate.source_url}
              alt={imageAlt}
              pending={pending}
              imageUnavailableSelected={
                selectedPayload?.decision === "REJECTED" &&
                selectedPayload.rejection_reason === "IMAGE_URL_UNAVAILABLE"
              }
              onImageUnavailable={() =>
                void submitDecision({
                  decision: "REJECTED",
                  rejection_reason: "IMAGE_URL_UNAVAILABLE",
                  notes: null,
                })
              }
            />
          </section>
          <section className="candidate-details">
            <div className="species-title">
              <p className="species-code">{candidate.species.code}</p>
              <h2>{commonName} <em>{candidate.species.scientific_name}</em></h2>
            </div>
            <dl className="metadata-grid">
              <Metadata label={t("source")} value={sourceLabel(locale, candidate.source_dataset)} />
              <Metadata label={t("sourceRecord")} value={candidate.source_record_id} />
              {candidate.creator ? <Metadata label={t("creator")} value={candidate.creator} /> : null}
              <Metadata label={t("license")} value={candidate.license} href={candidate.license_url} />
              <Metadata label={t("attribution")} value={candidate.attribution} />
              {candidate.location ? <Metadata label={t("location")} value={candidate.location} /> : null}
              {candidate.observed_on ? <Metadata label={t("observedOn")} value={candidate.observed_on} /> : null}
            </dl>
            <DecisionPanel
              onSubmit={(payload) => void submitDecision(payload)}
              pending={pending}
              onPayloadChange={handlePayloadChange}
              resetSignal={panelResetSignal}
              selectedPayload={selectedPayload}
            />
          </section>
        </article>
      ) : null}
    </main>
  );
}

function Metadata({ label, value, href }: { label: string; value: string; href?: string | null }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>
        {href ? <a href={href} target="_blank" rel="noopener noreferrer">{value}</a> : value}
      </dd>
    </div>
  );
}
