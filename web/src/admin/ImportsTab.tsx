import { useEffect, useMemo, useRef, useState } from "react";

import { API_BASE, ApiError, request, WEB_BASE } from "../api/client";
import { adminMutation, mutationMessage, type AdminTabProps } from "./common";
import { HelpHint } from "./HelpHint";
import { parseImportPreview, parseImportResult, type ImportIssueGroup, type ImportPreview } from "./types";

const ISSUE_LABELS: Record<string, string> = {
  EXACT_DUPLICATE: "完全重复记录（自动跳过）",
  DUPLICATE_IMAGE_URL: "同鱼种重复图片地址（自动跳过）",
  CONFLICTING_IMAGE_SPECIES: "同一图片被分到不同鱼种",
  UNAPPROVED_IMAGE_HOST: "图片来源待管理员批准",
  INVALID_SPECIES: "无效鱼种",
  MISSING_URL: "缺少图片地址",
  INVALID_LICENSE: "无效许可证",
  UNSUPPORTED_SOURCE: "不支持的来源",
  CSV_TOO_LARGE: "CSV 文件过大",
  CSV_TOO_MANY_ROWS: "CSV 行数过多",
  CSV_INVALID_ENCODING: "CSV 编码无效",
  CSV_INVALID_HEADER: "CSV 列名无效",
  CSV_MISSING_HEADERS: "CSV 缺少必要列",
  CSV_DUPLICATE_HEADERS: "CSV 存在重复列",
  CSV_MALFORMED: "CSV 格式错误",
  CONFLICTING_SOURCE_IDENTITY: "来源身份冲突",
  FIELD_TOO_LONG: "字段内容过长",
  INVALID_CONTROL_CHARACTER: "字段包含控制字符",
  INVALID_LICENSE_URL: "许可证地址无效",
  METADATA_TOO_LARGE: "元数据过大",
  MISSING_SOURCE_IDENTITY: "缺少来源身份",
  UNPARSED_SOURCE_DATE: "来源日期无法解析（不影响导入）",
  UNSAFE_URL: "地址格式确实不安全",
};

const DUPLICATE_ISSUE_CODES = new Set(["EXACT_DUPLICATE", "DUPLICATE_IMAGE_URL"]);

function issueExamples(issue: ImportIssueGroup): string | null {
  if (!issue.sample_rows.length) return null;
  if (DUPLICATE_ISSUE_CODES.has(issue.code)) {
    const pairs = issue.sample_rows.map((row, index) => {
      const related = issue.sample_related_rows[index];
      return related === null ? `第 ${row} 行与系统已有图片重复` : `第 ${row} 行与第 ${related} 行重复`;
    });
    return `${pairs.join("；")}${issue.omitted_rows ? `；另有 ${issue.omitted_rows} 行` : ""}`;
  }
  return `示例行：${issue.sample_rows.join("、")}${issue.omitted_rows ? `，另有 ${issue.omitted_rows} 行` : ""}`;
}

type Confirmation = "normal" | "skip" | null;
type CommandPlatform = "windows" | "unix";
type CollectionMode = "initial" | "replenish";

const COLLECTOR_SOURCES = [
  { code: "fish-vista", label: "Fish-Vista" },
  { code: "inat", label: "iNaturalist" },
  { code: "gbif", label: "GBIF" },
  { code: "commons", label: "维基共享资源" },
  { code: "ala", label: "澳大利亚生命地图集" },
  { code: "obis", label: "OBIS 海洋生物地理信息系统" },
  { code: "noaa", label: "NOAA 图片库" },
  { code: "smithsonian", label: "Smithsonian 开放资源" },
] as const;
const DEFAULT_COLLECTOR_SOURCES = COLLECTOR_SOURCES
  .filter((source) => source.code !== "smithsonian")
  .map((source) => source.code);

