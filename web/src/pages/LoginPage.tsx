import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { ApiError, request } from "../api/client";
import {
  type FixedName,
  FIXED_NAMES,
  parseLoginNames,
} from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { PillChoiceGroup } from "../components/PillChoiceGroup";

export { FIXED_NAMES } from "../api/types";

export function LoginPage() {
  const { login, successMessage } = useAuth();
  const [availableNames, setAvailableNames] = useState<readonly FixedName[] | null>(null);
  const [namesError, setNamesError] = useState(false);
  const [selectedName, setSelectedName] = useState<FixedName | null>(null);
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
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
      setError("请选择姓名并输入密码。");
      return;
    }
    setPending(true);
    setError(null);
    try {
      await login({ name: selectedName, password });
      setPassword("");
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 401) {
        setError("姓名或密码不正确。");
      } else if (failure instanceof ApiError && failure.status === 429) {
        setError("登录暂时不可用，请稍后再试。");
      } else {
        setError("服务暂时不可用，请重试。");
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="auth-layout">
      <section className="auth-card" aria-labelledby="login-title">
        <div className="brand-mark" aria-hidden="true">海</div>
        <p className="eyebrow">SukaSeafood · Collaborative Review</p>
        <h1 id="login-title">登录审核平台</h1>
        <p className="auth-intro">选择你的固定账号，继续协作审核。</p>

        {successMessage ? <p className="notice notice--success">{successMessage}</p> : null}
        {namesError ? (
          <div className="notice notice--error" role="alert">
            <p>无法载入成员名单</p>
            <button className="text-button" type="button" onClick={() => void loadNames()}>
              重试载入名单
            </button>
          </div>
        ) : availableNames ? (
          <form onSubmit={handleSubmit} noValidate>
            <fieldset className="field-group" disabled={pending}>
              <legend>选择姓名 <span lang="en">/ Choose your name</span></legend>
              <PillChoiceGroup
                label="选择姓名"
                options={availableNames}
                value={selectedName}
                onChange={setSelectedName}
                disabled={pending}
              />
            </fieldset>

            <label className="input-label" htmlFor="password">密码</label>
            <input
              id="password"
              className="text-input"
              type="password"
              autoComplete="current-password"
              value={password}
              disabled={pending}
              onChange={(event) => setPassword(event.target.value)}
            />
            {error ? <p className="notice notice--error" role="alert">{error}</p> : null}
            <button className="primary-button" type="submit" disabled={pending}>
              {pending ? "正在登录…" : "登录"}
            </button>
          </form>
        ) : (
          <p className="inline-loading" role="status">正在载入成员名单…</p>
        )}
      </section>
    </main>
  );
}
