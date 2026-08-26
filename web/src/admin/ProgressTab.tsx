import { useMemo, useState } from "react";

import { ApiError } from "../api/client";
import type { ProgressResponse } from "../api/types";
import { QueryBoundary, adminMutation, mutationMessage, sourceLabel, useAdminQuery, type AdminTabProps } from "./common";
import { parseCandidateReceipt, parseCurrentList, parseProgress, type CurrentItem } from "./types";

type Action = { kind: "release" | "transfer"; row: CurrentItem } | null;

function ChineseProgress({ data }: { data: ProgressResponse }) {
  return <section className="team-progress" aria-labelledby="admin-progress-title">
    <div className="team-progress__heading"><div><p className="eyebrow">总完成率</p><h3 id="admin-progress-title">团队审核进度</h3></div><strong className="progress-percent">{data.completion_percent}%</strong></div>
    <div className="progress-stat-grid">{[["候选总数", data.total], ["已审核", data.reviewed], ["待审核", data.pending], ["当前打开", data.currently_open]].map(([label, value]) => <div className="progress-stat" key={label}><span>{label}</span><strong className="progress-stat__value">{value}</strong></div>)}</div>
    <div className="decision-counts" aria-label="全部审核结果"><span>保留：{data.decision_counts.APPROVED}</span><span>拒绝：{data.decision_counts.REJECTED}</span><span>不确定：{data.decision_counts.UNSURE}</span><span>今日：{data.today_count}</span></div>
    <div className="progress-table-wrap"><table className="progress-table"><thead><tr><th>成员</th><th>已完成</th><th>保留</th><th>拒绝</th><th>不确定</th><th>今日</th></tr></thead><tbody>{data.members.map((member) => <tr key={member.name}><th>{member.name}</th><td>{member.completed}</td><td>{member.approved}</td><td>{member.rejected}</td><td>{member.unsure}</td><td>{member.today}</td></tr>)}</tbody></table></div>
    <p className="progress-explanation">进度按当前有效审核计算；重新开放的旧记录仅保留为历史。</p>
  </section>;
}

