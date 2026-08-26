import { useState } from "react";

import { ApiError, request } from "../api/client";
import { adminMutation, mutationMessage, type AdminTabProps } from "./common";
import { parseImportPreview, parseImportResult, type ImportPreview } from "./types";

const ISSUE_LABELS: Record<string, string> = {
  EXACT_DUPLICATE: "完全重复记录", POSSIBLE_URL_DUPLICATE: "可能重复的图片地址", INVALID_SPECIES: "无效鱼种",
  MISSING_URL: "缺少图片地址", INVALID_LICENSE: "无效许可证", UNSUPPORTED_SOURCE: "不支持的来源",
  CSV_TOO_LARGE: "CSV 文件过大", CSV_TOO_MANY_ROWS: "CSV 行数过多", CSV_INVALID_ENCODING: "CSV 编码无效",
  CSV_INVALID_HEADER: "CSV 列名无效", CSV_MISSING_HEADERS: "CSV 缺少必要列", CSV_DUPLICATE_HEADERS: "CSV 存在重复列", CSV_MALFORMED: "CSV 格式错误",
  CONFLICTING_SOURCE_IDENTITY: "来源身份冲突", FIELD_TOO_LONG: "字段内容过长", INVALID_CONTROL_CHARACTER: "字段包含控制字符",
  INVALID_LICENSE_URL: "许可证地址无效", METADATA_TOO_LARGE: "元数据过大", MISSING_SOURCE_IDENTITY: "缺少来源身份",
  UNPARSED_SOURCE_DATE: "来源日期无法解析", UNSAFE_URL: "地址不安全",
};

export function ImportsTab(props: AdminTabProps) {
  const [file, setFile] = useState<File | null>(null); const [preview, setPreview] = useState<ImportPreview | null>(null); const [pending, setPending] = useState(false); const [confirming, setConfirming] = useState(false); const [notice, setNotice] = useState<{ kind: "error" | "success"; text: string } | null>(null);
  function choose(next: File | null) { setFile(next); setPreview(null); setConfirming(false); setNotice(null); }
  async function runPreview() {
    if (!file || pending) return; if (!file.name.toLowerCase().endsWith(".csv")) { setNotice({ kind: "error", text: "只接受 .csv 文件。" }); return; }
    const form = new FormData(); form.append("file", file); setPending(true); setNotice(null);
    try {
      const raw = await request<unknown>("/admin/imports/preview", { method: "POST", body: form, csrfToken: props.csrfToken });
      const parsed = parseImportPreview(raw); setPreview(parsed); setNotice(parsed.can_commit ? null : { kind: "error", text: "预检查发现阻断问题，不能提交。" });
    } catch (error) {
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) void props.retryBootstrap();
      const code = error instanceof ApiError && typeof error.body === "object" && error.body && "detail" in error.body && typeof error.body.detail === "object" && error.body.detail && "code" in error.body.detail ? String(error.body.detail.code) : "";
      setNotice({ kind: "error", text: ISSUE_LABELS[code] ?? "预检查失败，请检查 CSV 后重试。" });
    } finally { setPending(false); }
  }
  async function commit() {
    if (!preview?.can_commit || !preview.preview_token || pending) return; setPending(true); setNotice(null);
    try { const raw = await adminMutation<unknown>("/admin/imports/commit", { method: "POST", body: { preview_token: preview.preview_token }, csrfToken: props.csrfToken }, props.retryBootstrap); const result = parseImportResult(raw, preview); setPreview(null); setFile(null); setConfirming(false); setNotice({ kind: "success", text: `导入完成：新增 ${result.inserted}，跳过完全重复 ${result.skipped_exact}，可能重复地址 ${result.possible_url_duplicates}。` }); }
    catch (error) { setNotice({ kind: "error", text: error instanceof ApiError && error.status === 409 ? "预检查已过期、已使用或与当前会话/数据库不一致，请重新预检查。" : mutationMessage(error) }); }
    finally { setPending(false); }
  }
  const summaries = preview ? [
    ["总行数", preview.total], ["新增", preview.new_rows], ["完全重复", preview.exact_duplicates], ["可能重复地址", preview.possible_url_duplicates], ["无效鱼种", preview.invalid_species], ["缺少地址", preview.missing_urls], ["无效许可证", preview.invalid_licenses], ["无效来源", preview.invalid_sources], ["身份冲突", preview.conflicting_identities], ["解析错误", preview.parse_errors], ["警告", preview.warnings], ["阻断问题", preview.blocking_errors],
  ] : [];
  return <div className="admin-stack">{notice ? <div className={`notice notice--${notice.kind}`} role={notice.kind === "error" ? "alert" : "status"}>{notice.text}</div> : null}<section className="admin-card"><h3>候选 CSV 导入</h3><p>只读取 CSV 文本进行预检查；不会在浏览器中请求任何图片地址。</p><label>候选 CSV 文件<input aria-label="候选 CSV 文件" type="file" accept=".csv,text/csv" onChange={(event) => choose(event.target.files?.[0] ?? null)} /></label><button type="button" className="primary-button compact-button" disabled={!file || pending} onClick={() => void runPreview()}>{pending ? "处理中…" : "预检查"}</button></section>
    {!preview && file ? <button type="button" className="primary-button compact-button" disabled>提交导入</button> : null}
    {preview ? <section className="admin-card"><h3>预检查摘要</h3><div className="admin-stat-grid">{summaries.map(([label, count]) => <div key={label}><span>{label}</span><strong>{count}</strong><span className="admin-summary-inline">{label === "新增" || label === "可能重复地址" ? `${label}：${count}` : ""}</span></div>)}</div><div className="admin-split"><div><h4>来源数量</h4><ul>{Object.entries(preview.source_counts).map(([code, count]) => <li key={code}>{code}：{count}</li>)}</ul></div><div><h4>鱼种数量</h4><ul>{Object.entries(preview.species_counts).map(([code, count]) => <li key={code}>{code}：{count}</li>)}</ul></div></div><h4>问题明细</h4>{preview.issues.length ? <ul>{preview.issues.map((issue, index) => <li key={`${issue.row}-${issue.code}-${index}`} className={issue.blocking ? "issue-blocking" : "issue-warning"}>{issue.blocking ? "阻断" : "警告"} · {issue.row ? `第 ${issue.row} 行 · ` : ""}{ISSUE_LABELS[issue.code] ?? `问题代码 ${issue.code}`}</li>)}</ul> : <p>没有问题。</p>}{preview.issues_truncated ? <p>另有 {preview.omitted_issue_details} 条问题明细未显示。</p> : null}
      {!confirming ? <button type="button" className="primary-button compact-button" disabled={!preview.can_commit || !preview.preview_token || pending} onClick={() => setConfirming(true)}>提交导入</button> : <div className="notice notice--error"><p>确认以单个事务提交本次预检查结果？</p><button type="button" className="danger-button" disabled={pending} onClick={() => void commit()}>确认提交导入</button><button type="button" className="secondary-button" onClick={() => setConfirming(false)}>取消</button></div>}
    </section> : null}
  </div>;
}
