import { useEffect, useRef, useState } from "react";

import { ApiError } from "../api/client";
import {
  QueryBoundary,
  adminMutation,
  mutationMessage,
  type AdminTabProps,
  useAdminQuery,
} from "./common";
import {
  parseSystemSettings,
  parseTemporaryPassword,
  type AdminUser,
  type SystemSettings,
} from "./types";

export function UsersTab(props: AdminTabProps) {
  const settingsQuery = useAdminQuery(
    "/admin/settings",
    parseSystemSettings,
    props.retryBootstrap,
  );
  const [settingsDraft, setSettingsDraft] = useState<SystemSettings | null>(null);
  const [settingsPending, setSettingsPending] = useState(false);
  const [settingsNotice, setSettingsNotice] = useState<{ kind: "error" | "success"; text: string } | null>(null);
  const [selected, setSelected] = useState<AdminUser | null>(null);
  const [reason, setReason] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);
  const [password, setPassword] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ kind: "error" | "success"; text: string } | null>(null);
  const secretRegion = useRef<HTMLElement | null>(null);
  const restoreFocus = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (settingsQuery.data) setSettingsDraft(settingsQuery.data);
  }, [settingsQuery.data]);
  useEffect(() => { if (password) secretRegion.current?.focus(); }, [password]);

  async function saveSettings() {
    if (!settingsDraft || settingsPending || settingsQuery.unavailable) return;
    setSettingsPending(true);
    setSettingsNotice(null);
    try {
      const raw = await adminMutation<unknown>(
        "/admin/settings",
        {
          method: "PATCH",
          body: {
            ...settingsDraft,
            reason: "更新登录与团队记录可见性",
          },
          csrfToken: props.csrfToken,
        },
        props.retryBootstrap,
      );
      const saved = parseSystemSettings(raw);
      setSettingsDraft(saved);
      setSettingsNotice({ kind: "success", text: "访问设置已保存。" });
      settingsQuery.reload();
    } catch (error) {
      setSettingsNotice({ kind: "error", text: mutationMessage(error) });
    } finally {
      setSettingsPending(false);
    }
  }

  function start(user: AdminUser) {
    restoreFocus.current = document.activeElement instanceof HTMLButtonElement
      ? document.activeElement
      : null;
    setSelected(user);
    setReason("");
    setConfirming(false);
    setNotice(null);
    setPassword(null);
  }

  function continueReset() {
    if (!reason.trim()) {
      setNotice({ kind: "error", text: "必须填写密码重置原因。" });
      return;
    }
    setConfirming(true);
    setNotice(null);
  }

  async function reset() {
    if (!selected || selected.role === "admin" || pending || !confirming || props.directoriesUnavailable) return;
    setPending(true);
    setNotice(null);
    try {
      const raw = await adminMutation<unknown>(
        `/admin/users/${selected.id}/reset-password`,
        {
          method: "POST",
          body: { reason: reason.trim() },
          csrfToken: props.csrfToken,
        },
        props.retryBootstrap,
      );
      setPassword(parseTemporaryPassword(raw));
      setConfirming(false);
      setSelected(null);
      setReason("");
    } catch (error) {
      setNotice({
        kind: "error",
        text: error instanceof ApiError && error.status === 409
          ? "该账号不能通过网页重置，请刷新账号状态。"
          : mutationMessage(error),
      });
    } finally {
      setPending(false);
    }
  }

  async function copy() {
    if (!password) return;
    try {
      await navigator.clipboard.writeText(password);
      setNotice({ kind: "success", text: "临时密码已复制。" });
    } catch {
      setNotice({ kind: "error", text: "复制失败，请手动选择并复制。" });
    }
  }

  function dismiss() {
    setPassword(null);
    setNotice(null);
    queueMicrotask(() => restoreFocus.current?.focus());
  }

  return <div className="admin-stack">
    <section className="admin-card admin-access-settings">
      <h3>访问设置</h3>
      <QueryBoundary query={settingsQuery}>
        {() => settingsDraft ? <fieldset className="admin-fieldset" disabled={settingsPending || settingsQuery.unavailable}>
          {settingsNotice ? <div className={`notice notice--${settingsNotice.kind}`} role={settingsNotice.kind === "error" ? "alert" : "status"}>{settingsNotice.text}</div> : null}
          <div className="admin-setting-grid">
            <div>
              <strong>登录页姓名方式</strong>
              <p>账号仍是现有 6 人；这里只控制登录页是否直接展示姓名。</p>
              <div className="command-pill-group" role="group" aria-label="登录页姓名方式">
                <button type="button" className={`pill-choice${settingsDraft.login_name_mode === "choices" ? " pill-choice--selected" : ""}`} aria-pressed={settingsDraft.login_name_mode === "choices"} onClick={() => setSettingsDraft({ ...settingsDraft, login_name_mode: "choices" })}>展示账号按钮</button>
                <button type="button" className={`pill-choice${settingsDraft.login_name_mode === "manual" ? " pill-choice--selected" : ""}`} aria-pressed={settingsDraft.login_name_mode === "manual"} onClick={() => setSettingsDraft({ ...settingsDraft, login_name_mode: "manual" })}>手动输入姓名</button>
              </div>
            </div>
            <div>
              <strong>团队记录可见性</strong>
              <p>隐藏后，审核员看不到菜单，也不能直接打开团队记录；管理员仍可查看。</p>
              <div className="command-pill-group" role="group" aria-label="团队记录可见性">
                <button type="button" className={`pill-choice${settingsDraft.reviewer_team_progress_visible ? " pill-choice--selected" : ""}`} aria-pressed={settingsDraft.reviewer_team_progress_visible} onClick={() => setSettingsDraft({ ...settingsDraft, reviewer_team_progress_visible: true })}>显示团队记录</button>
                <button type="button" className={`pill-choice${!settingsDraft.reviewer_team_progress_visible ? " pill-choice--selected" : ""}`} aria-pressed={!settingsDraft.reviewer_team_progress_visible} onClick={() => setSettingsDraft({ ...settingsDraft, reviewer_team_progress_visible: false })}>隐藏团队记录</button>
              </div>
            </div>
          </div>
          <button type="button" className="primary-button compact-button" disabled={settingsPending} onClick={() => void saveSettings()}>{settingsPending ? "保存中…" : "保存访问设置"}</button>
        </fieldset> : <p>正在加载设置…</p>}
      </QueryBoundary>
    </section>

    {notice ? <div className={`notice notice--${notice.kind}`} role={notice.kind === "error" ? "alert" : "status"}>{notice.text}</div> : null}
    <section className="admin-card">
      <h3>固定账号</h3>
      <div className="admin-table-wrap"><table className="admin-account-table"><thead><tr><th>姓名</th><th>角色</th><th>状态</th><th>操作</th></tr></thead><tbody>{props.users.map((user) => <tr className="admin-account-row" key={user.id}><th>{user.display_name}</th><td>{user.role === "admin" ? "管理员" : "审核员"}</td><td>{user.active ? "启用" : "停用"}</td><td>{user.role === "reviewer" ? <button type="button" className="danger-button" aria-label={`重置 ${user.display_name} 密码`} disabled={props.directoriesUnavailable} onClick={() => start(user)}>重置密码</button> : <span className="admin-account-action-placeholder">管理员账号只能通过服务器命令重置</span>}</td></tr>)}</tbody></table></div>
    </section>

    {selected ? <fieldset className="admin-fieldset" disabled={props.directoriesUnavailable}><section className="admin-card admin-confirm"><h3>重置 {selected.display_name} 密码</h3><label>密码重置原因<textarea aria-label="密码重置原因" value={reason} onChange={(event) => setReason(event.target.value)} /></label>{!confirming ? <button type="button" className="danger-button" onClick={continueReset}>继续重置 {selected.display_name}</button> : <div className="notice notice--error"><p>即将撤销 {selected.display_name} 的所有会话并生成一次性密码。</p><button type="button" className="danger-button" disabled={pending} onClick={() => void reset()}>确认重置 {selected.display_name} 密码</button></div>}<button type="button" className="secondary-button" disabled={pending} onClick={() => setSelected(null)}>取消</button></section></fieldset> : null}
    {password ? <section ref={secretRegion} tabIndex={-1} className="one-time-secret" role="region" aria-live="polite" aria-labelledby="temporary-password-title"><h3 id="temporary-password-title">一次性临时密码</h3><p>请立即复制。关闭后无法从本页面恢复。</p><output>{password}</output><div className="inline-actions"><button type="button" className="secondary-button" onClick={() => void copy()}>复制临时密码</button><button type="button" className="primary-button compact-button" onClick={dismiss}>我已复制并关闭</button></div></section> : null}
  </div>;
}
