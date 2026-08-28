import { useEffect, useRef, useState } from "react";

import { ApiError } from "../api/client";
import {
  adminMutation,
  mutationMessage,
  type AdminTabProps,
} from "./common";
import {
  parseTemporaryPassword,
  type AdminUser,
} from "./types";

export function UsersTab(props: AdminTabProps) {
  const [selected, setSelected] = useState<AdminUser | null>(null);
  const [reason, setReason] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);
  const [password, setPassword] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ kind: "error" | "success"; text: string } | null>(null);
  const secretRegion = useRef<HTMLElement | null>(null);
  const restoreFocus = useRef<HTMLButtonElement | null>(null);

  useEffect(() => { if (password) secretRegion.current?.focus(); }, [password]);

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
    {notice ? <div className={`notice notice--${notice.kind}`} role={notice.kind === "error" ? "alert" : "status"}>{notice.text}</div> : null}
    <section className="admin-card">
      <h3>固定账号</h3>
      <div className="admin-table-wrap"><table className="admin-account-table"><thead><tr><th>姓名</th><th>角色</th><th>状态</th><th>操作</th></tr></thead><tbody>{props.users.map((user) => <tr className="admin-account-row" key={user.id}><th>{user.display_name}</th><td>{user.role === "admin" ? "管理员" : "审核员"}</td><td>{user.active ? "启用" : "停用"}</td><td>{user.role === "reviewer" ? <button type="button" className="danger-button" aria-label={`重置 ${user.display_name} 密码`} disabled={props.directoriesUnavailable} onClick={() => start(user)}>重置密码</button> : <span className="admin-account-action-placeholder">管理员账号只能通过服务器命令重置</span>}</td></tr>)}</tbody></table></div>
    </section>

    {selected ? <fieldset className="admin-fieldset" disabled={props.directoriesUnavailable}><section className="admin-card admin-confirm"><h3>重置 {selected.display_name} 密码</h3><label>密码重置原因<textarea aria-label="密码重置原因" value={reason} onChange={(event) => setReason(event.target.value)} /></label>{!confirming ? <button type="button" className="danger-button" onClick={continueReset}>继续重置 {selected.display_name}</button> : <div className="notice notice--error"><p>即将撤销 {selected.display_name} 的所有会话并生成一次性密码。</p><button type="button" className="danger-button" disabled={pending} onClick={() => void reset()}>确认重置 {selected.display_name} 密码</button></div>}<button type="button" className="secondary-button" disabled={pending} onClick={() => setSelected(null)}>取消</button></section></fieldset> : null}
    {password ? <section ref={secretRegion} tabIndex={-1} className="one-time-secret" role="region" aria-live="polite" aria-labelledby="temporary-password-title"><h3 id="temporary-password-title">一次性临时密码</h3><p>请立即复制。关闭后无法从本页面恢复。</p><output>{password}</output><div className="inline-actions"><button type="button" className="secondary-button" onClick={() => void copy()}>复制临时密码</button><button type="button" className="primary-button compact-button" onClick={dismiss}>我已复制并关闭</button></div></section> : null}
  </div>;
}
