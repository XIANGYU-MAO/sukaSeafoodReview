import { useCallback, useState } from "react";

import { API_BASE, ApiError } from "../api/client";
import {
  PageControls,
  QueryBoundary,
  adminMutation,
  mutationMessage,
  useAdminQuery,
  type AdminTabProps,
} from "./common";
import { HelpHint } from "./HelpHint";
import {
  parseExportBatches,
  parseExportConflict,
  parseExportCreate,
  parsePendingCounts,
  parseReceiptFile,
  parseReceiptResponse,
  type ExportBatch,
} from "./types";

export const MAX_RECEIPT_FILE_BYTES = 20 * 1024 * 1024;
export const receiptFileSizeAllowed = (size: number) => size <= MAX_RECEIPT_FILE_BYTES;

export function ExportsTab(props: AdminTabProps) {
  const parseCounts = useCallback(
    (value: unknown) => parsePendingCounts(value, props.species),
    [props.species],
  );
  const counts = useAdminQuery(
    props.species.length ? "/admin/exports/pending-counts" : null,
    parseCounts,
    props.retryBootstrap,
  );
  const [offset, setOffset] = useState(0);
  const batchQuery = new URLSearchParams({ limit: "20", offset: String(offset) });
  const batches = useAdminQuery(
    `/admin/exports?${batchQuery}`,
    parseExportBatches,
    props.retryBootstrap,
  );
  const [scope, setScope] = useState("");
  const [pending, setPending] = useState(false);
  const [draggingBatchId, setDraggingBatchId] = useState<string | null>(null);
  const [notice, setNotice] = useState<{
    kind: "error" | "success";
    text: string;
  } | null>(null);

  async function create() {
    if (pending || counts.unavailable || batches.unavailable || props.directoriesUnavailable) return;
    setPending(true);
    setNotice(null);
    const requestedScope = scope || null;
    try {
      const raw = await adminMutation<unknown>(
        "/admin/exports",
        {
          method: "POST",
          body: requestedScope ? { species_code: requestedScope } : {},
          csrfToken: props.csrfToken,
        },
        props.retryBootstrap,
      );
      const result = parseExportCreate(raw, requestedScope);
      setNotice(
        result.kind === "no-work"
          ? { kind: "success", text: "没有待同步项目。" }
          : {
              kind: "success",
              text: result.batch.created
                ? "已创建新的同步批次。"
                : "已返回现有未完成批次。",
            },
      );
      counts.reload();
      batches.reload();
    } catch (error) {
      const conflict =
        error instanceof ApiError && error.status === 409
          ? parseExportConflict(error.body)
          : null;
      const text =
        conflict?.code === "EXPORT_SCOPE_OVERLAP"
          ? `同步范围重叠，涉及 ${conflict.overlapCount} 个批次；列表已刷新。`
          : conflict?.code === "EXPORT_BATCH_EXPIRED"
            ? "同步批次已过期；列表已刷新。"
            : conflict?.code === "UNSAFE_SPECIES_CODE"
              ? "鱼种代码不适合本地路径；列表已刷新。"
              : mutationMessage(error);
      setNotice({ kind: "error", text });
      if (error instanceof ApiError && error.status === 409) {
        counts.reload();
        batches.reload();
      }
    } finally {
      setPending(false);
    }
  }

  async function receiptFile(batch: ExportBatch, file: File | null) {
    if (!file || pending) return;
    setNotice(null);
    if (!file.name.toLowerCase().endsWith(".json")) {
      setNotice({ kind: "error", text: "只接受 .json 回执文件。" });
      return;
    }
    if (!receiptFileSizeAllowed(file.size)) {
      setNotice({ kind: "error", text: "回执文件超过 20 MiB。" });
      return;
    }
    setPending(true);
    try {
      const parsedJson: unknown = JSON.parse(await file.text());
      const upload = parseReceiptFile(parsedJson, batch.id);
      const submitted = new Map(
        upload.items.map((item) => [item.candidate_id, item.status]),
      );
      const raw = await adminMutation<unknown>(
        `/admin/exports/${batch.id}/receipt-file`,
        { method: "POST", body: upload, csrfToken: props.csrfToken },
        props.retryBootstrap,
      );
      const result = parseReceiptResponse(raw, batch.id, submitted);
      setNotice({
        kind: "success",
        text: `回执已处理：接受 ${result.accepted}，待处理 ${result.pending}。`,
      });
      batches.reload();
      counts.reload();
    } catch (error) {
      setNotice({
        kind: "error",
        text:
          error instanceof Error && error.message.includes("批次不匹配")
            ? "回执批次不匹配或项目格式无效。"
            : mutationMessage(error),
      });
    } finally {
      setPending(false);
    }
  }

  const creationUnavailable =
    counts.unavailable || batches.unavailable || props.directoriesUnavailable;

  return (
    <div className="admin-stack export-workspace">
      {notice ? (
        <div
          className={`notice notice--${notice.kind}`}
          role={notice.kind === "error" ? "alert" : "status"}
        >
          {notice.text}
        </div>
      ) : null}

      <section className="admin-card export-workflow">
        <h2>训练数据同步流程</h2>
        <p>
          审核通过后，服务器只生成下载任务清单；原图由管理员自己的电脑直接从外部来源下载。
        </p>
        <ol className="export-workflow__steps">
          <li>
            <h3>1. 下载任务 CSV</h3>
            <p>CSV 只交给本地下载工具，不需要再上传到网页。</p>
          </li>
          <li>
            <h3>2. 在本地下载原图</h3>
            <p>本地工具读取 CSV、下载新的审核通过原图，并生成 JSON 回执。</p>
          </li>
          <li>
            <div className="admin-field-label">
              <h3>3. 上传 JSON 回执</h3>
              <HelpHint context="操作" label="上传下载回执">
                上传回执是为了告诉服务器哪些原图已经在本地下载成功。网页不会上传图片；成功项目以后不会重复加入下载批次，失败项目仍可重试。
              </HelpHint>
            </div>
            <p>把本地工具生成的 JSON 拖回对应批次，服务器才会记住下载结果。</p>
          </li>
        </ol>
      </section>

      <fieldset className="admin-fieldset" disabled={creationUnavailable}>
        <section className="admin-card export-create-card">
          <h3>创建增量同步批次</h3>
          <p>
            已经成功下载的原图会自动排除；下载失败或尚未处理的项目仍可继续同步。
          </p>
          <label>
            范围
            <select value={scope} onChange={(event) => setScope(event.target.value)}>
              <option value="">全部鱼种</option>
              {props.species.map((item) => (
                <option key={item.id} value={item.code}>
                  {item.code} · {item.name_zh}
                </option>
              ))}
            </select>
          </label>
          <button
            className="primary-button compact-button"
            type="button"
            disabled={pending}
            onClick={() => void create()}
          >
            创建同步批次
          </button>
        </section>
      </fieldset>

      <section className="admin-card">
        <h3>待同步数量</h3>
        <QueryBoundary query={counts}>
          {(data) => (
            <ul className="pending-counts">
              {props.species.map((item) => (
                <li key={item.code}>{item.code}：{data[item.code]}</li>
              ))}
            </ul>
          )}
        </QueryBoundary>
      </section>

      <section className="admin-card export-history">
        <h3>同步批次历史</h3>
        <p>每个批次都有自己的 CSV 和 JSON 回执，请在同一张批次卡片内完成操作。</p>
        <QueryBoundary query={batches}>
          {(data, unavailable) =>
            data.items.length ? (
              <>
                <div className="admin-card-grid export-batch-grid">
                  {data.items.map((batch) => {
                    const uploadDisabled =
                      pending || unavailable || props.directoriesUnavailable;
                    return (
                      <article className="export-batch-card" key={batch.id}>
                        <header className="export-batch-card__header">
                          <div>
                            <span className="export-batch-card__scope">
                              {batch.species_code ?? "全部鱼种"}
                            </span>
                            <h4 className="mono">{batch.id}</h4>
                          </div>
                          <span className={`export-batch-status export-batch-status--${batch.status}`}>
                            {batch.status === "pending"
                              ? "待处理"
                              : batch.status === "completed"
                                ? "已完成"
                                : "已过期"}
                          </span>
                        </header>
                        <dl className="export-batch-stats">
                          <div><dt>项目</dt><dd>{batch.item_count}</dd></div>
                          <div><dt>待处理</dt><dd>{batch.pending_count}</dd></div>
                        </dl>
                        <p className="export-batch-dates">
                          创建：{new Date(batch.created_at).toLocaleString("zh-CN")}<br />
                          过期：{new Date(batch.expires_at).toLocaleString("zh-CN")}
                        </p>
                        <a
                          className="primary-button compact-button export-download-link"
                          href={`${API_BASE}/admin/exports/${batch.id}.csv`}
                          download={`sukaseafood-export-${batch.id}.csv`}
                        >
                          下载任务 CSV
                        </a>
                        <div
                          className={`receipt-drop-zone${draggingBatchId === batch.id ? " receipt-drop-zone--active" : ""}`}
                          onDragEnter={(event) => {
                            event.preventDefault();
                            if (!uploadDisabled) setDraggingBatchId(batch.id);
                          }}
                          onDragOver={(event) => event.preventDefault()}
                          onDragLeave={() => setDraggingBatchId((current) =>
                            current === batch.id ? null : current
                          )}
                          onDrop={(event) => {
                            event.preventDefault();
                            setDraggingBatchId(null);
                            if (!uploadDisabled) {
                              void receiptFile(batch, event.dataTransfer.files?.[0] ?? null);
                            }
                          }}
                        >
                          <strong>把 JSON 回执拖到这里</strong>
                          <span>或</span>
                          <label className="receipt-upload" htmlFor={`receipt-${batch.id}`}>
                            选择 JSON 回执
                          </label>
                          <input
                            id={`receipt-${batch.id}`}
                            className="receipt-file-input"
                            aria-label={`上传 ${batch.id} 回执`}
                            type="file"
                            accept=".json,application/json"
                            disabled={uploadDisabled}
                            onChange={(event) => {
                              const file = event.target.files?.[0] ?? null;
                              event.currentTarget.value = "";
                              void receiptFile(batch, file);
                            }}
                          />
                        </div>
                      </article>
                    );
                  })}
                </div>
                <PageControls
                  offset={offset}
                  total={data.total}
                  limit={20}
                  onChange={setOffset}
                  disabled={unavailable}
                />
              </>
            ) : (
              <p>暂无同步批次。</p>
            )
          }
        </QueryBoundary>
      </section>
    </div>
  );
}
