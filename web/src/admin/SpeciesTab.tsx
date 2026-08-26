import { useMemo, useState } from "react";

import { ApiError } from "../api/client";
import { PageControls, QueryBoundary, adminMutation, mutationMessage, useAdminQuery, type AdminTabProps } from "./common";
import { isSafeSpeciesCode, parseSpeciesList, parseSpeciesReceipt, type AdminSpecies } from "./types";

const LIMIT = 20;
interface Draft { code: string; name_zh: string; name_en: string; scientific_name: string; sort_order: string; active: boolean; reason: string }
const EMPTY: Draft = { code: "", name_zh: "", name_en: "", scientific_name: "", sort_order: "0", active: true, reason: "" };

export function SpeciesTab(props: AdminTabProps) {
  const [search, setSearch] = useState(""); const [active, setActive] = useState(""); const [applied, setApplied] = useState({ search: "", active: "" }); const [offset, setOffset] = useState(0);
  const [mode, setMode] = useState<"create" | "edit" | null>(null); const [selected, setSelected] = useState<Required<AdminSpecies> | null>(null); const [draft, setDraft] = useState<Draft>(EMPTY);
  const [stopConfirm, setStopConfirm] = useState(false); const [pending, setPending] = useState(false); const [notice, setNotice] = useState<{ kind: "error" | "success"; text: string } | null>(null);
  const path = useMemo(() => { const query = new URLSearchParams({ limit: String(LIMIT), offset: String(offset) }); if (applied.search) query.set("search", applied.search); if (applied.active) query.set("active", applied.active); return `/admin/species?${query}`; }, [applied, offset]);
  const query = useAdminQuery(path, parseSpeciesList, props.retryBootstrap);

  function startCreate() { setMode("create"); setSelected(null); setDraft(EMPTY); setStopConfirm(false); setNotice(null); }
  function startEdit(item: Required<AdminSpecies>) { setMode("edit"); setSelected(item); setDraft({ code: item.code, name_zh: item.name_zh, name_en: item.name_en, scientific_name: item.scientific_name, sort_order: String(item.sort_order), active: item.active, reason: "" }); setStopConfirm(false); setNotice(null); }
  function close() { setMode(null); setSelected(null); setDraft(EMPTY); setStopConfirm(false); }
  async function save() {
    if (pending) return;
    if (!draft.reason.trim() || !draft.name_zh.trim() || !draft.name_en.trim() || !draft.scientific_name.trim()) { setNotice({ kind: "error", text: "名称和修改原因不能为空。" }); return; }
    if (mode === "create" && !isSafeSpeciesCode(draft.code)) { setNotice({ kind: "error", text: /^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$/.test(draft.code) ? "鱼种代码不能使用 Windows 保留名。" : "鱼种代码格式无效。" }); return; }
    if (!Number.isSafeInteger(Number(draft.sort_order))) { setNotice({ kind: "error", text: "排序必须是整数。" }); return; }
    if (mode === "edit" && selected?.active && !draft.active && !stopConfirm) { setStopConfirm(true); setNotice({ kind: "error", text: "停用鱼种需要明确确认；已有候选仍保留在历史中。" }); return; }
    setPending(true); setNotice(null);
    try {
      let body: Record<string, unknown>; let pathValue: string; let method: "POST" | "PATCH";
      if (mode === "create") {
        body = { code: draft.code, name_zh: draft.name_zh.trim(), name_en: draft.name_en.trim(), scientific_name: draft.scientific_name.trim(), active: draft.active, sort_order: Number(draft.sort_order), reason: draft.reason.trim() };
        pathValue = "/admin/species"; method = "POST";
      } else if (selected) {
        body = { reason: draft.reason.trim() };
        if (draft.name_zh.trim() !== selected.name_zh) body.name_zh = draft.name_zh.trim();
        if (draft.name_en.trim() !== selected.name_en) body.name_en = draft.name_en.trim();
        if (draft.scientific_name.trim() !== selected.scientific_name) body.scientific_name = draft.scientific_name.trim();
        if (Number(draft.sort_order) !== selected.sort_order) body.sort_order = Number(draft.sort_order);
        if (draft.active !== selected.active) body.active = draft.active;
        if (Object.keys(body).length === 1) { setNotice({ kind: "error", text: "没有可保存的修改。" }); return; }
        pathValue = `/admin/species/${selected.id}`; method = "PATCH";
      } else return;
      const raw = await adminMutation<unknown>(pathValue, { method, body, csrfToken: props.csrfToken }, props.retryBootstrap);
      const submitted = mode === "create"
        ? { name_zh: draft.name_zh.trim(), name_en: draft.name_en.trim(), scientific_name: draft.scientific_name.trim(), sort_order: Number(draft.sort_order), active: draft.active }
        : Object.fromEntries(Object.entries(body).filter(([key]) => key !== "reason"));
      parseSpeciesReceipt(raw, mode === "create" ? { code: draft.code, submitted, create: true } : { id: selected?.id, code: selected?.code, submitted });
      setNotice({ kind: "success", text: mode === "create" ? "鱼种已创建。" : "鱼种修改已保存。" }); close(); query.reload(); props.refreshDirectories();
    } catch (error) { setNotice({ kind: "error", text: error instanceof ApiError && error.status === 409 ? "鱼种状态已变化，请刷新后重试。" : mutationMessage(error) }); if (error instanceof ApiError && error.status === 409) query.reload(); }
    finally { setPending(false); }
  }

  return <div className="admin-stack">{notice ? <div role={notice.kind === "error" ? "alert" : "status"} className={`notice notice--${notice.kind}`}>{notice.text}</div> : null}
    <fieldset className="admin-fieldset" disabled={query.unavailable || props.directoriesUnavailable}><form className="admin-filters" onSubmit={(event) => { event.preventDefault(); setOffset(0); setApplied({ search: search.trim(), active }); }}><label>搜索<input type="search" value={search} onChange={(event) => setSearch(event.target.value)} /></label><label>状态<select value={active} onChange={(event) => setActive(event.target.value)}><option value="">全部</option><option value="true">启用</option><option value="false">停用</option></select></label><button type="submit" className="secondary-button">应用鱼种筛选</button><button type="button" className="primary-button compact-button" onClick={startCreate}>新增鱼种</button></form></fieldset>
    <QueryBoundary query={query}>{(data, unavailable) => data.items.length ? <><div className="admin-table-wrap"><table><thead><tr><th>代码</th><th>中文名</th><th>英文名</th><th>学名</th><th>排序</th><th>候选数</th><th>状态</th><th>操作</th></tr></thead><tbody>{data.items.map((item) => <tr key={item.id}><th>{item.code}</th><td>{item.name_zh}</td><td>{item.name_en}</td><td><em>{item.scientific_name}</em></td><td>{item.sort_order}</td><td>{item.candidate_count}</td><td>{item.active ? "启用" : "停用"}</td><td><button disabled={unavailable || props.directoriesUnavailable} type="button" className="secondary-button" aria-label={`编辑 ${item.code}`} onClick={() => startEdit(item)}>编辑</button></td></tr>)}</tbody></table></div><PageControls offset={offset} total={data.total} limit={LIMIT} onChange={setOffset} disabled={unavailable || props.directoriesUnavailable} /></> : <p>没有鱼种。</p>}</QueryBoundary>
    {mode ? <fieldset className="admin-fieldset" disabled={query.unavailable || props.directoriesUnavailable}><section className="admin-card"><h3>{mode === "create" ? "新增鱼种" : `编辑 ${selected?.code}`}</h3>
      {mode === "create" ? <label>鱼种代码<input aria-label="鱼种代码" value={draft.code} onChange={(event) => setDraft({ ...draft, code: event.target.value.toUpperCase() })} /></label> : <p>代码（不可修改）：<strong>{selected?.code}</strong></p>}
      <div className="admin-form-grid"><label>中文名<input aria-label="中文名" value={draft.name_zh} onChange={(event) => setDraft({ ...draft, name_zh: event.target.value })} /></label><label>英文名<input aria-label="英文名" value={draft.name_en} onChange={(event) => setDraft({ ...draft, name_en: event.target.value })} /></label><label>学名<input aria-label="学名" value={draft.scientific_name} onChange={(event) => setDraft({ ...draft, scientific_name: event.target.value })} /></label><label>排序<input type="number" value={draft.sort_order} onChange={(event) => setDraft({ ...draft, sort_order: event.target.value })} /></label></div>
      <label className="check-label"><input type="checkbox" checked={draft.active} onChange={(event) => { setDraft({ ...draft, active: event.target.checked }); setStopConfirm(false); }} />启用</label>
      <label>鱼种修改原因<textarea aria-label="鱼种修改原因" value={draft.reason} onChange={(event) => setDraft({ ...draft, reason: event.target.value })} /></label>
      <div className="inline-actions">{mode === "edit" && selected?.active ? <button type="button" className="danger-button" onClick={() => setDraft({ ...draft, active: false })}>停用鱼种</button> : null}<button type="button" className="primary-button compact-button" disabled={pending} onClick={() => void save()}>{pending ? "保存中…" : mode === "create" ? "创建鱼种" : stopConfirm ? "确认停用并保存" : "保存鱼种"}</button><button type="button" className="secondary-button" onClick={close}>{mode === "create" ? "取消新增" : "取消"}</button></div>
    </section></fieldset> : null}
  </div>;
}
