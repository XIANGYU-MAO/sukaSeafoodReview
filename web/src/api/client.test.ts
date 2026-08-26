import { describe, expect, it, vi } from "vitest";

import { ApiError, request } from "./client";

describe("API client", () => {
  it("sends JSON mutations to the same-origin API with cookies and CSRF", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await request("/history/123", {
      method: "PATCH",
      body: { decision: "UNSURE" },
      csrfToken: "csrf-value",
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "/sukaseafood/api/v1/history/123",
      expect.objectContaining({
        method: "PATCH",
        credentials: "include",
        body: JSON.stringify({ decision: "UNSURE" }),
      }),
    );
    const headers = new Headers(fetchMock.mock.calls[0][1]?.headers);
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.get("X-CSRF-Token")).toBe("csrf-value");
  });

  it("returns no body for a successful 204 response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));

    await expect(request("/auth/logout", { method: "POST", csrfToken: "csrf" })).resolves.toBeUndefined();
  });

  it("passes an AbortSignal to fetch", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await request<{ ok: boolean }>("/auth/me", { signal: controller.signal });

    expect(fetchMock).toHaveBeenCalledWith(
      "/sukaseafood/api/v1/auth/me",
      expect.objectContaining({ signal: controller.signal }),
    );
  });

  it.each([
    ["text/html", "<html>upstream secret</html>"],
    ["application/json", "{not-valid-json"],
  ])("rejects a successful typed response with invalid %s content safely", async (contentType, body) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(body, {
          status: 200,
          headers: { "Content-Type": contentType },
        }),
      ),
    );

    let failure: unknown;
    try {
      await request<{ ok: boolean }>("/auth/me");
    } catch (error) {
      failure = error;
    }

    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).status).toBe(200);
    expect((failure as ApiError).detail).not.toContain("upstream secret");
    expect((failure as ApiError).detail).not.toContain("not-valid-json");
  });

  it("preserves stable JSON details without exposing non-JSON server bodies", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn()
        .mockResolvedValueOnce(jsonResponse({ detail: "Current password is invalid" }, 400))
        .mockResolvedValueOnce(
          new Response("<html>proxy secret traceback</html>", {
            status: 502,
            headers: { "Content-Type": "text/html" },
          }),
        ),
    );

    await expect(request("/auth/change-password")).rejects.toMatchObject({
      status: 400,
      detail: "Current password is invalid",
    });

    let failure: unknown;
    try {
      await request("/auth/me");
    } catch (error) {
      failure = error;
    }
    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).status).toBe(502);
    expect((failure as ApiError).detail).not.toContain("proxy secret");
    expect((failure as ApiError).detail).not.toContain("html");
  });
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
