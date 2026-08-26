import { StrictMode, type ReactNode } from "react";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { AuthProvider } from "../auth/AuthProvider";

export const authState = {
  id: "8de1871b-677f-4ea8-8e11-1f4d49a88c86",
  name: "Hassan",
  role: "reviewer",
  must_change_password: false,
  csrf_token: "test-csrf-token",
} as const;

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export function renderWithAuth(ui: ReactNode, initialEntry = "/") {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <AuthProvider>{ui}</AuthProvider>
    </MemoryRouter>,
  );
}

export function renderWithStrictAuth(ui: ReactNode, initialEntry = "/") {
  return render(
    <StrictMode>
      <MemoryRouter initialEntries={[initialEntry]}>
        <AuthProvider>{ui}</AuthProvider>
      </MemoryRouter>
    </StrictMode>,
  );
}

export function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}
