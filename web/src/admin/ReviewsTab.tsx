import { useMemo, useState } from "react";

import { ApiError } from "../api/client";
import type { DecisionCode, RejectionReasonCode } from "../api/types";
import { PillChoiceGroup } from "../components/PillChoiceGroup";
import {
  PageControls,
  QueryBoundary,
  adminMutation,
  decisionLabels,
  mutationMessage,
  rejectionLabels,
  sourceLabel,
  useAdminQuery,
  type AdminTabProps,
} from "./common";
import {
  parseAdminReviewList,
  parseCandidateReceipt,
  parseReviewReceipt,
  type AdminReviewItem,
} from "./types";

const LIMIT = 20;
type EditDraft = {
  decision: DecisionCode;
  rejection: RejectionReasonCode | null;
  notes: string;
  reason: string;
};

const EMPTY_REVIEW_FILTERS = {
  reviewer: "",
  species: "",
  source: "",
  decision: "",
  current: "",
  from: "",
  to: "",
};

export function ReviewsTab(props: AdminTabProps) {
  const [filters, setFilters] = useState({ ...EMPTY_REVIEW_FILTERS });
  const [applied, setApplied] = useState({ ...EMPTY_REVIEW_FILTERS });
  const [offset, setOffset] = useState(0);
  const [editing, setEditing] = useState<AdminReviewItem | null>(null);
  const [draft, setDraft] = useState<EditDraft | null>(null);
  const [reopening, setReopening] = useState<AdminReviewItem | null>(null);
  const [target, setTarget] = useState("");
  const [reopenReason, setReopenReason] = useState("");
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState<{
    kind: "error" | "success";
    text: string;
  } | null>(null);
  const path = useMemo(() => {
    const query = new URLSearchParams({ limit: String(LIMIT), offset: String(offset) });
    if (applied.reviewer) query.set("reviewer_id", applied.reviewer);
    if (applied.species) query.set("species_code", applied.species);
    if (applied.source) query.set("source_dataset", applied.source);
    if (applied.decision) query.set("decision", applied.decision);
    if (applied.current) query.set("current", applied.current);
    if (applied.from) query.set("date_from", applied.from);
    if (applied.to) query.set("date_to", applied.to);
    return `/admin/reviews?${query}`;
  }, [applied, offset]);
  const query = useAdminQuery(path, parseAdminReviewList, props.retryBootstrap);

  function resetFilters() {
    const alreadyUnfiltered = offset === 0 && Object.values(applied).every((value) => value === "");
    setFilters({ ...EMPTY_REVIEW_FILTERS });
    setApplied({ ...EMPTY_REVIEW_FILTERS });
    setOffset(0);
    setNotice(null);
    if (alreadyUnfiltered) query.reload();
  }

  function startEdit(item: AdminReviewItem) {
    setEditing(item);
    setDraft({
      decision: item.decision,
      rejection: item.rejection_reason,
      notes: item.notes ?? "",
      reason: "",
    });
    setNotice(null);
  }

  function validateDecision(current: EditDraft): {
    rejection_reason: RejectionReasonCode | null;
    notes: string | null;
  } | null {
    if (!current.reason.trim()) {
      setNotice({ kind: "error", text: "必须填写管理员修改原因。" });
      return null;
    }
    if (current.decision !== "REJECTED") {
      return { rejection_reason: null, notes: current.notes.trim() || null };
    }
    if (!current.rejection) {
      setNotice({ kind: "error", text: "请选择拒绝原因。" });
      return null;
    }
    if (current.rejection === "OTHER" && !current.notes.trim()) {
      setNotice({ kind: "error", text: "其他原因必须填写备注。" });
      return null;
    }
    return { rejection_reason: current.rejection, notes: current.notes.trim() || null };
  }

  async function saveEdit() {
    if (!editing || !draft || pending) return;
    const normalized = validateDecision(draft);
    if (!normalized) return;
    const body = {
      version: editing.version,
      decision: draft.decision,
      ...normalized,
      reason: draft.reason.trim(),
    };
    setPending(true);
    setNotice(null);
    try {
      const raw = await adminMutation<unknown>(
        `/admin/reviews/${editing.id}`,
        { method: "PATCH", body, csrfToken: props.csrfToken },
        props.retryBootstrap,
      );
      parseReviewReceipt(raw, {
        id: editing.id,
        candidateId: editing.candidate_id,
        reviewerId: editing.reviewer_id,
        previousVersion: editing.version,
        decision: draft.decision,
        rejectionReason: normalized.rejection_reason,
        notes: normalized.notes,
      });
      setNotice({ kind: "success", text: "审核修改已保存" });
      setEditing(null);
      setDraft(null);
      query.reload();
    } catch (error) {
      setNotice({
        kind: "error",
        text:
          error instanceof ApiError && error.status === 409
            ? "审核记录已被更新，请查看最新状态后重试。"
            : mutationMessage(error),
      });
      if (error instanceof ApiError && error.status === 409) query.reload();
    } finally {
      setPending(false);
    }
  }

  async function reopen() {
    if (!reopening || pending) return;
    if (!target || !reopenReason.trim()) {
      setNotice({ kind: "error", text: "请选择新审核人并填写原因。" });
      return;
    }
    setPending(true);
    setNotice(null);
    const body = {
      candidate_version: reopening.candidate.version,
      review_version: reopening.version,
      new_reviewer_id: target,
      reason: reopenReason.trim(),
    };
    try {
      const raw = await adminMutation<unknown>(
        `/admin/reviews/${reopening.id}/reopen`,
        { method: "POST", body, csrfToken: props.csrfToken },
        props.retryBootstrap,
      );
      parseCandidateReceipt(raw, {
        id: reopening.candidate_id,
        previousVersion: reopening.candidate.version,
        operation: "reopen",
        targetReviewerId: target,
      });
      setNotice({
        kind: "success",
        text: "已重新开放；旧审核保留为历史，候选已分配。",
      });
      setReopening(null);
      setTarget("");
      setReopenReason("");
      query.reload();
    } catch (error) {
      setNotice({
        kind: "error",
        text:
          error instanceof ApiError && error.status === 409
            ? "审核或候选状态已被更新，请重试。"
            : mutationMessage(error),
      });
      if (error instanceof ApiError && error.status === 409) query.reload();
    } finally {
      setPending(false);
    }
  }

  function applyFilters(event: React.FormEvent) {
    event.preventDefault();
    if (filters.from && filters.to && filters.from > filters.to) {
      setNotice({ kind: "error", text: "开始日期不能晚于结束日期。" });
      return;
    }
    setOffset(0);
    setApplied(filters);
  }

  return (
    <div className="admin-stack">
      {notice ? (
        <div
          role={notice.kind === "error" ? "alert" : "status"}
          className={`notice notice--${notice.kind}`}
        >
          {notice.text}
        </div>
      ) : null}
      <fieldset
        className="admin-fieldset"
        disabled={query.unavailable || props.directoriesUnavailable}
      >
        <form className="admin-filters" onSubmit={applyFilters}>
          <label>
            成员
            <select
              value={filters.reviewer}
              onChange={(event) => setFilters({ ...filters, reviewer: event.target.value })}
            >
              <option value="">全部</option>
              {props.users.map((item) => (
                <option key={item.id} value={item.id}>{item.display_name}</option>
              ))}
            </select>
          </label>
          <label>
            鱼种
            <select
              value={filters.species}
              onChange={(event) => setFilters({ ...filters, species: event.target.value })}
            >
              <option value="">全部</option>
              {props.species.map((item) => (
                <option key={item.id} value={item.code}>{item.code} · {item.name_zh}</option>
              ))}
            </select>
          </label>
          <label>
            来源
            <select
              value={filters.source}
              onChange={(event) => setFilters({ ...filters, source: event.target.value })}
            >
              <option value="">全部</option>
              {props.sources.map((item) => (
                <option key={item} value={item}>{sourceLabel(item)}</option>
              ))}
            </select>
          </label>
          <label>
            结果
            <select
              value={filters.decision}
              onChange={(event) => setFilters({ ...filters, decision: event.target.value })}
            >
              <option value="">全部</option>
              {Object.entries(decisionLabels).map(([code, label]) => (
                <option key={code} value={code}>{label}</option>
              ))}
            </select>
          </label>
          <label>
            当前状态
            <select
              value={filters.current}
              onChange={(event) => setFilters({ ...filters, current: event.target.value })}
            >
              <option value="">全部</option>
              <option value="true">当前</option>
              <option value="false">旧记录</option>
            </select>
          </label>
          <label>
            开始日期
            <input
              type="date"
              max="9998-12-31"
              value={filters.from}
              onChange={(event) => setFilters({ ...filters, from: event.target.value })}
            />
          </label>
          <label>
            审核结束日期
            <input
              aria-label="审核结束日期"
              type="date"
              max="9998-12-31"
              value={filters.to}
              onChange={(event) => setFilters({ ...filters, to: event.target.value })}
            />
          </label>
          <button className="secondary-button" type="submit">应用审核筛选</button>
          <button className="secondary-button" type="button" onClick={resetFilters}>重置审核筛选</button>
        </form>

        <QueryBoundary query={query}>
          {(data) =>
            data.items.length ? (
              <>
                <div className="admin-card-grid admin-review-card-grid">
                  {data.items.map((item) => (
                    <ReviewCard
                      key={item.id}
                      item={item}
                      onEdit={() => startEdit(item)}
                      onReopen={() => {
                        setReopening(item);
                        setTarget("");
                        setReopenReason("");
                        setNotice(null);
                      }}
                    />
                  ))}
                </div>
                <PageControls
                  offset={offset}
                  total={data.total}
                  limit={LIMIT}
                  onChange={setOffset}
                />
              </>
            ) : (
              <p>没有符合条件的审核记录。</p>
            )
          }
        </QueryBoundary>

        {editing && draft ? (
          <section
            className="admin-card admin-review-editor"
            role="region"
            aria-label={`编辑审核：${editing.species.name_zh}`}
          >
            <h3>编辑审核：{editing.species.name_zh}</h3>
            <p>{editing.species.code} · <em>{editing.species.scientific_name}</em></p>
            <div className="admin-review-decision-buttons">
              {(["APPROVED", "REJECTED", "UNSURE"] as DecisionCode[]).map((code) => (
                <button
                  key={code}
                  className={`admin-review-decision-button admin-review-decision-button--${code.toLowerCase()}`}
                  type="button"
                  aria-pressed={draft.decision === code}
                  onClick={() => setDraft({
                    ...draft,
                    decision: code,
                    rejection: code === "REJECTED" ? draft.rejection : null,
                  })}
                >
                  {draft.decision === code ? <span aria-hidden="true">✓ </span> : null}
                  {decisionLabels[code]}
                </button>
              ))}
            </div>
            {draft.decision === "REJECTED" ? (
              <PillChoiceGroup
                label="拒绝原因"
                options={Object.keys(rejectionLabels) as RejectionReasonCode[]}
                value={draft.rejection}
                getOptionLabel={(code) => rejectionLabels[code]}
                onChange={(rejection) => setDraft({ ...draft, rejection })}
              />
            ) : null}
            <label>
              审核备注
              <textarea
                value={draft.notes}
                onChange={(event) => setDraft({ ...draft, notes: event.target.value })}
              />
            </label>
            <label>
              管理员修改原因
              <textarea
                aria-label="管理员修改原因"
                value={draft.reason}
                onChange={(event) => setDraft({ ...draft, reason: event.target.value })}
              />
            </label>
            <div className="inline-actions equal-action-row">
              <button
                type="button"
                disabled={pending}
                className="primary-button compact-button"
                onClick={() => void saveEdit()}
              >
                保存审核
              </button>
              <button
                type="button"
                className="secondary-button"
                onClick={() => {
                  setEditing(null);
                  setDraft(null);
                }}
              >
                取消
              </button>
            </div>
          </section>
        ) : null}

        {reopening ? (
          <section className="admin-card admin-confirm admin-review-editor">
            <h3>确认重新开放：{reopening.species.name_zh}</h3>
            <p>旧审核保留为历史，不会自动形成第二次审核或平均结果。</p>
            <label>
              重新开放给
              <select
                aria-label="重新开放给"
                value={target}
                onChange={(event) => setTarget(event.target.value)}
              >
                <option value="">请选择</option>
                {props.users
                  .filter((item) =>
                    item.active && item.role === "reviewer" && item.id !== reopening.reviewer_id
                  )
                  .map((item) => (
                    <option key={item.id} value={item.id}>{item.display_name}</option>
                  ))}
              </select>
            </label>
            <label>
              重新开放原因
              <textarea
                aria-label="重新开放原因"
                value={reopenReason}
                onChange={(event) => setReopenReason(event.target.value)}
              />
            </label>
            <div className="inline-actions equal-action-row">
              <button
                className="danger-button"
                type="button"
                disabled={pending}
                onClick={() => void reopen()}
              >
                确认重新开放
              </button>
              <button
                className="secondary-button"
                type="button"
                onClick={() => setReopening(null)}
              >
                取消
              </button>
            </div>
          </section>
        ) : null}
      </fieldset>
    </div>
  );
}

