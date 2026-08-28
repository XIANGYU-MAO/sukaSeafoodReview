import { type ReactNode, useEffect, useRef, useState } from "react";
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
  const showTeamProgress = authenticatedUser.team_progress_visible;

  return (
    <Routes>
      <Route
        path="/"
        element={
          <AuthenticatedShell
            name={authenticatedUser.name}
            isAdmin={authenticatedUser.role === "admin"}
            showTeamProgress={showTeamProgress}
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
            showTeamProgress={showTeamProgress}
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
        element={showTeamProgress ?
          <AuthenticatedShell
            name={authenticatedUser.name}
            isAdmin={authenticatedUser.role === "admin"}
            showTeamProgress={showTeamProgress}
            onChangePassword={() => setChangingPassword(true)}
            onLogout={auth.logout}
          >
            <TeamProgressPage retryBootstrap={auth.retryBootstrap} />
          </AuthenticatedShell>
          : <Navigate to="/" replace />}
      />
      <Route
        path="/admin"
        element={
          authenticatedUser.name === "Mao" && authenticatedUser.role === "admin" ? <AuthenticatedShell
            name={authenticatedUser.name}
            isAdmin={authenticatedUser.role === "admin"}
            showTeamProgress={showTeamProgress}
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
  showTeamProgress: boolean;
  onChangePassword: () => void;
  onLogout: () => Promise<void>;
  children: ReactNode;
}

function AuthenticatedShell({ name, isAdmin, showTeamProgress, onChangePassword, onLogout, children }: AuthenticatedShellProps) {
  const { locale, t, toggleLocale } = useI18n();
  const [logoutPending, setLogoutPending] = useState(false);
  const [logoutError, setLogoutError] = useState(false);
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const accountMenuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!accountMenuOpen) return;
    function closeOnOutsideClick(event: PointerEvent) {
      if (!accountMenuRef.current?.contains(event.target as Node)) setAccountMenuOpen(false);
    }
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setAccountMenuOpen(false);
    }
    document.addEventListener("pointerdown", closeOnOutsideClick);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsideClick);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [accountMenuOpen]);

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
          {showTeamProgress ? <NavLink to="/progress">{t("navProgress")}</NavLink> : null}
          {isAdmin ? <NavLink to="/admin">{t("navAdmin")}</NavLink> : null}
        </nav>
        <div className="user-actions">
          <div className="account-menu" ref={accountMenuRef}>
            <button
              className="user-badge account-menu__trigger"
              type="button"
              aria-label={`${name} ${t("accountMenu")}`}
              aria-haspopup="menu"
              aria-expanded={accountMenuOpen}
              aria-controls="account-menu-popover"
              onClick={() => setAccountMenuOpen((open) => !open)}
            >
              <span>{name}</span>
              <svg className="account-menu__chevron" viewBox="0 0 16 16" aria-hidden="true">
                <path d="m4 6 4 4 4-4" />
              </svg>
            </button>
            {accountMenuOpen ? <div id="account-menu-popover" className="account-menu__popover" role="menu" aria-label={name}>
              <button type="button" role="menuitem" onClick={() => { setAccountMenuOpen(false); onChangePassword(); }}>
                {t("changePassword")}
              </button>
              <button type="button" role="menuitem" disabled={logoutPending} onClick={() => { setAccountMenuOpen(false); void handleLogout(); }}>
                {logoutPending ? t("loggingOut") : t("logout")}
              </button>
            </div> : null}
          </div>
          <button
            className="secondary-button language-toggle language-icon-button"
            type="button"
            aria-label={t("switchLanguage")}
            title={t("switchLanguage")}
            onClick={toggleLocale}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <circle cx="12" cy="12" r="9" />
              <path d="M3 12h18M12 3c2.4 2.5 3.6 5.5 3.6 9s-1.2 6.5-3.6 9c-2.4-2.5-3.6-5.5-3.6-9S9.6 5.5 12 3Z" />
            </svg>
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
