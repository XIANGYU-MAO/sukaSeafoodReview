import { useCallback, useEffect, useRef, useState } from "react";

import { CandidatesTab } from "../admin/CandidatesTab";
import { ExportsTab } from "../admin/ExportsTab";
import { ImportsTab } from "../admin/ImportsTab";
import { ProgressTab } from "../admin/ProgressTab";
import { ReviewsTab } from "../admin/ReviewsTab";
import { SpeciesTab } from "../admin/SpeciesTab";
import { UsersTab } from "../admin/UsersTab";
import { useAdminQuery } from "../admin/common";
import { useSpeciesDirectory } from "../admin/directory";
import { parseAdminSources, parseAdminUsers } from "../admin/types";

const TABS = [
  ["progress", "审核进度", ProgressTab],
  ["candidates", "候选图片", CandidatesTab],
  ["species", "鱼种管理", SpeciesTab],
  ["reviews", "审核历史", ReviewsTab],
  ["imports", "导入", ImportsTab],
  ["exports", "训练集同步", ExportsTab],
  ["users", "账号", UsersTab],
] as const;
const EMPTY_USERS: never[] = [];
const EMPTY_SPECIES: never[] = [];
const EMPTY_SOURCES: string[] = [];

export function AdminPage({ csrfToken, retryBootstrap }: { csrfToken: string; retryBootstrap: () => Promise<void> }) {
  const [active, setActive] = useState(0);
  const [visited, setVisited] = useState(() => new Set([0]));
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const usersQuery = useAdminQuery("/admin/users", parseAdminUsers, retryBootstrap);
  const speciesQuery = useSpeciesDirectory(retryBootstrap);
  const sourcesQuery = useAdminQuery("/admin/sources", parseAdminSources, retryBootstrap);
  const refreshDirectories = useCallback(() => { usersQuery.reload(); speciesQuery.reload(); sourcesQuery.reload(); }, [sourcesQuery.reload, speciesQuery.reload, usersQuery.reload]);

  useEffect(() => { setVisited((current) => current.has(active) ? current : new Set(current).add(active)); }, [active]);

  function select(index: number) {
    setActive(index);
    queueMicrotask(() => tabRefs.current[index]?.focus());
  }
  function onKeyDown(event: React.KeyboardEvent<HTMLButtonElement>, index: number) {
    let next: number | null = null;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") next = (index + 1) % TABS.length;
    if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = (index - 1 + TABS.length) % TABS.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = TABS.length - 1;
    if (next !== null) { event.preventDefault(); select(next); }
  }

  const shared = {
    csrfToken,
    retryBootstrap,
    users: usersQuery.data?.items ?? EMPTY_USERS,
    species: speciesQuery.data?.items ?? EMPTY_SPECIES,
    sources: sourcesQuery.data?.sources ?? EMPTY_SOURCES,
    directoriesUnavailable: usersQuery.unavailable || speciesQuery.unavailable || sourcesQuery.unavailable,
    refreshDirectories,
  };

  return <main className="admin-workspace" lang="zh-CN">
    <div className="admin-heading"><p className="eyebrow">SukaSeafood</p><h1>管理后台</h1><p>管理共享审核、目录、导入和本地训练集同步。</p></div>
    {usersQuery.error || speciesQuery.error || sourcesQuery.error ? <div role="alert" className="notice notice--error">管理选项加载失败。<button className="text-button" type="button" onClick={refreshDirectories}>重试</button></div> : null}
    <div className="admin-tabs" role="tablist" aria-label="后台管理功能">
      {TABS.map(([key, label], index) => <button
        key={key}
        ref={(node) => { tabRefs.current[index] = node; }}
        id={`admin-tab-${key}`}
        type="button"
        role="tab"
        aria-selected={active === index}
        aria-controls={`admin-panel-${key}`}
        tabIndex={active === index ? 0 : -1}
        onClick={() => select(index)}
        onKeyDown={(event) => onKeyDown(event, index)}
      >{label}</button>)}
    </div>
    {TABS.map(([key, label, Component], index) => visited.has(index) ? <div
      key={key}
      id={`admin-panel-${key}`}
      role={active === index ? "tabpanel" : undefined}
      aria-labelledby={active === index ? `admin-tab-${key}` : undefined}
      hidden={active !== index}
      className="admin-panel"
    ><h2 className="visually-hidden">{label}</h2><Component {...shared} /></div> : null)}
  </main>;
}
