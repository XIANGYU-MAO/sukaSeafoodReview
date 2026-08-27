import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { ApiError, request } from "../api/client";
import type { AdminSpecies, AdminUser } from "./types";

export interface AdminTabProps {
  csrfToken: string;
  retryBootstrap: () => Promise<void>;
  users: AdminUser[];
  species: Required<AdminSpecies>[];
  sources: string[];
  directoriesUnavailable: boolean;
  refreshDirectories: () => void;
  openSpecies: () => void;
}

export interface QueryState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  unavailable: boolean;
  reload: () => void;
}

export function useAdminQuery<T>(
  path: string | null,
  parse: (value: unknown) => T,
  retryBootstrap: () => Promise<void>,
): QueryState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(path !== null);
  const [error, setError] = useState<string | null>(null);
  const [generation, setGeneration] = useState(0);
  const activeGeneration = useRef(0);
  const reload = useCallback(() => setGeneration((value) => value + 1), []);

  useEffect(() => {
    if (!path) { setLoading(false); return; }
    const controller = new AbortController();
    const current = ++activeGeneration.current;
    setLoading(true);
    setError(null);
    void request<unknown>(path, { signal: controller.signal })
      .then((value) => parse(value))
      .then((value) => {
        if (!controller.signal.aborted && current === activeGeneration.current) setData(value);
      })
      .catch((failure: unknown) => {
        if (controller.signal.aborted || current !== activeGeneration.current) return;
        if (failure instanceof ApiError && (failure.status === 401 || failure.status === 403)) {
          void retryBootstrap();
          return;
        }
        setError(failure instanceof Error && failure.message.includes("响应无效") ? "服务返回了无效数据。" : "加载失败，请重试。");
      })
      .finally(() => {
        if (!controller.signal.aborted && current === activeGeneration.current) setLoading(false);
      });
    return () => controller.abort();
  }, [generation, parse, path, retryBootstrap]);
  return { data, loading, error, unavailable: loading || error !== null, reload };
}

export function QueryBoundary<T>({ query, children, empty = "暂无数据" }: { query: QueryState<T>; children: (data: T, unavailable: boolean) => ReactNode; empty?: string }) {
  if (query.loading && !query.data) return <p role="status" className="admin-state">正在加载…</p>;
  if (query.error && !query.data) return <div className="notice notice--error" role="alert">{query.error}<button type="button" className="text-button" onClick={query.reload}>重试</button></div>;
  if (!query.data) return <p className="admin-state">{empty}</p>;
  return <div className="admin-refresh-boundary" aria-busy={query.loading || undefined}>
    {query.loading ? <p role="status" className="notice">正在刷新，旧数据暂不可操作…</p> : null}
    {query.error ? <div className="notice notice--error" role="alert">刷新失败，旧数据暂不可操作。<button type="button" className="text-button" onClick={query.reload}>重试刷新</button></div> : null}
    {children(query.data, query.unavailable)}
  </div>;
}

export async function adminMutation<T>(
  path: string,
  options: { method: "POST" | "PATCH"; body: unknown; csrfToken: string },
  retryBootstrap: () => Promise<void>,
): Promise<T> {
  try {
    return await request<T>(path, { method: options.method, body: options.body, csrfToken: options.csrfToken });
  } catch (error) {
    if (error instanceof ApiError && (error.status === 401 || error.status === 403)) void retryBootstrap();
    throw error;
  }
}

export function mutationMessage(error: unknown, conflictText = "数据已被更新，请根据最新状态重试。"): string {
  if (error instanceof ApiError && error.status === 409) return conflictText;
  if (error instanceof Error && error.message.includes("结果无效")) return "服务返回无效结果，操作未确认成功。";
  return "操作失败，请重试。";
}

export function PageControls({ offset, total, limit, onChange, disabled = false }: { offset: number; total: number; limit: number; onChange: (next: number) => void; disabled?: boolean }) {
  return <div className="history-pagination">
    <button type="button" className="secondary-button" disabled={disabled || offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>上一页</button>
    <span>{Math.floor(offset / limit) + 1} / {Math.max(1, Math.ceil(total / limit))}</span>
    <button type="button" className="secondary-button" disabled={disabled || offset + limit >= total} onClick={() => onChange(offset + limit)}>下一页</button>
  </div>;
}

export const sourceLabels: Record<string, string> = {
  INATURALIST: "iNaturalist",
  iNaturalist: "iNaturalist",
  GBIF: "GBIF",
  WIKIMEDIA_COMMONS: "维基共享资源",
  Wikimedia: "维基共享资源",
  FISH_VISTA: "Fish-Vista",
};
export function sourceLabel(code: string): string { return sourceLabels[code] ?? code; }

export const decisionLabels = { APPROVED: "保留", REJECTED: "拒绝", UNSURE: "不确定" } as const;
export const rejectionLabels = {
  WRONG_SPECIES: "鱼种错误", NOT_WHOLE_FISH: "不是完整鱼体", NOT_A_FISH: "不是鱼", COOKED_OR_PROCESSED: "已烹饪或加工",
  TOO_OCCLUDED: "遮挡严重", TOO_SMALL_OR_BLURRY: "过小或模糊", DUPLICATE: "重复图片",
  ARTWORK_OR_DIAGRAM: "绘画或示意图", LICENSE_OR_SOURCE_CONCERN: "许可证或来源问题",
  IMAGE_URL_UNAVAILABLE: "图片地址失效", OTHER: "其他",
} as const;

export function safeHttps(value: string): boolean {
  try { const parsed = new URL(value); return value === value.trim() && !/\s/.test(value) && parsed.protocol === "https:" && Boolean(parsed.hostname) && !parsed.username && !parsed.password; }
  catch { return false; }
}
