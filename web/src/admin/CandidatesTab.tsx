import { useMemo, useState } from "react";

import { ApiError } from "../api/client";
import { PageControls, QueryBoundary, adminMutation, mutationMessage, safeHttps, sourceLabel, useAdminQuery, type AdminTabProps } from "./common";
import { parseCandidateList, parseCandidateReceipt, type AdminCandidate } from "./types";

const LIMIT = 20;
type Draft = { preview_url: string; original_url: string; species_id: string; active: boolean; reason: string; target: string; confirm: boolean };

export function CandidatesTab(props: AdminTabProps) {
  const [filters, setFilters] = useState({ species: "", source: "", active: "", reviewed: "", decision: "", reviewer: "", search: "" });
  const [applied, setApplied] = useState(filters);
  const [offset, setOffset] = useState(0);
  const [editing, setEditing] = useState<AdminCandidate | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState<{ kind: "error" | "success"; text: string } | null>(null);
  const path = useMemo(() => {
    const query = new URLSearchParams({ limit: String(LIMIT), offset: String(offset) });
    if (applied.species) query.set("species_code", applied.species);
    if (applied.source) query.set("source_dataset", applied.source);
    if (applied.active) query.set("active", applied.active);
    if (applied.reviewed) query.set("reviewed", applied.reviewed);
    if (applied.decision) query.set("decision", applied.decision);
    if (applied.reviewer) query.set("current_reviewer_id", applied.reviewer);
    if (applied.search.trim()) query.set("search", applied.search.trim());
    return `/admin/candidates?${query}`;
  }, [applied, offset]);
  const query = useAdminQuery(path, parseCandidateList, props.retryBootstrap);
  const sources = props.sources;

  function edit(item: AdminCandidate) {
    setEditing(item); setDraft({ preview_url: item.preview_url, original_url: item.original_url, species_id: item.species.id, active: item.active, reason: "", target: "", confirm: false }); setNotice(null);
  }
  async function save() {
    if (!editing || !draft || pending) return;
    if (!draft.reason.trim()) { setNotice({ kind: "error", text: "必须填写候选修改原因。" }); return; }
    if (!safeHttps(draft.preview_url) || !safeHttps(draft.original_url)) { setNotice({ kind: "error", text: "图片地址必须是安全的 HTTPS 地址。" }); return; }
    const speciesChanged = draft.species_id !== editing.species.id;
    if (speciesChanged && editing.current_review && !draft.confirm) { setDraft({ ...draft, confirm: true }); setNotice({ kind: "error", text: "修改已审核候选的鱼种需要二次确认并指定新审核人。" }); return; }
    if (speciesChanged && editing.current_review && !draft.target) { setNotice({ kind: "error", text: "请选择新审核人。" }); return; }
    const changed: Record<string, unknown> = {};
    if (draft.preview_url !== editing.preview_url) changed.preview_url = draft.preview_url;
    if (draft.original_url !== editing.original_url) changed.original_url = draft.original_url;
    if (draft.species_id !== editing.species.id) changed.species_id = draft.species_id;
    if (draft.active !== editing.active) changed.active = draft.active;
    if (!Object.keys(changed).length) { setNotice({ kind: "error", text: "没有可保存的修改。" }); return; }
    if (speciesChanged && editing.current_review) { changed.confirm_review_invalidation = true; changed.new_reviewer_id = draft.target; }
    const body = { version: editing.version, ...changed, reason: draft.reason.trim() };
    setPending(true); setNotice(null);
    try {
      const raw = await adminMutation<unknown>(`/admin/candidates/${editing.id}`, { method: "PATCH", body, csrfToken: props.csrfToken }, props.retryBootstrap);
      const submitted: Partial<Pick<AdminCandidate, "preview_url" | "original_url" | "active">> & { species_id?: string } = {};
      if (typeof changed.preview_url === "string") submitted.preview_url = changed.preview_url;
      if (typeof changed.original_url === "string") submitted.original_url = changed.original_url;
      if (typeof changed.active === "boolean") submitted.active = changed.active;
      if (typeof changed.species_id === "string") submitted.species_id = changed.species_id;
      parseCandidateReceipt(raw, speciesChanged && editing.current_review
        ? { id: editing.id, previousVersion: editing.version, operation: "invalidation", targetReviewerId: draft.target, speciesId: draft.species_id, submitted }
        : { id: editing.id, previousVersion: editing.version, operation: "patch", submitted, previous: editing });
      setNotice({ kind: "success", text: "候选修改已保存。" }); setEditing(null); setDraft(null); query.reload();
    } catch (error) {
      setNotice({ kind: "error", text: error instanceof ApiError && error.status === 409 ? "候选状态已被更新，请根据最新状态重试。" : mutationMessage(error) });
      if (error instanceof ApiError && error.status === 409) query.reload();
    } finally { setPending(false); }
  }

  return <div className="admin-stack">
    {notice ? <div role={notice.kind === "error" ? "alert" : "status"} className={`notice notice--${notice.kind}`}>{notice.text}</div> : null}
    <fieldset className="admin-fieldset" disabled={query.unavailable || props.directoriesUnavailable}><form className="admin-filters" onSubmit={(event) => { event.preventDefault(); setOffset(0); setApplied(filters); }}>
      <label>鱼种<select value={filters.species} onChange={(event) => setFilters({ ...filters, species: event.target.value })}><option value="">全部</option>{props.species.map((item) => <option key={item.id} value={item.code}>{item.code} · {item.name_zh}</option>)}</select></label>
      <label>来源<select value={filters.source} onChange={(event) => setFilters({ ...filters, source: event.target.value })}><option value="">全部</option>{sources.map((source) => <option key={source} value={source}>{sourceLabel(source)}</option>)}</select></label>
      <label>启用状态<select value={filters.active} onChange={(event) => setFilters({ ...filters, active: event.target.value })}><option value="">全部</option><option value="true">启用</option><option value="false">停用</option></select></label>
      <label>审核状态<select aria-label="审核状态" value={filters.reviewed} onChange={(event) => setFilters({ ...filters, reviewed: event.target.value })}><option value="">全部</option><option value="true">已审核</option><option value="false">未审核</option></select></label>
      <label>结果<select value={filters.decision} onChange={(event) => setFilters({ ...filters, decision: event.target.value })}><option value="">全部</option><option value="APPROVED">保留</option><option value="REJECTED">拒绝</option><option value="UNSURE">不确定</option></select></label>
      <label>当前审核人<select value={filters.reviewer} onChange={(event) => setFilters({ ...filters, reviewer: event.target.value })}><option value="">全部</option>{props.users.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></label>
      <label>候选搜索<input type="search" aria-label="候选搜索" value={filters.search} onChange={(event) => setFilters({ ...filters, search: event.target.value })} /></label>
      <button type="submit" className="secondary-button">应用候选筛选</button>
    </form></fieldset>
    <QueryBoundary query={query}>{(data, unavailable) => data.items.length ? <><div className="admin-card-grid">{data.items.map((item) => <article className="admin-card" key={item.id}><h3 className="mono">候选编号 {item.id}</h3><p>{item.species.code} · {item.species.name_zh}</p><p>{sourceLabel(item.source_dataset)} · {item.source_record_id}</p><p>版本 {item.version} · {item.active ? "启用" : "停用"} · {item.current_review ? "已审核" : "未审核"}</p><div className="inline-actions"><a href={item.source_url} target="_blank" rel="noreferrer">来源页</a><a href={item.original_url} target="_blank" rel="noreferrer">原图</a><button disabled={unavailable || props.directoriesUnavailable} type="button" className="secondary-button" onClick={() => edit(item)}>编辑候选</button></div></article>)}</div><PageControls offset={offset} total={data.total} limit={LIMIT} onChange={setOffset} disabled={unavailable || props.directoriesUnavailable} /></> : <p>没有符合条件的候选图片。</p>}</QueryBoundary>
    {editing && draft ? <fieldset className="admin-fieldset" disabled={query.unavailable || props.directoriesUnavailable}><section className="admin-card"><h3>编辑候选 {editing.id}</h3>
      <label>预览图地址<input aria-label="预览图地址" value={draft.preview_url} onChange={(event) => setDraft({ ...draft, preview_url: event.target.value })} /></label>
      <label>原图地址<input aria-label="原图地址" value={draft.original_url} onChange={(event) => setDraft({ ...draft, original_url: event.target.value })} /></label>
      <label>所属鱼种<select aria-label="所属鱼种" value={draft.species_id} onChange={(event) => setDraft({ ...draft, species_id: event.target.value, confirm: false, target: "" })}>{props.species.filter((item) => item.active).map((item) => <option key={item.id} value={item.id}>{item.code} · {item.name_zh}</option>)}</select></label>
      <label className="check-label"><input type="checkbox" checked={draft.active} onChange={(event) => setDraft({ ...draft, active: event.target.checked })} />启用</label>
      {draft.confirm ? <div className="notice notice--error"><p>旧审核记录保留为历史，当前结果会失效，候选将重新分配。</p><label>新审核人<select aria-label="新审核人" value={draft.target} onChange={(event) => setDraft({ ...draft, target: event.target.value })}><option value="">请选择</option>{props.users.filter((item) => item.active && item.role === "reviewer" && item.id !== editing.current_review?.reviewer.id).map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></label></div> : null}
      <label>候选修改原因<textarea aria-label="候选修改原因" value={draft.reason} onChange={(event) => setDraft({ ...draft, reason: event.target.value })} /></label>
      <div className="inline-actions"><button disabled={pending} type="button" className="primary-button compact-button" onClick={() => void save()}>{pending ? "保存中…" : draft.confirm ? "确认失效并保存" : "保存候选"}</button><button type="button" className="secondary-button" disabled={pending} onClick={() => { setEditing(null); setDraft(null); }}>取消</button></div>
    </section></fieldset> : null}
  </div>;
}
