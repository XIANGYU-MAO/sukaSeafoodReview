import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import { ApiError, request } from "../api/client";
import type { AuthState, ChangePasswordPayload, LoginPayload } from "../api/types";

type AuthStatus = "booting" | "anonymous" | "authenticated" | "service-error";

interface AuthContextValue {
  status: AuthStatus;
  user: AuthState | null;
  successMessage: string | null;
  retryBootstrap: () => Promise<void>;
  login: (payload: LoginPayload) => Promise<void>;
  logout: () => Promise<void>;
  changePassword: (payload: ChangePasswordPayload) => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("booting");
  const [user, setUser] = useState<AuthState | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const bootstrap = useCallback(async () => {
    setStatus("booting");
    try {
      const restored = await request<AuthState>("/auth/me");
      setUser(restored);
      setStatus("authenticated");
    } catch (error) {
      setUser(null);
      if (error instanceof ApiError && error.status === 401) {
        setStatus("anonymous");
      } else {
        setStatus("service-error");
      }
    }
  }, []);

  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  const login = useCallback(async (payload: LoginPayload) => {
    const authenticated = await request<AuthState>("/auth/login", {
      method: "POST",
      body: payload,
    });
    setSuccessMessage(null);
    setUser(authenticated);
    setStatus("authenticated");
  }, []);

  const logout = useCallback(async () => {
    if (!user) return;
    try {
      await request("/auth/logout", {
        method: "POST",
        csrfToken: user.csrf_token,
      });
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 401)) {
        throw error;
      }
    }
    setUser(null);
    setSuccessMessage(null);
    setStatus("anonymous");
  }, [user]);

  const changePassword = useCallback(
    async (payload: ChangePasswordPayload) => {
      if (!user) return;
      await request("/auth/change-password", {
        method: "POST",
        body: payload,
        csrfToken: user.csrf_token,
      });
      setUser(null);
      setSuccessMessage("密码已修改，请重新登录。");
      setStatus("anonymous");
    },
    [user],
  );

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      user,
      successMessage,
      retryBootstrap: bootstrap,
      login,
      logout,
      changePassword,
    }),
    [bootstrap, changePassword, login, logout, status, successMessage, user],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used inside AuthProvider");
  }
  return context;
}