export function ImportsTab(props: AdminTabProps) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [pending, setPending] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [confirmation, setConfirmation] = useState<Confirmation>(null);
  const [dragging, setDragging] = useState(false);
  const [platform, setPlatform] = useState<CommandPlatform>("windows");
  const [collectionMode, setCollectionMode] = useState<CollectionMode>("initial");
  const [maxPerSpecies, setMaxPerSpecies] = useState(100);
  const [minimumPerSpecies, setMinimumPerSpecies] = useState(300);
  const [selectedSources, setSelectedSources] = useState<string[]>(DEFAULT_COLLECTOR_SOURCES);
  const [smithsonianApiKey, setSmithsonianApiKey] = useState("");
  const [copyState, setCopyState] = useState<"idle" | "success" | "error">("idle");
  const [notice, setNotice] = useState<{ kind: "error" | "success"; text: string } | null>(null);
  const generation = useRef(0);
  const operationKind = useRef<"preview" | "commit" | null>(null);
  const mounted = useRef(true);
  const previewController = useRef<AbortController | null>(null);
  const packageUrl = `${WEB_BASE}downloads/sukaseafood-collector.zip`;
  const configUrl = `${API_BASE}/admin/collector/config`;
  const hasActiveSpecies = props.species.some((species) => species.active);
  const activeSpecies = props.species.filter((species) => species.active);
  const command = useMemo(() => {
    const python = "python";
    const separator = platform === "windows" ? ".\\" : "./";
    const resume = collectionMode === "replenish" ? " --resume" : "";
    const sources = COLLECTOR_SOURCES
      .filter((source) => selectedSources.includes(source.code))
      .map((source) => ` --source ${source.code}`)
      .join("");
    const key = selectedSources.includes("smithsonian") && smithsonianApiKey
      ? ` --smithsonian-api-key ${smithsonianApiKey}`
      : "";
    return `${python} ${separator}collect_fish_images.py --config ${separator}species_config.json${sources}${key} --max-per-species ${maxPerSpecies} --minimum-total-per-species ${minimumPerSpecies}${resume}`;
  }, [collectionMode, maxPerSpecies, minimumPerSpecies, platform, selectedSources, smithsonianApiKey]);

  useEffect(() => setCopyState("idle"), [command]);

  function toggleSource(code: string) {
    if (selectedSources.includes(code)) {
      if (selectedSources.length === 1) {
        setNotice({ kind: "error", text: "至少选择一个采集来源。" });
        return;
      }
      setSelectedSources(selectedSources.filter((source) => source !== code));
    } else {
      setSelectedSources([...selectedSources, code]);
    }
    setNotice(null);
  }

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      generation.current += 1;
      previewController.current?.abort();
      previewController.current = null;
    };
  }, []);

  function choose(next: File | null) {
    if (operationKind.current === "commit") return;
    generation.current += 1;
    previewController.current?.abort();
    previewController.current = null;
    operationKind.current = null;
    setPending(false);
    setFile(next);
    setPreview(null);
    setConfirmation(null);
    setNotice(null);
  }

  async function previewFile(selectedFile: File) {
    if (operationKind.current !== null) return;
    if (!selectedFile.name.toLowerCase().endsWith(".csv")) {
      setNotice({ kind: "error", text: "只接受 .csv 文件。" });
      return;
    }
    operationKind.current = "preview";
    const owner = ++generation.current;
    previewController.current?.abort();
    const controller = new AbortController();
    previewController.current = controller;
    const form = new FormData();
    form.append("file", selectedFile);
    setPreview(null);
    setConfirmation(null);
    setPending(true);
    setNotice(null);
    try {
      const raw = await request<unknown>("/admin/imports/preview", {
        method: "POST",
        body: form,
        csrfToken: props.csrfToken,
        signal: controller.signal,
      });
      const parsed = parseImportPreview(raw);
      if (!mounted.current || owner !== generation.current || controller.signal.aborted || selectedFile !== file) return;
      setPreview(parsed);
      setNotice(parsed.can_commit ? null : {
        kind: "error",
        text: parsed.new_rows > 0
          ? "发现阻断行：可先批准待批准来源，或明确选择跳过阻断行并导入其余有效行。"
          : "发现阻断问题，当前没有可导入的有效行。请处理下方问题后重新预检查。",
      });
    } catch (error) {
      if (!mounted.current || owner !== generation.current || controller.signal.aborted) return;
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) void props.retryBootstrap();
      const detail = error instanceof ApiError && typeof error.body === "object" && error.body && "detail" in error.body && typeof error.body.detail === "object" && error.body.detail ? error.body.detail as Record<string, unknown> : null;
      const code = detail && typeof detail.code === "string" ? detail.code : "";
      if (error instanceof ApiError && (error.status === 413 || error.status === 422) && detail && "report" in detail) {
        try {
          const report = parseImportPreview(detail.report);
          if (!report.can_commit && report.preview_token === null) setPreview(report);
        } catch {
          // Malformed server reports remain opaque.
        }
      }
      setNotice({ kind: "error", text: ISSUE_LABELS[code] ?? "预检查失败，请检查 CSV 后重试。" });
    } finally {
      if (mounted.current && owner === generation.current) {
        operationKind.current = null;
        setPending(false);
        previewController.current = null;
      }
    }
  }

  async function runPreview() {
    if (file) await previewFile(file);
  }

  async function approveOrigin(host: string) {
    if (!file || !preview?.preview_token || operationKind.current !== null) return;
    operationKind.current = "preview";
    const selectedFile = file;
    setPending(true);
    setNotice(null);
    let approved = false;
    try {
      await adminMutation<unknown>("/admin/imports/approve-origin", {
        method: "POST",
        body: { preview_token: preview.preview_token, hostname: host },
        csrfToken: props.csrfToken,
      }, props.retryBootstrap);
      approved = true;
    } catch (error) {
      setNotice({ kind: "error", text: error instanceof ApiError && error.status === 409 ? "这份预检查已变化，请重新预检查。" : mutationMessage(error) });
    } finally {
      operationKind.current = null;
      setPending(false);
    }
    if (approved && mounted.current) await previewFile(selectedFile);
  }

  async function commit(skipBlockingRows: boolean) {
    if (!preview?.preview_token || operationKind.current !== null) return;
    if (!skipBlockingRows && !preview.can_commit) return;
    if (skipBlockingRows && (preview.can_commit || preview.new_rows === 0)) return;
    operationKind.current = "commit";
    const owner = ++generation.current;
    const committedPreview = preview;
    setPending(true);
    setCommitting(true);
    setNotice(null);
    try {
      const raw = await adminMutation<unknown>("/admin/imports/commit", {
        method: "POST",
        body: { preview_token: committedPreview.preview_token, skip_blocking_rows: skipBlockingRows },
        csrfToken: props.csrfToken,
      }, props.retryBootstrap);
      const result = parseImportResult(raw, committedPreview, skipBlockingRows);
      if (!mounted.current || owner !== generation.current) return;
      setPreview(null);
      setFile(null);
      setConfirmation(null);
      setNotice({ kind: "success", text: `导入完成：新增 ${result.inserted}，跳过完全重复 ${result.skipped_exact}，跳过同鱼种重复地址 ${result.skipped_url_duplicates}，跳过阻断行 ${result.skipped_blocking}。` });
    } catch (error) {
      if (!mounted.current || owner !== generation.current) return;
      if (error instanceof ApiError && error.status === 409) {
        setPreview(null);
        setConfirmation(null);
      }
      setNotice({ kind: "error", text: error instanceof ApiError && error.status === 409 ? "预检查已过期、已使用或与当前会话/数据库不一致，请重新预检查。" : mutationMessage(error) });
    } finally {
      if (mounted.current && owner === generation.current) {
        operationKind.current = null;
        setPending(false);
        setCommitting(false);
      }
    }
  }

  async function copyCommand() {
    try {
      await navigator.clipboard.writeText(command);
      setCopyState("success");
    } catch {
      setCopyState("error");
    }
  }

  const summaries = preview ? [
    ["总行数", preview.total],
    ["新增", preview.new_rows],
    ["完全重复", preview.exact_duplicates],
    ["同鱼种重复地址", preview.url_duplicates],
    ["无效鱼种", preview.invalid_species],
    ["缺少地址", preview.missing_urls],
    ["无效许可证", preview.invalid_licenses],
    ["无效来源", preview.invalid_sources],
    ["身份冲突", preview.conflicting_identities],
    ["解析错误", preview.parse_errors],
    ["警告", preview.warnings],
    ["阻断问题", preview.blocking_errors],
  ] : [];

  return <div className="admin-stack">
    {notice ? <div className={`notice notice--${notice.kind}`} role={notice.kind === "error" ? "alert" : "status"}>{notice.text}</div> : null}
    <section className="admin-card">
      <h3>1. 管理鱼种</h3>
      <p>当前启用鱼种：{activeSpecies.length} 种。先维护鱼种和需要的来源覆盖值。</p>
      {activeSpecies.length > 0 ? <ul aria-label="当前启用鱼种">{activeSpecies.map((species) => <li key={species.id}>{species.code} · {species.name_zh}</li>)}</ul> : null}
      <button type="button" className="secondary-button" onClick={props.openSpecies}>前往鱼种管理</button>
    </section>
    <section className="admin-card">
      <h3>2. 准备本地采集器</h3>
      <p>首次使用请下载采集器 ZIP；鱼种更新后下载最新配置。</p>
      <div className="inline-actions equal-action-row" role="group" aria-label="采集器下载">
        <a className="primary-button compact-button" href={packageUrl} download>下载采集器 ZIP</a>
        {hasActiveSpecies ? <a className="secondary-button" href={configUrl}>下载最新鱼种配置</a> : <button type="button" className="secondary-button" disabled>下载最新鱼种配置</button>}
      </div>
      {hasActiveSpecies ? null : <p>请先在鱼种管理中新增并启用鱼种。</p>}
    </section>
    <section className="admin-card">
      <h3>3. 本地生成 CSV</h3>
      <p>解压 ZIP、安装 requirements.txt，并把最新 species_config.json 放到采集器目录。</p>
      <div className="command-options">
        <fieldset className="command-option-group"><legend>命令格式</legend><div className="command-pill-group">
          <button type="button" className={`pill-choice${platform === "windows" ? " pill-choice--selected" : ""}`} aria-pressed={platform === "windows"} onClick={() => setPlatform("windows")}>Windows</button>
          <button type="button" className={`pill-choice${platform === "unix" ? " pill-choice--selected" : ""}`} aria-pressed={platform === "unix"} onClick={() => setPlatform("unix")}>macOS / Linux</button>
        </div></fieldset>
        <fieldset className="command-option-group"><legend>采集方式</legend><div className="command-pill-group">
          <button type="button" className={`pill-choice${collectionMode === "initial" ? " pill-choice--selected" : ""}`} aria-pressed={collectionMode === "initial"} onClick={() => setCollectionMode("initial")}>首次采集</button>
          <button type="button" className={`pill-choice${collectionMode === "replenish" ? " pill-choice--selected" : ""}`} aria-pressed={collectionMode === "replenish"} onClick={() => setCollectionMode("replenish")}>数量不足时补采</button>
        </div></fieldset>
        <div className="command-option-group command-option-group--wide"><div className="admin-field-label"><strong>采集来源</strong><HelpHint context="字段" label="来源选择说明">只会请求已选中的来源，不选的来源完全不会访问。建议先保留全部免密来源；某个来源质量不合适时可取消。Smithsonian 需要免费的 Open Access API Key，密钥只写进你本地复制的命令，不会上传到本系统。</HelpHint></div><div className="command-pill-group command-pill-group--sources" role="group" aria-label="采集来源">
          {COLLECTOR_SOURCES.map((source) => {
            const selected = selectedSources.includes(source.code);
            return <button key={source.code} type="button" className={`pill-choice${selected ? " pill-choice--selected" : ""}`} aria-pressed={selected} onClick={() => toggleSource(source.code)}>{source.label}</button>;
          })}
        </div></div>
        {selectedSources.includes("smithsonian") ? <div className="command-number-field command-number-field--wide"><label htmlFor="smithsonian-api-key">Smithsonian API Key</label><input id="smithsonian-api-key" type="password" autoComplete="off" value={smithsonianApiKey} onChange={(event) => setSmithsonianApiKey(event.target.value.replace(/[^A-Za-z0-9_-]/g, ""))} /><small>先在 api.data.gov 免费申请；未填写时 Smithsonian 会提示缺少密钥，其余来源仍可继续。</small></div> : null}
        <div className="command-number-field command-number-field--target"><div className="admin-field-label"><label htmlFor="collector-minimum">每个鱼种候选数至少达到</label><HelpHint context="字段" label="最低候选目标">这是服务器中每个鱼种希望达到的候选图片总数。最新鱼种配置会记录当前数量；采集器只补不足的鱼种，达到目标的会跳过。来源图片可能不足或被系统判重，所以一次未达到时，先导入 CSV，再重新下载最新配置继续补采。</HelpHint></div><input id="collector-minimum" type="number" min="1" max="10000" value={minimumPerSpecies} onChange={(event) => setMinimumPerSpecies(Math.max(1, Math.min(10000, Number(event.target.value) || 1)))} /></div>
        <div className="command-number-field"><div className="admin-field-label"><label htmlFor="collector-max">每个鱼种、每个来源最多采集</label><HelpHint context="字段" label="采集数量参数">这是每个鱼种在每个来源尝试收集的上限，不是最终保证数量。审核后不够时，调大这个数字并选择“数量不足时补采”；采集器保留旧 CSV，导入时系统也会按来源身份和原图地址自动去重。</HelpHint></div><input id="collector-max" type="number" min="1" max="10000" value={maxPerSpecies} onChange={(event) => setMaxPerSpecies(Math.max(1, Math.min(10000, Number(event.target.value) || 1)))} /></div>
      </div>
      {activeSpecies.length ? <ul className="collector-shortfalls" aria-label="鱼种候选缺口">{activeSpecies.map((species) => {
        const shortfall = Math.max(0, minimumPerSpecies - species.candidate_count);
        return <li key={species.id} className={shortfall === 0 ? "collector-shortfalls__reached" : ""}><strong>{species.code}</strong><span>{species.name_zh}</span><span>当前 {species.candidate_count}</span><span>{shortfall ? `还差 ${shortfall}` : "已达到"}</span></li>;
      })}</ul> : null}
      <div className="collector-command-panel">
        <strong>运行命令</strong>
        <p className="collector-command"><code>{command}</code></p>
        <div className="collector-copy-row">
          <button type="button" className="secondary-button collector-copy-button" onClick={() => void copyCommand()}>{copyState === "success" ? "已复制" : copyState === "error" ? "重新复制" : "复制命令"}</button>
          {copyState === "success" ? <span className="collector-copy-feedback collector-copy-feedback--success" role="status">命令已复制到剪贴板。</span> : null}
          {copyState === "error" ? <span className="collector-copy-feedback collector-copy-feedback--error" role="alert">复制失败，请手动选择命令或重试。</span> : null}
        </div>
      </div>
      <p>输出文件为 <code>output/candidates.csv</code>。“首次采集”会重写这份本地 CSV；“数量不足时补采”自动添加 <code>--resume</code>，保留旧行并合并去重。上传时服务器还会再次去重。</p>
    </section>
    <section className="admin-card">
      <h3>4. 预检查并导入</h3>
      <p>只读取 CSV 文本进行预检查；不会在浏览器中请求任何图片地址。</p>
      <div className={`csv-drop-zone${dragging ? " csv-drop-zone--active" : ""}`} onDragEnter={(event) => { event.preventDefault(); if (!committing) setDragging(true); }} onDragOver={(event) => { event.preventDefault(); }} onDragLeave={(event) => { if (event.currentTarget === event.target) setDragging(false); }} onDrop={(event) => { event.preventDefault(); setDragging(false); if (!committing) choose(event.dataTransfer.files?.[0] ?? null); }}>
        <label htmlFor="candidate-csv"><strong>把候选 CSV 拖到这里</strong><span>，或点击选择文件</span><small>{file ? `已选择：${file.name}` : "仅接受 .csv 文件"}</small></label>
        <input id="candidate-csv" aria-label="候选 CSV 文件" type="file" accept=".csv,text/csv" disabled={committing} onChange={(event) => { if (operationKind.current === "commit") { event.currentTarget.value = ""; return; } choose(event.target.files?.[0] ?? null); }} />
      </div>
      <div className="inline-actions equal-action-row"><button type="button" className="primary-button compact-button" disabled={!file || pending} onClick={() => void runPreview()}>{pending ? "处理中…" : "预检查"}</button></div>
      {file && (!preview || !preview.preview_token) ? <button type="button" className="primary-button compact-button" disabled>提交导入</button> : null}
      {preview ? <section className="admin-card-subsection">
        <h4>预检查摘要</h4>
        <div className="admin-stat-grid">{summaries.map(([label, count]) => <div key={label}><span>{label}</span><strong>{count}</strong></div>)}</div>
        <div className="admin-split"><div><h4>来源数量</h4><ul>{Object.entries(preview.source_counts).map(([code, count]) => <li key={code}>{code}：{count}</li>)}</ul></div><div><h4>鱼种数量</h4><ul>{Object.entries(preview.species_counts).map(([code, count]) => <li key={code}>{code}：{count}</li>)}</ul></div></div>
        <h4>问题汇总</h4>
        {preview.issue_groups.length ? <ul className="import-issue-groups">{preview.issue_groups.map((issue) => <li key={`${issue.code}-${issue.host ?? "none"}`} className={issue.blocking ? "issue-blocking" : "issue-warning"}><div><strong>{issue.blocking ? "阻断" : "警告"} · {ISSUE_LABELS[issue.code] ?? issue.message}</strong>{issue.host ? <span> · {issue.host}</span> : null}<span> · 共 {issue.count} 行</span>{issueExamples(issue) ? <small>{issueExamples(issue)}</small> : null}</div>{issue.code === "UNAPPROVED_IMAGE_HOST" && issue.host && preview.preview_token ? <button type="button" className="secondary-button" disabled={pending} onClick={() => void approveOrigin(issue.host!)}>批准此来源并重新预检查</button> : null}</li>)}</ul> : <p>没有问题。</p>}
        {confirmation === null ? <div className="inline-actions equal-action-row">
          {preview.can_commit ? <button type="button" className="primary-button compact-button" disabled={!preview.preview_token || pending} onClick={() => setConfirmation("normal")}>提交导入</button> : null}
          {!preview.can_commit && preview.new_rows > 0 ? <button type="button" className="danger-button" disabled={!preview.preview_token || pending} onClick={() => setConfirmation("skip")}>跳过阻断行并导入有效行</button> : null}
        </div> : confirmation === "normal" ? <div className="notice notice--error"><p>确认以单个事务提交本次预检查结果？</p><div className="inline-actions equal-action-row"><button type="button" className="danger-button" disabled={pending} onClick={() => void commit(false)}>确认提交导入</button><button type="button" className="secondary-button" disabled={pending} onClick={() => setConfirmation(null)}>取消</button></div></div> : <div className="notice notice--error"><p>确认忽略全部阻断行，只导入上方显示的 {preview.new_rows} 行有效数据？被跳过的行不会进入审核队列。</p><div className="inline-actions equal-action-row"><button type="button" className="danger-button" disabled={pending} onClick={() => void commit(true)}>确认跳过并导入</button><button type="button" className="secondary-button" disabled={pending} onClick={() => setConfirmation(null)}>取消</button></div></div>}
      </section> : null}
    </section>
  </div>;
}
