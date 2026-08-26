import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, request } from "../api/client";
import type { QueryState } from "./common";
import { parseSpeciesList, type AdminSpeciesList } from "./types";

const PAGE_SIZE = 100;

export function useSpeciesDirectory(retryBootstrap: () => Promise<void>): QueryState<AdminSpeciesList> {
  const [data, setData] = useState<AdminSpeciesList | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [generation, setGeneration] = useState(0);
  const owner = useRef(0);
  const reload = useCallback(() => setGeneration((value) => value + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    const current = ++owner.current;
    setLoading(true); setError(null);
    void (async () => {
      const items: AdminSpeciesList["items"] = [];
      let total: number | null = null;
      for (let offset = 0; total === null || offset < total; offset += PAGE_SIZE) {
        const query = new URLSearchParams({ active: "true", limit: String(PAGE_SIZE), offset: String(offset) });
        const page = parseSpeciesList(await request<unknown>(`/admin/species?${query}`, { signal: controller.signal }));
        if (total === null) total = page.total;
        if (page.total !== total || (offset < total && page.items.length === 0)) throw new Error("鱼种目录响应无效");
        items.push(...page.items);
      }
      if (items.length !== total || new Set(items.map((item) => item.id)).size !== items.length) throw new Error("鱼种目录响应无效");
      return { total, items } as AdminSpeciesList;
    })().then((value) => {
      if (!controller.signal.aborted && current === owner.current) setData(value);
    }).catch((failure: unknown) => {
      if (controller.signal.aborted || current !== owner.current) return;
      if (failure instanceof ApiError && (failure.status === 401 || failure.status === 403)) { void retryBootstrap(); return; }
      setError("鱼种目录加载失败。");
    }).finally(() => {
      if (!controller.signal.aborted && current === owner.current) setLoading(false);
    });
    return () => controller.abort();
  }, [generation, retryBootstrap]);

  return { data, loading, error, unavailable: loading || error !== null, reload };
}