export function ProgressTab(props: AdminTabProps) {
  const [filters, setFilters] = useState({ species: "", source: "", reviewer: "", search: "" });
  const [applied, setApplied] = useState(filters);
  const [action, setAction] = useState<Action>(null);
  const [reason, setReason] = useState("");
  const [target, setTarget] = useState("");
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState<{ kind: "error" | "success"; text: string } | null>(null);
  const progress = useAdminQuery("/progress", parseProgress, props.retryBootstrap);
  const currentPath = useMemo(() => {
    const query = new URLSearchParams({ limit: "50", offset: "0" });
    if (applied.species) query.set("species_code", applied.species);
    if (applied.source) query.set("source_dataset", applied.source);
    if (applied.reviewer) query.set("reviewer_id", applied.reviewer);
    if (applied.search.trim()) query.set("search", applied.search.trim());
    return `/admin/current?${query}`;
  }, [applied]);
  const current = useAdminQuery(currentPath, parseCurrentList, props.retryBootstrap);

  function start(kind: "release" | "transfer", row: CurrentItem) {
    setAction({ kind, row }); setReason(""); setTarget(""); setNotice(null);
  }

  async function submit() {
    if (!action || pending) return;
    if (!reason.trim()) { setNotice({ kind: "error", text: "必须填写原因。" }); return; }
    if (action.kind === "transfer" && !target) { setNotice({ kind: "error", text: "请选择新的审核人。" }); return; }
    setPending(true); setNotice(null);
    const candidate = action.row.candidate;
    const path = `/admin/current/${candidate.id}/${action.kind}`;
    const body = action.kind === "release"
      ? { version: candidate.version, reason: reason.trim() }
      : { version: candidate.version, new_reviewer_id: target, reason: reason.trim() };
    try {
      const raw = await adminMutation<unknown>(path, { method: "POST", body, csrfToken: props.csrfToken }, props.retryBootstrap);
      parseCandidateReceipt(raw, { id: candidate.id, previousVersion: candidate.version, operation: action.kind, targetReviewerId: action.kind === "transfer" ? target : undefined });
      setNotice({ kind: "success", text: action.kind === "release" ? "释放成功。" : "转交成功。" });
      setAction(null); setReason(""); setTarget(""); current.reload(); progress.reload();
    } catch (error) {
      setNotice({ kind: "error", text: error instanceof ApiError && error.status === 409 ? "记录已被更新，请查看最新状态后重试。" : mutationMessage(error) });
      if (error instanceof ApiError && error.status === 409) { current.reload(); progress.reload(); }
    } finally { setPending(false); }
  }

  const sources = props.sources;
  return <div className="admin-stack">
    {notice ? <div role={notice.kind === "error" ? "alert" : "status"} className={`notice notice--${notice.kind}`}>{notice.text}</div> : null}
    <QueryBoundary query={progress}>{(data) => <ChineseProgress data={data} />}</QueryBoundary>
    <section className="admin-card"><h3>当前打开的图片</h3>
      <fieldset className="admin-fieldset" disabled={current.unavailable || props.directoriesUnavailable}><form className="admin-filters" onSubmit={(event) => { event.preventDefault(); setApplied(filters); }}>
        <label>鱼种<select value={filters.species} onChange={(event) => setFilters({ ...filters, species: event.target.value })}><option value="">全部鱼种</option>{props.species.map((item) => <option key={item.id} value={item.code}>{item.code} · {item.name_zh}</option>)}</select></label>
        <label>来源<select value={filters.source} onChange={(event) => setFilters({ ...filters, source: event.target.value })}><option value="">全部来源</option>{sources.map((source) => <option key={source} value={source}>{sourceLabel(source)}</option>)}</select></label>
        <label>成员<select value={filters.reviewer} onChange={(event) => setFilters({ ...filters, reviewer: event.target.value })}><option value="">全部成员</option>{props.users.filter((item) => item.active).map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></label>
        <label>搜索<input value={filters.search} onChange={(event) => setFilters({ ...filters, search: event.target.value })} /></label>
        <button type="submit" className="secondary-button">应用筛选</button>
      </form></fieldset>
      <QueryBoundary query={current}>{(data, unavailable) => data.items.length ? <div className="admin-table-wrap"><table><thead><tr><th>成员</th><th>候选 ID</th><th>来源记录</th><th>鱼种</th><th>打开时间</th><th>操作</th></tr></thead><tbody>{data.items.map((row) => <tr key={row.candidate.id}><td>{row.reviewer.display_name}</td><td className="mono">{row.candidate.id}</td><td>{sourceLabel(row.candidate.source_dataset)} · {row.candidate.source_record_id}</td><td>{row.species.code} · {row.species.name_zh}</td><td>{new Date(row.current_started_at).toLocaleString("zh-CN")}</td><td><div className="inline-actions"><button disabled={unavailable || props.directoriesUnavailable} className="danger-button" type="button" onClick={() => start("release", row)}>释放</button><button disabled={unavailable || props.directoriesUnavailable} className="secondary-button" type="button" onClick={() => start("transfer", row)}>转交</button></div></td></tr>)}</tbody></table></div> : <p>当前没有成员打开图片。</p>}</QueryBoundary>
    </section>
    {action ? <fieldset className="admin-fieldset" disabled={current.unavailable || props.directoriesUnavailable}><section className="admin-card admin-confirm" aria-label={action.kind === "release" ? "确认释放当前图片" : "确认转交当前图片"}>
      <h3>{action.kind === "release" ? "确认释放" : "确认转交"} {action.row.candidate.id}</h3>
      <p>此操作只处理尚未提交的当前图片，不会自动过期。</p>
      {action.kind === "transfer" ? <label>新审核人<select value={target} onChange={(event) => setTarget(event.target.value)}><option value="">请选择</option>{props.users.filter((user) => user.active && user.role === "reviewer" && user.id !== action.row.reviewer.id).map((user) => <option key={user.id} value={user.id}>{user.display_name}</option>)}</select></label> : null}
      <label>{action.kind === "release" ? "释放原因" : "转交原因"}<textarea value={reason} onChange={(event) => setReason(event.target.value)} /></label>
      <div className="inline-actions"><button disabled={pending} className="danger-button" type="button" onClick={() => void submit()}>{pending ? "处理中…" : action.kind === "release" ? "确认释放" : "确认转交"}</button><button className="secondary-button" type="button" disabled={pending} onClick={() => setAction(null)}>取消</button></div>
    </section></fieldset> : null}
  </div>;
}
