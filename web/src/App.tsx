import { type ReactNode, useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { useAuth } from "./auth/AuthProvider";
import { ChangePasswordPage } from "./pages/ChangePasswordPage";
import { LoginPage } from "./pages/LoginPage";
import { ReviewPage } from "./pages/ReviewPage";
import { I18nProvider, useI18n } from "./i18n/I18nProvider";

export const APP_PATHS = ["/", "/history", "/admin"] as const;

export function App() {
  const auth = useAuth();
  const [changingPassword, setChangingPassword] = useState(false);

  useEffect(() => {
    setChangingPassword(false);
  }, [auth.status, auth.user?.id, auth.user?.csrf_token]);

  if (auth.status === "booting") {
    return (
      <main className="system-state" aria-busy="true">
        <div className="spinner" aria-hidden="true" />
        <p role="status">正在恢复会话…</p>
      </main>
    );
  }
  if (auth.status === "service-error") {
    return (
      <main className="system-state">
        <div className="notice notice--error" role="alert">无法连接审核服务，请检查网络后重试。</div>
        <button className="primary-button compact-button" type="button" onClick={() => void auth.retryBootstrap()}>
          重试连接
        </button>
      </main>
    );
  }
  if (auth.status === "anonymous" || !auth.user) {
    return <LoginPage />;
  }
  if (auth.user.must_change_password) {
    return <ChangePasswordPage forced />;
  }
  if (changingPassword) {
    return <ChangePasswordPage forced={false} onCancel={() => setChangingPassword(false)} />;
  }
  const authenticatedUser = auth.user;

  return (
    <I18nProvider>
      <Routes>
        <Route
          path="/"
          element={
            <AuthenticatedShell
              name={authenticatedUser.name}
              onChangePassword={() => setChangingPassword(true)}
              onLogout={auth.logout}
            >
              <ReviewPage
                csrfToken={authenticatedUser.csrf_token}
                reviewerId={authenticatedUser.id}
                retryBootstrap={auth.retryBootstrap}
              />
            </AuthenticatedShell>
          }
        />
        <Route
          path="/history"
          element={
            <AuthenticatedShell
              name={authenticatedUser.name}
              onChangePassword={() => setChangingPassword(true)}
              onLogout={auth.logout}
            >
              <DeferredPage title="历史记录尚未接入" description="历史筛选和修改将在下一任务接入真实接口。" />
            </AuthenticatedShell>
          }
        />
        <Route
          path="/admin"
          element={
            <AuthenticatedShell
              name={authenticatedUser.name}
              onChangePassword={() => setChangingPassword(true)}
              onLogout={auth.logout}
            >
              <DeferredPage title="管理后台尚未接入" description="Mao 中文后台将在后续任务接入真实管理接口。" />
            </AuthenticatedShell>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </I18nProvider>
  );
}

interface AuthenticatedShellProps {
  name: string;
  onChangePassword: () => void;
  onLogout: () => Promise<void>;
  children: ReactNode;
}

function AuthenticatedShell({ name, onChangePassword, onLogout, children }: AuthenticatedShellProps) {
  const { locale, toggleLocale } = useI18n();
  const [logoutPending, setLogoutPending] = useState(false);
  const [logoutError, setLogoutError] = useState(false);

  async function handleLogout() {
    if (logoutPending) return;
    setLogoutPending(true);
    setLogoutError(false);
    try {
      await onLogout();
    } catch {
      setLogoutError(true);
    } finally {
      setLogoutPending(false);
    }
  }

  return (
    <div className="app-shell">
      <header className="shell-header">
        <div>
          <p className="eyebrow">SukaSeafood</p>
          <strong>协作审核</strong>
        </div>
        <div className="user-actions">
          <span className="user-badge">{name}</span>
          <button className="secondary-button language-toggle" type="button" onClick={toggleLocale}>
            {locale === "zh" ? "English" : "中文"}
          </button>
          <button className="secondary-button" type="button" onClick={onChangePassword}>
            修改密码 / Change password
          </button>
          <button className="secondary-button" type="button" disabled={logoutPending} onClick={() => void handleLogout()}>
            {logoutPending ? "正在退出…" : "退出登录"}
          </button>
        </div>
      </header>
      {logoutError ? (
        <div className="notice notice--error shell-notice" role="alert">
          <span>退出失败，请重试。</span>
          <button className="text-button" type="button" onClick={() => void handleLogout()}>
            重试退出
          </button>
        </div>
      ) : null}
      {children}
    </div>
  );
}

function DeferredPage({ title, description }: { title: string; description: string }) {
  return (
    <main className="placeholder-panel">
      <p className="eyebrow">Coming next</p>
      <h1>{title}</h1>
      <p>{description}</p>
    </main>
  );
}
