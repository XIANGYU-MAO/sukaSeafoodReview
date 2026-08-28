import { useEffect, useState } from "react";

import {
  QueryBoundary,
  adminMutation,
  mutationMessage,
  type AdminTabProps,
  useAdminQuery,
} from "./common";
import { parseSystemSettings, type SystemSettings } from "./types";

export function AccessSettingsTab(props: AdminTabProps) {
  const settingsQuery = useAdminQuery(
    "/admin/settings",
    parseSystemSettings,
    props.retryBootstrap,
  );
  const [settingsDraft, setSettingsDraft] = useState<SystemSettings | null>(null);
  const [settingsPending, setSettingsPending] = useState(false);
  const [settingsNotice, setSettingsNotice] = useState<{ kind: "error" | "success"; text: string } | null>(null);

  useEffect(() => {
    if (settingsQuery.data) setSettingsDraft(settingsQuery.data);
  }, [settingsQuery.data]);

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

  return <div className="admin-stack">
    <section className="admin-card admin-access-settings">
      <h3>访问设置</h3>
      <QueryBoundary query={settingsQuery}>
        {() => settingsDraft ? <fieldset className="admin-fieldset" disabled={settingsPending || settingsQuery.unavailable}>
          {settingsNotice ? <div className={`notice notice--${settingsNotice.kind}`} role={settingsNotice.kind === "error" ? "alert" : "status"}>{settingsNotice.text}</div> : null}
          <div className="admin-setting-grid">
            <div>
              <strong>登录页姓名方式</strong>
              <p>账号由“账号”页面维护；这里只控制登录页是否直接展示姓名。</p>
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
  </div>;
}