function ReviewCard({
  item,
  onEdit,
  onReopen,
}: {
  item: AdminReviewItem;
  onEdit: () => void;
  onReopen: () => void;
}) {
  const source = sourceLabel(item.candidate.source_dataset);
  return (
    <article className="admin-review-card">
      <img
        className="admin-review-card__background"
        src={item.candidate.preview_url}
        alt=""
        aria-hidden="true"
        loading="lazy"
        referrerPolicy="no-referrer"
      />
      <span className="admin-review-card__overlay" aria-hidden="true" />
      <div className="admin-review-card__content">
        <header className="admin-review-card__header">
          <span className="admin-review-card__reviewer">{item.reviewer.display_name}</span>
          <span className={`admin-review-result admin-review-result--${item.decision.toLowerCase()}`}>
            {decisionLabels[item.decision]}
          </span>
        </header>
        <div className="admin-review-card__fish">
          <span>{item.species.code}</span>
          <h3>{item.species.name_zh}</h3>
          <em>{item.species.scientific_name}</em>
          <small>{item.is_current ? "当前审核" : "旧记录（只读）"}</small>
        </div>
        <footer className="admin-review-card__footer">
          <div className="admin-review-card__actions">
            {item.is_current ? (
              <>
                <button className="secondary-button" type="button" onClick={onEdit}>
                  编辑审核
                </button>
                <button className="danger-button" type="button" onClick={onReopen}>
                  重新开放
                </button>
              </>
            ) : null}
          </div>
          <a
            className="admin-review-source-link"
            href={item.candidate.source_url}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={`打开 ${source} 来源页`}
            title={source}
          >
            <span className="admin-review-source-link__label">{source}</span>
            <svg aria-hidden="true" viewBox="0 0 24 24" focusable="false">
              <path d="M14 4h6v6M20 4l-9 9M19 13v6a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h6" />
            </svg>
          </a>
        </footer>
      </div>
    </article>
  );
}
