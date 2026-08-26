import { type FormEvent, useState } from "react";

import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthProvider";

interface ChangePasswordPageProps {
  forced: boolean;
  onCancel?: () => void;
}

export function ChangePasswordPage({ forced, onCancel }: ChangePasswordPageProps) {
  const { changePassword } = useAuth();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (pending) return;
    if (!currentPassword) {
      setError("请输入当前密码。");
      return;
    }
    // Array.from counts Unicode code points, matching Python/Pydantic's length
    // contract; JavaScript's UTF-16 .length would count astral characters twice.
    const newPasswordLength = Array.from(newPassword).length;
    if (newPasswordLength < 12) {
      setError("新密码至少需要 12 个字符。");
      return;
    }
    if (newPasswordLength > 128) {
      setError("新密码不能超过 128 个字符。");
      return;
    }
    if (newPassword !== confirmation) {
      setError("两次输入的新密码不一致。");
      return;
    }

    setPending(true);
    setError(null);
    try {
      await changePassword({
        current_password: currentPassword,
        new_password: newPassword,
      });
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 400) {
        setError(failure.detail);
      } else if (failure instanceof ApiError && failure.status === 401) {
        setError("会话已失效，请刷新页面后重试。");
      } else {
        setError("修改密码失败，请重试。");
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="auth-layout">
      <section className="auth-card" aria-labelledby="change-password-title">
        <p className="eyebrow">账号安全 · Account security</p>
        <h1 id="change-password-title">{forced ? "首次登录，请修改密码" : "修改密码"}</h1>
        <p className="auth-intro">
          {forced ? "完成修改后才能进入审核工作区。" : "修改后所有会话都会退出，请使用新密码重新登录。"}
        </p>
        <form onSubmit={handleSubmit} noValidate>
          <PasswordField
            id="current-password"
            label="当前密码"
            value={currentPassword}
            onChange={setCurrentPassword}
            autoComplete="current-password"
            disabled={pending}
          />
          <PasswordField
            id="new-password"
            label="新密码"
            value={newPassword}
            onChange={setNewPassword}
            autoComplete="new-password"
            disabled={pending}
          />
          <p className="field-hint">12–128 个字符；服务器会执行最终校验。</p>
          <PasswordField
            id="confirm-password"
            label="确认新密码"
            value={confirmation}
            onChange={setConfirmation}
            autoComplete="new-password"
            disabled={pending}
          />
          {error ? <p className="notice notice--error" role="alert">{error}</p> : null}
          <div className="button-row">
            <button className="primary-button" type="submit" disabled={pending}>
              {pending ? "正在修改…" : "修改密码"}
            </button>
            {!forced && onCancel ? (
              <button className="secondary-button" type="button" disabled={pending} onClick={onCancel}>
                返回审核工作区
              </button>
            ) : null}
          </div>
        </form>
      </section>
    </main>
  );
}

interface PasswordFieldProps {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  autoComplete: string;
  disabled: boolean;
}

function PasswordField({ id, label, value, onChange, autoComplete, disabled }: PasswordFieldProps) {
  return (
    <div className="input-block">
      <label className="input-label" htmlFor={id}>{label}</label>
      <input
        className="text-input"
        id={id}
        type="password"
        value={value}
        autoComplete={autoComplete}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  );
}
