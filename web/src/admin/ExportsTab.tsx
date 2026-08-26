import { useCallback, useState } from "react";

import { ApiError } from "../api/client";
import { API_BASE } from "../api/client";
import { PageControls, QueryBoundary, adminMutation, mutationMessage, useAdminQuery, type AdminTabProps } from "./common";
import { parseExportBatches, parseExportConflict, parseExportCreate, parsePendingCounts, parseReceiptFile, parseReceiptResponse, type ExportBatch } from "./types";

export function ExportsTab(props: AdminTabProps) {
  const parseCounts = useCallback((value: unknown) => parsePendingCounts(value, props.species), [props.species]);
  const counts = useAdminQuery(props.species.length ? "/admin/exports/pending-counts" : null, parseCounts, props.retryBootstrap);
  const [offset, setOffset] = useState(0); const batchQuery = new URLSearchParams({ limit: "20", offset: String(offset) });
  const batches = useAdminQuery(`/admin/exports?${batchQuery}`, parseExportBatches, props.retryBootstrap);
  const [scope, setScope] = useState(""); const [pending, setPending] = useState(false); const [notice, setNotice] = useState<{ kind: "error" | "success"; text: string } | null>(null);
  async function create() {
    if (pending || counts.unavailable || batches.unavailable || props.directoriesUnavailable) return; setPending(true); setNotice(null);
    const requestedScope = scope || null;
    try { const raw = await adminMutation<unknown>("/admin/exports", { method: "POST", body: requestedScope ? { species_code: requestedScope } : {}, csrfToken: props.csrfToken }, props.retryBootstrap); const result = parseExportCreate(raw, requestedScope); setNotice(result.kind === "no-work" ? { kind: "success", text: "没有待同步项目。" } : { kind: "success", text: result.batch.created ? "已创建新的同步批次。" : "已返回现有未完成批次。" }); counts.reload(); batches.reload(); }
    catch (error) { const conflict = error instanceof ApiError && error.status === 409 ? parseExportConflict(error.body) : null; const text = conflict?.code === "EXPORT_SCOPE_OVERLAP" ? `同步范围重叠，涉及 ${conflict.overlapCount} 个批次；列表已刷新。` : conflict?.code === "EXPORT_BATCH_EXPIRED" ? "同步批次已过期；列表已刷新。" : conflict?.code === "UNSAFE_SPECIES_CODE" ? "鱼种代码不适合本地路径；列表已刷新。" : mutationMessage(error); setNotice({ kind: "error", text }); if (error instanceof ApiError && error.status === 409) { counts.reload(); batches.reload(); } }
    finally { setPending(false); }
  }
  async function receiptFile(batch: ExportBatch, file: File | null) {
    if (!file || pending) return; setNotice(null); if (!file.name.toLowerCase().endsWith(".json")) { setNotice({ kind: "error", text: "只接受 .json 回执文件。" }); return; } if (file.size > 128 * 1024) { setNotice({ kind: "error", text: "回执文件超过 128 KiB。" }); return; }
    setPending(true);
    try {
      const parsedJson: unknown = JSON.parse(await file.text()); const upload = parseReceiptFile(parsedJson, batch.id); const submitted = new Map(upload.items.map((item) => [item.candidate_id, item.status]));
      const raw = await adminMutation<unknown>(`/admin/exports/${batch.id}/receipt-file`, { method: "POST", body: upload, csrfToken: props.csrfToken }, props.retryBootstrap); const result = parseReceiptResponse(raw, batch.id, submitted); setNotice({ kind: "success", text: `回执已处理：接受 ${result.accepted}，待处理 ${result.pending}。` }); batches.reload(); counts.reload();
    } catch (error) { setNotice({ kind: "error", text: error instanceof Error && error.message.includes("批次不匹配") ? "回执批次不匹配或项目格式无效。" : mutationMessage(error) }); }
    finally { setPending(false); }
  }
  const creationUnavailable = counts.unavailable || batches.unavailable || props.directoriesUnavailable;
  return <div className="admin-stack">{notice ? <div className={`notice notice--${notice.kind}`} role={notice.kind === "error" ? "alert" : "status"}>{notice.text}</div> : null}<fieldset className="admin-fieldset" disabled={creationUnavailable}><section className="admin-card"><h3>创建增量同步批次</h3><p>CSV 交给 Mao 的 Windows 本地下载工具；网页不会下载或转发原图。已经成功的原图会从未来批次排除，失败或待处理项目仍可进入后续批次。</p><label>范围<select value={scope} onChange={(event) => setScope(event.target.value)}><option value="">全部鱼种</option>{props.species.map((item) => <option key={item.id} value={item.code}>{item.code} · {item.name_zh}</option>)}</select></label><button className="primary-button compact-button" type="button" disabled={pending} onClick={() => void create()}>创建同步批次</button></section></fieldset>
    <section className="admin-card"><h3>待同步数量</h3><QueryBoundary query={counts}>{(data) => <ul className="pending-counts">{props.species.map((item) => <li key={item.code}>{item.code}：{data[item.code]}</li>)}</ul>}</QueryBoundary></section>
    <section className="admin-card"><h3>不可变批次历史</h3><QueryBoundary query={batches}>{(data, unavailable) => data.items.length ? <><div className="admin-card-grid">{data.items.map((batch) => <article className="admin-card" key={batch.id}><h4 className="mono">{batch.id}</h4><p>{batch.species_code ?? "全部鱼种"} · {batch.status === "pending" ? "待处理" : batch.status === "completed" ? "已完成" : "已过期"}</p><p>项目 {batch.item_count} · 待处理 {batch.pending_count}</p><p>创建：{new Date(batch.created_at).toLocaleString("zh-CN")}<br />过期：{new Date(batch.expires_at).toLocaleString("zh-CN")}</p><a href={`${API_BASE}/admin/exports/${batch.id}.csv`} download={`sukaseafood-export-${batch.id}.csv`}>下载 CSV</a><label className="receipt-upload">上传 {batch.id} 回执<input aria-label={`上传 ${batch.id} 回执`} type="file" accept=".json,application/json" disabled={pending || unavailable || props.directoriesUnavailable} onChange={(event) => { const file = event.target.files?.[0] ?? null; event.currentTarget.value = ""; void receiptFile(batch, file); }} /></label></article>)}</div><PageControls offset={offset} total={data.total} limit={20} onChange={setOffset} disabled={unavailable} /></> : <p>暂无同步批次。</p>}</QueryBoundary></section>
  </div>;
}
