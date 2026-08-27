import { type ReactNode, useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes } from "react-router-dom";

import { useAuth } from "./auth/AuthProvider";
import { ChangePasswordPage } from "./pages/ChangePasswordPage";
import { LoginPage } from "./pages/LoginPage";
import { HistoryPage } from "./pages/HistoryPage";
import { ReviewPage } from "./pages/ReviewPage";
import { TeamProgressPage } from "./pages/TeamProgressPage";
import { AdminPage } from "./pages/AdminPage";
import { useI18n } from "./i18n/I18nProvider";

export const APP_PATHS = ["/", "/history", "/progress", "/admin"] as const;

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
    <Routes>
        <Route
          path="/"
          element={
            <AuthenticatedShell
              name={authenticatedUser.name}
              isAdmin={authenticatedUser.role === "admin"}
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
              isAdmin={authenticatedUser.role === "admin"}
              onChangePassword={() => setChangingPassword(true)}
              onLogout={auth.logout}
            >
              <HistoryPage
                csrfToken={authenticatedUser.csrf_token}
                reviewerId={authenticatedUser.id}
                retryBootstrap={auth.retryBootstrap}
              />
            </AuthenticatedShell>
          }
        />
        <Route
          path="/progress"
          element={
            <AuthenticatedShell
              name={authenticatedUser.name}
              isAdmin={authenticatedUser.role === "admin"}
              onChangePassword={() => setChangingPassword(true)}
              onLogout={auth.logout}
            >
              <TeamProgressPage retryBootstrap={auth.retryBootstrap} />
            </AuthenticatedShell>
          }
        />
        <Route
          path="/admin"
          element={
            authenticatedUser.name === "Mao" && authenticatedUser.role === "admin" ? <AuthenticatedShell
              name={authenticatedUser.name}
              isAdmin={authenticatedUser.role === "admin"}
              onChangePassword={() => setChangingPassword(true)}
              onLogout={auth.logout}
            >
              <AdminPage csrfToken={authenticatedUser.csrf_token} retryBootstrap={auth.retryBootstrap} />
            </AuthenticatedShell>
            : <Navigate to="/" replace />
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

interface AuthenticatedShellProps {
  name: string;
  isAdmin: boolean;
  onChangePassword: () => void;
  onLogout: () => Promise<void>;
  children: ReactNode;
}

function AuthenticatedShell({ name, isAdmin, onChangePassword, onLogout, children }: AuthenticatedShellProps) {
  const { locale, t, toggleLocale } = useI18n();
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
          <strong>{t("shellTitle")}</strong>
        </div>
        <nav className="shell-navigation" aria-label={t("shellTitle")}>
          <NavLink to="/" end>{t("navReview")}</NavLink>
          <NavLink to="/history">{t("navHistory")}</NavLink>
          <NavLink to="/progress">{t("navProgress")}</NavLink>
          {isAdmin ? <NavLink to="/admin">{t("navAdmin")}</NavLink> : null}
        </nav>
        <div className="user-actions">
          <span className="user-badge">{name}</span>
          <button className="secondary-button language-toggle" type="button" onClick={toggleLocale}>
            {locale === "zh" ? "English" : "中文"}
          </button>
          <button className="secondary-button" type="button" onClick={onChangePassword}>
            {t("changePassword")}
          </button>
          <button className="secondary-button" type="button" disabled={logoutPending} onClick={() => void handleLogout()}>
            {logoutPending ? t("loggingOut") : t("logout")}
          </button>
        </div>
      </header>
      {logoutError ? (
        <div className="notice notice--error shell-notice" role="alert">
          <span>{t("logoutError")}</span>
          <button className="text-button" type="button" onClick={() => void handleLogout()}>
            {t("retryLogout")}
          </button>
        </div>
      ) : null}
      {children}
    </div>
  );
}
