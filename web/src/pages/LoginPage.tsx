import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { ApiError, request } from "../api/client";
import {
  type FixedName,
  FIXED_NAMES,
  parseLoginNames,
} from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { PillChoiceGroup } from "../components/PillChoiceGroup";
import type { MessageKey } from "../i18n/catalog";
import { useI18n } from "../i18n/I18nProvider";

export { FIXED_NAMES } from "../api/types";

export function LoginPage() {
  const { login, successMessageKey } = useAuth();
  const { locale, t, toggleLocale } = useI18n();
  const [availableNames, setAvailableNames] = useState<readonly FixedName[] | null>(null);
  const [namesError, setNamesError] = useState(false);
  const [selectedName, setSelectedName] = useState<FixedName | null>(null);
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
    setAvailableNames(null);
    try {
      const response = await request<unknown>("/auth/names", { signal: controller.signal });
      const verifiedNames = parseLoginNames(response);
      if (generation !== namesGeneration.current) return;
      setAvailableNames(verifiedNames);
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
    if (!selectedName || !password) {
      setErrorKey("loginMissingFields");
      return;
    }
    setPending(true);
    setErrorKey(null);
    try {
      await login({ name: selectedName, password });
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
        <p className="auth-intro">{t("loginIntro")}</p>

        {successMessageKey ? <p className="notice notice--success">{t(successMessageKey)}</p> : null}
        {namesError ? (
          <div className="notice notice--error" role="alert">
            <p>{t("namesLoadError")}</p>
            <button className="text-button" type="button" onClick={() => void loadNames()}>
              {t("retryNames")}
            </button>
          </div>
        ) : availableNames ? (
          <form onSubmit={handleSubmit} noValidate>
            <fieldset className="field-group" disabled={pending}>
              <legend>{t("chooseName")}</legend>
              <PillChoiceGroup
                label={t("chooseName")}
                options={availableNames}
                value={selectedName}
                onChange={setSelectedName}
                disabled={pending}
              />
            </fieldset>

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
