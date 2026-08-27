import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { ApiError, request } from "../api/client";
import {
  type AuthState,
  type ChangePasswordPayload,
  type LoginPayload,
  parseAuthState,
} from "../api/types";
import type { MessageKey } from "../i18n/catalog";

type AuthStatus = "booting" | "anonymous" | "authenticated" | "service-error";

interface AuthContextValue {
  status: AuthStatus;
  user: AuthState | null;
  successMessageKey: MessageKey | null;
  retryBootstrap: () => Promise<void>;
  login: (payload: LoginPayload) => Promise<void>;
  logout: () => Promise<void>;
  changePassword: (payload: ChangePasswordPayload) => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("booting");
  const [user, setUser] = useState<AuthState | null>(null);
  const [successMessageKey, setSuccessMessageKey] = useState<MessageKey | null>(null);
  const authGeneration = useRef(0);
  const bootstrapController = useRef<AbortController | null>(null);

  const invalidateBootstrap = useCallback(() => {
    authGeneration.current += 1;
    bootstrapController.current?.abort();
    bootstrapController.current = null;
    return authGeneration.current;
  }, []);

  const bootstrap = useCallback(async () => {
    const generation = invalidateBootstrap();
    const controller = new AbortController();
    bootstrapController.current = controller;
    setStatus("booting");
    try {
      const response = await request<unknown>("/auth/me", { signal: controller.signal });
      const restored = parseAuthState(response);
      if (generation !== authGeneration.current) return;
      setUser(restored);
      setStatus("authenticated");
    } catch (error) {
      if (controller.signal.aborted || generation !== authGeneration.current) return;
      setUser(null);
      if (error instanceof ApiError && error.status === 401) {
        setStatus("anonymous");
      } else {
        setStatus("service-error");
      }
    } finally {
      if (bootstrapController.current === controller) {
        bootstrapController.current = null;
      }
    }
  }, [invalidateBootstrap]);

  useEffect(() => {
    void bootstrap();
    return () => {
      invalidateBootstrap();
    };
  }, [bootstrap, invalidateBootstrap]);

  const login = useCallback(async (payload: LoginPayload) => {
    const generation = invalidateBootstrap();
    const response = await request<unknown>("/auth/login", {
      method: "POST",
      body: payload,
    });
    const authenticated = parseAuthState(response);
    if (generation !== authGeneration.current) return;
    setSuccessMessageKey(null);
    setUser(authenticated);
    setStatus("authenticated");
  }, [invalidateBootstrap]);

  const logout = useCallback(async () => {
    if (!user) return;
    const generation = invalidateBootstrap();
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
    if (generation !== authGeneration.current) return;
    setUser(null);
    setSuccessMessageKey(null);
    setStatus("anonymous");
  }, [invalidateBootstrap, user]);

  const changePassword = useCallback(
    async (payload: ChangePasswordPayload) => {
      if (!user) return;
      const generation = invalidateBootstrap();
      await request("/auth/change-password", {
        method: "POST",
        body: payload,
        csrfToken: user.csrf_token,
      });
      if (generation !== authGeneration.current) return;
      setUser(null);
      setSuccessMessageKey("passwordChangedLoginAgain");
      setStatus("anonymous");
    },
    [invalidateBootstrap, user],
  );

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      user,
      successMessageKey,
      retryBootstrap: bootstrap,
      login,
      logout,
      changePassword,
    }),
    [bootstrap, changePassword, login, logout, status, successMessageKey, user],
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
