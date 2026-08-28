import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { ApiError, request } from "../api/client";
import {
  type FixedName,
  FIXED_NAMES,
  parseLoginOptions,
  type LoginOptions,
} from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { PillChoiceGroup } from "../components/PillChoiceGroup";
import type { MessageKey } from "../i18n/catalog";
import { useI18n } from "../i18n/I18nProvider";

export { FIXED_NAMES } from "../api/types";

export function LoginPage() {
  const { login, successMessageKey } = useAuth();
  const { locale, t, toggleLocale } = useI18n();
  const [loginOptions, setLoginOptions] = useState<LoginOptions | null>(null);
  const [namesError, setNamesError] = useState(false);
  const [selectedName, setSelectedName] = useState<FixedName | null>(null);
  const [manualName, setManualName] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [errorKey, setErrorKey] = useState<MessageKey | null>(null);
  const namesGeneration = useRef(0);
  const namesController = useRef<AbortController | null>(null);

  const loadNames = useCallback(async () => {
    namesGeneration.current += 1;
    const generation = namesGeneration.current;
    namesController.current?.abort();
    const controller = new AbortController();
    namesController.current = controller;
    setNamesError(false);
    setLoginOptions(null);
    try {
      const response = await request<unknown>("/auth/names", { signal: controller.signal });
      const verifiedOptions = parseLoginOptions(response);
      if (generation !== namesGeneration.current) return;
      setLoginOptions(verifiedOptions);
      setSelectedName(null);
      setManualName("");
    } catch {
      if (controller.signal.aborted || generation !== namesGeneration.current) return;
      setNamesError(true);
    }
  }, []);

  useEffect(() => {
    void loadNames();
    return () => {
      namesGeneration.current += 1;
      namesController.current?.abort();
    };
  }, [loadNames]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (pending) return;
    const submittedName = loginOptions?.login_name_mode === "manual" ? manualName.trim() : selectedName;
    if (!submittedName || !password) {
      setErrorKey("loginMissingFields");
      return;
    }
    setPending(true);
    setErrorKey(null);
    try {
      await login({ name: submittedName, password });
      setPassword("");
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 401) {
        setErrorKey("loginUnauthorized");
      } else if (failure instanceof ApiError && failure.status === 429) {
        setErrorKey("loginRateLimited");
      } else {
        setErrorKey("loginServiceUnavailable");
      }
    } finally {
      setPending(false);
    }
  }

  const alphabeticalNames = loginOptions?.login_name_mode === "choices"
    ? [...loginOptions.names].sort((left, right) => left.localeCompare(right, "en"))
    : [];

  return (
    <main className="auth-layout">
      <section className="auth-card" aria-labelledby="login-title">
        <div className="auth-card-toolbar">
          <button className="secondary-button language-toggle" type="button" onClick={toggleLocale}>
            {locale === "zh" ? "English" : "中文"}
          </button>
        </div>
        <div className="brand-mark" aria-hidden="true">海</div>
        <p className="eyebrow">{t("loginEyebrow")}</p>
        <h1 id="login-title">{t("loginTitle")}</h1>
        {loginOptions?.login_name_mode === "choices" ? <p className="auth-intro">{t("loginIntro")}</p> : null}

        {successMessageKey ? <p className="notice notice--success">{t(successMessageKey)}</p> : null}
        {namesError ? (
          <div className="notice notice--error" role="alert">
            <p>{t("namesLoadError")}</p>
            <button className="text-button" type="button" onClick={() => void loadNames()}>
              {t("retryNames")}
            </button>
          </div>
        ) : loginOptions ? (
          <form onSubmit={handleSubmit} noValidate>
            {loginOptions.login_name_mode === "choices" ? <fieldset className="field-group" disabled={pending}>
              <legend>{t("chooseName")}</legend>
              <PillChoiceGroup
                label={t("chooseName")}
                options={alphabeticalNames}
                value={selectedName}
                onChange={setSelectedName}
                disabled={pending}
              />
            </fieldset> : <>
              <label className="input-label" htmlFor="login-name">{t("name")}</label>
              <input
                id="login-name"
                className="text-input"
                type="text"
                autoComplete="username"
                value={manualName}
                disabled={pending}
                onChange={(event) => setManualName(event.target.value)}
              />
            </>}

            <label className="input-label" htmlFor="password">{t("password")}</label>
            <input
              id="password"
              className="text-input"
              type="password"
              autoComplete="current-password"
              value={password}
              disabled={pending}
              onChange={(event) => setPassword(event.target.value)}
            />
            {errorKey ? <p className="notice notice--error" role="alert">{t(errorKey)}</p> : null}
            <button className="primary-button" type="submit" disabled={pending}>
              {pending ? t("loggingIn") : t("login")}
            </button>
          </form>
        ) : (
          <p className="inline-loading" role="status">{t("loadingNames")}</p>
        )}
      </section>
    </main>
  );
}
