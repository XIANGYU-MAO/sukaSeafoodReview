export const WEB_BASE = "/sukaseafood/review/";
export const API_BASE = "/sukaseafood/api/v1";

const SAFE_FALLBACK = "请求失败，请稍后重试。";
const SAFE_PROTOCOL_FAILURE = "服务返回了无效响应，请重试。";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly body: unknown;

  constructor(status: number, detail = SAFE_FALLBACK, body?: unknown) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.body = body;
  }
}

export interface ApiRequestOptions extends Omit<RequestInit, "body" | "headers"> {
  body?: unknown;
  csrfToken?: string;
  headers?: HeadersInit;
}

export async function request<T = void>(
  path: string,
  options: ApiRequestOptions = {},
): Promise<T> {
  const { body, csrfToken, headers: suppliedHeaders, ...requestInit } = options;
  const headers = new Headers(suppliedHeaders);
  if (body !== undefined) {
    headers.set("Content-Type", "application/json");
  }
  if (csrfToken) {
    headers.set("X-CSRF-Token", csrfToken);
  }

  const response = await fetch(`${API_BASE}${normalizePath(path)}`, {
    ...requestInit,
    credentials: "include",
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (!response.ok) {
    const error = await safeErrorPayload(response);
    throw new ApiError(response.status, error.detail, error.body);
  }
  if (response.status === 204) {
    return undefined as T;
  }

  const contentType = response.headers.get("Content-Type") ?? "";
  if (!contentType.toLowerCase().includes("application/json")) {
    throw new ApiError(response.status, SAFE_PROTOCOL_FAILURE);
  }
  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError(response.status, SAFE_PROTOCOL_FAILURE);
  }
}

function normalizePath(path: string): string {
  return path.startsWith("/") ? path : `/${path}`;
}

async function safeErrorPayload(response: Response): Promise<{ detail: string; body?: unknown }> {
  const contentType = response.headers.get("Content-Type") ?? "";
  if (!contentType.toLowerCase().includes("application/json")) {
    return { detail: SAFE_FALLBACK };
  }
  try {
    const body: unknown = await response.json();
    if (
      typeof body === "object" &&
      body !== null &&
      "detail" in body &&
      typeof body.detail === "string" &&
      body.detail.trim()
    ) {
      return { detail: body.detail, body };
    }
    return { detail: SAFE_FALLBACK, body };
  } catch {
    // Malformed JSON receives the same opaque fallback as HTML/plain text.
  }
  return { detail: SAFE_FALLBACK };
}
