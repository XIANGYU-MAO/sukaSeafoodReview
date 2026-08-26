import { StrictMode } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { I18nProvider } from "../i18n/I18nProvider";
import { deferred, jsonResponse } from "../test/helpers";
import { ReviewPage } from "./ReviewPage";

const reviewerId = "8de1871b-677f-4ea8-8e11-1f4d49a88c86";
const candidate = {
  id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  species: {
    code: "SF001",
    name_zh: "测试鱼",
    name_en: "Test fish",
    scientific_name: "Piscis probatio",
  },
  source_dataset: "INATURALIST",
  source_record_id: "obs:1/photo:10",
  preview_url: "https://images.example.test/preview.jpg",
  original_url: "https://images.example.test/original.jpg",
  source_url: "https://source.example.test/record/1",
  creator: "Ada",
  license: "CC-BY-NC",
  license_url: "https://creativecommons.org/licenses/by-nc/4.0/",
  attribution: "Ada / iNaturalist",
  location: "South China Sea",
  observed_on: "2026-08-13",
  metadata: { source_observation_quality: "research" },
};

function decisionResponse(
  payload: { decision: string; rejection_reason: string | null; notes: string | null },
  candidateId = candidate.id,
) {
  return {
    id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    candidate_id: candidateId,
    reviewer_id: reviewerId,
    decision: payload.decision,
    rejection_reason: payload.rejection_reason,
    notes: payload.notes,
    whole_fish: payload.decision === "APPROVED" ? "YES" : "REVIEW",
    exact_species_verified: payload.decision === "APPROVED" ? "YES" : "REVIEW",
    is_current: true,
    version: 1,
  };
}

function renderPage(
  retryBootstrap = vi.fn(async () => undefined),
  strict = false,
) {
  const page = (
    <I18nProvider initialLocale="zh">
      <ReviewPage
        csrfToken="test-csrf-token"
        reviewerId={reviewerId}
        retryBootstrap={retryBootstrap}
      />
    </I18nProvider>
  );
  return {
    retryBootstrap,
    ...render(strict ? <StrictMode>{page}</StrictMode> : page),
  };
}

function uuidSequence(...values: string[]) {
  let index = 0;
  return vi.spyOn(globalThis.crypto, "randomUUID").mockImplementation(
    () => (values[index++] ?? values.at(-1)) as `${string}-${string}-${string}-${string}-${string}`,
  );
}

describe("ReviewPage current candidate", () => {
  it("requests current with CSRF, validates it, and renders localized safe metadata", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(candidate));
    vi.stubGlobal("fetch", fetchMock);
    renderPage();

    expect(await screen.findByRole("heading", { name: /测试鱼/ })).toHaveTextContent("Piscis probatio");
    expect(screen.getByText("iNaturalist")).toBeInTheDocument();
    expect(screen.getByText("Ada / iNaturalist")).toBeInTheDocument();
    expect(screen.getByText("South China Sea")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/sukaseafood/api/v1/reviews/current");
    expect(fetchMock.mock.calls[0][1]).toEqual(
      expect.objectContaining({ method: "POST", credentials: "include" }),
    );
    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get("X-CSRF-Token")).toBe(
      "test-csrf-token",
    );
    expect(fetchMock).not.toHaveBeenCalledWith(candidate.preview_url, expect.anything());
  });

  it("shows an honest empty-pool state for 204", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
    renderPage();

    expect(await screen.findByText("暂时没有待审核图片。稍后重试即可。")).toHaveAttribute("role", "status");
    expect(screen.getByText("本次会话已完成 0 张")).toBeInTheDocument();
  });

  it.each([
    ["network failure", () => Promise.reject(new TypeError("offline"))],
    ["server failure", () => Promise.resolve(jsonResponse({}, 503))],
    [
      "non-JSON success",
      () => Promise.resolve(new Response("<html>not candidate JSON</html>", {
        status: 200,
        headers: { "Content-Type": "text/html" },
      })),
    ],
    ["malformed success", () => Promise.resolve(jsonResponse({ preview_url: "https://x.test/a.jpg" }))],
  ])("makes a %s finite and retryable", async (_label, firstResponse) => {
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(firstResponse)
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("无法载入当前图片");
    await user.click(screen.getByRole("button", { name: "重试载入" }));
    expect(await screen.findByText("暂时没有待审核图片。稍后重试即可。")).toHaveAttribute("role", "status");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each([401, 403])("delegates current status %s to auth bootstrap", async (status) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({}, status)));
    const retryBootstrap = vi.fn(async () => undefined);
    renderPage(retryBootstrap);

    await waitFor(() => expect(retryBootstrap).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("button", { name: "重试载入" })).not.toBeInTheDocument();
  });
});

describe("ReviewPage request ownership", () => {
  it("aborts StrictMode replay and ignores its stale candidate", async () => {
    const stale = deferred<Response>();
    const active = deferred<Response>();
    const signals: AbortSignal[] = [];
    let currentCalls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
        signals.push(init?.signal as AbortSignal);
        currentCalls += 1;
        return currentCalls === 1 ? stale.promise : active.promise;
      }),
    );
    renderPage(undefined, true);
    await waitFor(() => expect(currentCalls).toBe(2));

    await act(async () => active.resolve(jsonResponse(candidate)));
    expect(await screen.findByText("Piscis probatio")).toBeInTheDocument();
    await act(async () =>
      stale.resolve(
        jsonResponse({
          ...candidate,
          id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
          species: { ...candidate.species, scientific_name: "Stale fish" },
        }),
      ),
    );

    expect(signals[0]).toBeInstanceOf(AbortSignal);
    expect(signals[0].aborted).toBe(true);
    expect(screen.queryByText("Stale fish")).not.toBeInTheDocument();
    expect(screen.getByText("Piscis probatio")).toBeInTheDocument();
  });

  it("does not let a pre-success current response resurrect an older item", async () => {
    const stale = deferred<Response>();
    let currentCalls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/reviews/current")) {
          currentCalls += 1;
          if (currentCalls === 1) return stale.promise;
          if (currentCalls === 2) return Promise.resolve(jsonResponse(candidate));
          return Promise.resolve(
            jsonResponse({
              ...candidate,
              id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
              species: { ...candidate.species, scientific_name: "Next fish" },
            }),
          );
        }
        const submitted = JSON.parse(String(init?.body ?? "{}"));
        return Promise.resolve(jsonResponse(decisionResponse(submitted), 201));
      }),
    );
    uuidSequence("10000000-0000-4000-8000-000000000001");
    renderPage(undefined, true);
    expect(await screen.findByText("Piscis probatio")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保留 (K)" }));
    expect(await screen.findByText("Next fish")).toBeInTheDocument();

    await act(async () =>
      stale.resolve(
        jsonResponse({
          ...candidate,
          id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
          species: { ...candidate.species, scientific_name: "Resurrected fish" },
        }),
      ),
    );
    expect(screen.queryByText("Resurrected fish")).not.toBeInTheDocument();
    expect(screen.getByText("Next fish")).toBeInTheDocument();
  });
});

describe("ReviewPage immediate decision state machine", () => {
  it("sends one CSRF/idempotent decision, blocks duplicates, and advances only after validated success", async () => {
    const saved = deferred<Response>();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(candidate))
      .mockReturnValueOnce(saved.promise)
      .mockResolvedValueOnce(
        jsonResponse({
          ...candidate,
          id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
          species: { ...candidate.species, scientific_name: "Next fish" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    uuidSequence("10000000-0000-4000-8000-000000000001");
    renderPage();
    await screen.findByText("Piscis probatio");

    const keep = screen.getByRole("button", { name: "保留 (K)" });
    fireEvent.click(keep);
    fireEvent.click(keep);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(screen.getByText("Piscis probatio")).toBeInTheDocument();
    expect(screen.getByText("正在保存…")).toHaveAttribute("role", "status");
    expect(screen.getByRole("button", { name: "保留 (K)" })).toBeDisabled();
    const decisionCall = fetchMock.mock.calls[1];
    expect(decisionCall[0]).toBe(
      `/sukaseafood/api/v1/reviews/${candidate.id}/decision`,
    );
    expect(JSON.parse(String(decisionCall[1]?.body))).toEqual({
      decision: "APPROVED",
      rejection_reason: null,
      notes: null,
    });
    const headers = new Headers(decisionCall[1]?.headers);
    expect(headers.get("X-CSRF-Token")).toBe("test-csrf-token");
    expect(headers.get("Idempotency-Key")).toBe("10000000-0000-4000-8000-000000000001");

    await act(async () =>
      saved.resolve(
        jsonResponse(
          decisionResponse({ decision: "APPROVED", rejection_reason: null, notes: null }),
          201,
        ),
      ),
    );
    expect(await screen.findByText("Next fish")).toBeInTheDocument();
    expect(screen.getByText("本次会话已完成 1 张")).toBeInTheDocument();
  });

  it("retries network and 5xx ambiguity with the exact key and payload", async () => {
    const approved = { decision: "APPROVED", rejection_reason: null, notes: null };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(candidate))
      .mockRejectedValueOnce(new TypeError("offline"))
      .mockResolvedValueOnce(jsonResponse({}, 503))
      .mockResolvedValueOnce(jsonResponse(decisionResponse(approved), 201))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    uuidSequence("10000000-0000-4000-8000-000000000001");
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Piscis probatio");

    await user.click(screen.getByRole("button", { name: "保留 (K)" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("保存结果无法确认");
    await user.click(screen.getByRole("button", { name: "重试保存" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("保存结果无法确认");
    await user.click(screen.getByRole("button", { name: "重试保存" }));
    expect(await screen.findByText("暂时没有待审核图片。稍后重试即可。")).toHaveAttribute("role", "status");

    const decisionCalls = fetchMock.mock.calls.filter(([input]) =>
      String(input).endsWith("/decision"),
    );
    expect(decisionCalls).toHaveLength(3);
    expect(decisionCalls.map(([, init]) => new Headers(init?.headers).get("Idempotency-Key"))).toEqual([
      "10000000-0000-4000-8000-000000000001",
      "10000000-0000-4000-8000-000000000001",
      "10000000-0000-4000-8000-000000000001",
    ]);
    expect(new Set(decisionCalls.map(([, init]) => init?.body))).toHaveLength(1);
  });

  it.each([408, 425, 429])(
    "treats transient HTTP %s as ambiguous and retries the same payload with the exact key",
    async (status) => {
      const approved = { decision: "APPROVED", rejection_reason: null, notes: null };
      const fetchMock = vi
        .fn()
        .mockResolvedValueOnce(jsonResponse(candidate))
        .mockResolvedValueOnce(jsonResponse({}, status))
        .mockResolvedValueOnce(jsonResponse(decisionResponse(approved), 201))
        .mockResolvedValueOnce(new Response(null, { status: 204 }));
      vi.stubGlobal("fetch", fetchMock);
      const uuidSpy = uuidSequence(
        "10000000-0000-4000-8000-000000000001",
        "10000000-0000-4000-8000-000000000002",
      );
      const user = userEvent.setup();
      renderPage();
      await screen.findByText("Piscis probatio");

      await user.click(screen.getByRole("button", { name: "保留 (K)" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("保存结果无法确认");
      await user.click(screen.getByRole("button", { name: "重试保存" }));
      await screen.findByText("暂时没有待审核图片。稍后重试即可。");

      const decisionCalls = fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/decision"));
      expect(decisionCalls.map(([, init]) => new Headers(init?.headers).get("Idempotency-Key"))).toEqual([
        "10000000-0000-4000-8000-000000000001",
        "10000000-0000-4000-8000-000000000001",
      ]);
      expect(new Set(decisionCalls.map(([, init]) => init?.body))).toHaveLength(1);
      expect(uuidSpy).toHaveBeenCalledTimes(1);
    },
  );

  it.each([
    ["保留 (K)", "APPROVED"],
    ["不确定 (U)", "UNSURE"],
  ] as const)(
    "clears a stale rejection draft before a failed %s decision becomes selected",
    async (buttonName, decision) => {
      const fetchMock = vi
        .fn()
        .mockResolvedValueOnce(jsonResponse(candidate))
        .mockRejectedValueOnce(new TypeError("offline"));
      vi.stubGlobal("fetch", fetchMock);
      uuidSequence("10000000-0000-4000-8000-000000000001");
      const user = userEvent.setup();
      renderPage();
      await screen.findByText("Piscis probatio");

      await user.click(screen.getByRole("button", { name: "拒绝 (R)" }));
      await user.click(screen.getByRole("radio", { name: "鱼种错误" }));
      await user.click(screen.getByRole("button", { name: buttonName }));
      expect(await screen.findByRole("alert")).toHaveTextContent("保存结果无法确认");

      expect(screen.getAllByRole("button", { pressed: true })).toHaveLength(1);
      expect(screen.getByRole("button", { name: buttonName })).toHaveAttribute("aria-pressed", "true");
      expect(screen.getByRole("button", { name: "拒绝 (R)" })).toHaveAttribute("aria-pressed", "false");
      expect(screen.queryByRole("radiogroup", { name: "拒绝原因" })).not.toBeInTheDocument();
      const decisionCall = fetchMock.mock.calls.find(([input]) => String(input).endsWith("/decision"));
      expect(JSON.parse(String(decisionCall?.[1]?.body))).toEqual({
        decision,
        rejection_reason: null,
        notes: null,
      });
    },
  );

  it("dismisses an ambiguous notice without retiring the operation key or visible choice", async () => {
    const approved = { decision: "APPROVED", rejection_reason: null, notes: null };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(candidate))
      .mockRejectedValueOnce(new TypeError("offline"))
      .mockResolvedValueOnce(jsonResponse(decisionResponse(approved), 201))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    const uuidSpy = uuidSequence(
      "10000000-0000-4000-8000-000000000001",
      "10000000-0000-4000-8000-000000000002",
    );
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Piscis probatio");

    await user.click(screen.getByRole("button", { name: "保留 (K)" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("保存结果无法确认");
    expect(screen.getByRole("button", { name: "保留 (K)" })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "取消重试" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保留 (K)" })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "保留 (K)" }));
    await screen.findByText("暂时没有待审核图片。稍后重试即可。");

    const decisionCalls = fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/decision"));
    expect(decisionCalls.map(([, init]) => new Headers(init?.headers).get("Idempotency-Key"))).toEqual([
      "10000000-0000-4000-8000-000000000001",
      "10000000-0000-4000-8000-000000000001",
    ]);
    expect(uuidSpy).toHaveBeenCalledTimes(1);
  });

  it("keeps the ambiguous key when rejection UI opens and closes without a concrete change", async () => {
    const approved = { decision: "APPROVED", rejection_reason: null, notes: null };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(candidate))
      .mockRejectedValueOnce(new TypeError("offline"))
      .mockResolvedValueOnce(jsonResponse(decisionResponse(approved), 201))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    uuidSequence(
      "10000000-0000-4000-8000-000000000001",
      "10000000-0000-4000-8000-000000000002",
    );
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Piscis probatio");

    await user.click(screen.getByRole("button", { name: "保留 (K)" }));
    await screen.findByRole("alert");
    await user.click(screen.getByRole("button", { name: "拒绝 (R)" }));
    await user.click(screen.getByRole("button", { name: "取消拒绝" }));
    expect(screen.getByRole("button", { name: "保留 (K)" })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "保留 (K)" }));
    await screen.findByText("暂时没有待审核图片。稍后重试即可。");

    const decisionCalls = fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/decision"));
    expect(decisionCalls.map(([, init]) => new Headers(init?.headers).get("Idempotency-Key"))).toEqual([
      "10000000-0000-4000-8000-000000000001",
      "10000000-0000-4000-8000-000000000001",
    ]);
  });

  it("invalidates the failed key before retrying a changed rejection payload", async () => {
    const secondPayload = {
      decision: "REJECTED",
      rejection_reason: "TOO_OCCLUDED",
      notes: null,
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(candidate))
      .mockRejectedValueOnce(new TypeError("offline"))
      .mockResolvedValueOnce(jsonResponse(decisionResponse(secondPayload), 201))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    uuidSequence(
      "10000000-0000-4000-8000-000000000001",
      "10000000-0000-4000-8000-000000000002",
    );
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Piscis probatio");

    await user.click(screen.getByRole("button", { name: "拒绝 (R)" }));
    await user.click(screen.getByRole("radio", { name: "鱼种错误" }));
    await user.click(screen.getByRole("button", { name: "确认拒绝" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("保存结果无法确认");
    await user.click(screen.getByRole("radio", { name: "遮挡过多" }));
    expect(screen.queryByRole("button", { name: "重试保存" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认拒绝" }));

    const decisionCalls = fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/decision"));
    expect(decisionCalls.map(([, init]) => new Headers(init?.headers).get("Idempotency-Key"))).toEqual([
      "10000000-0000-4000-8000-000000000001",
      "10000000-0000-4000-8000-000000000002",
    ]);
  });

  it("submits and visibly retains the structured image-error shortcut after ambiguity", async () => {
    const rejected = {
      decision: "REJECTED",
      rejection_reason: "IMAGE_URL_UNAVAILABLE",
      notes: null,
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(candidate))
      .mockRejectedValueOnce(new TypeError("offline"));
    vi.stubGlobal("fetch", fetchMock);
    uuidSequence("10000000-0000-4000-8000-000000000001");
    const user = userEvent.setup();
    renderPage();
    const image = await screen.findByRole("img");
    fireEvent.error(image);
    await user.click(screen.getByRole("button", { name: "图片链接失效" }));

    const call = fetchMock.mock.calls.find(([input]) => String(input).endsWith("/decision"));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual(rejected);
    expect(await screen.findByRole("alert")).toHaveTextContent("保存结果无法确认");
    expect(screen.getByRole("button", { name: "图片链接失效" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "拒绝 (R)" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("radio", { name: "图片链接失效" })).toHaveAttribute("aria-checked", "true");
  });

  it("treats malformed decision success as ambiguous and never advances", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(candidate))
      .mockResolvedValueOnce(jsonResponse({}, 201));
    vi.stubGlobal("fetch", fetchMock);
    uuidSequence("10000000-0000-4000-8000-000000000001");
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Piscis probatio");
    await user.click(screen.getByRole("button", { name: "不确定 (U)" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("保存结果无法确认");
    expect(screen.getByRole("button", { name: "不确定 (U)" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "不确定 (U)" })).toHaveTextContent("✓");
    expect(screen.getByText("Piscis probatio")).toBeInTheDocument();
    expect(screen.getByText("本次会话已完成 0 张")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("retires a definitively rejected key but retries the preserved payload with a new key", async () => {
    const unsure = { decision: "UNSURE", rejection_reason: null, notes: null };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(candidate))
      .mockResolvedValueOnce(jsonResponse({}, 422))
      .mockResolvedValueOnce(jsonResponse(decisionResponse(unsure), 201))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    uuidSequence(
      "10000000-0000-4000-8000-000000000001",
      "10000000-0000-4000-8000-000000000002",
    );
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Piscis probatio");
    await user.click(screen.getByRole("button", { name: "不确定 (U)" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("保存请求被明确拒绝");
    await user.click(screen.getByRole("button", { name: "重试保存" }));
    expect(await screen.findByText("暂时没有待审核图片。稍后重试即可。")).toBeInTheDocument();

    const decisionCalls = fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/decision"));
    expect(decisionCalls.map(([, init]) => new Headers(init?.headers).get("Idempotency-Key"))).toEqual([
      "10000000-0000-4000-8000-000000000001",
      "10000000-0000-4000-8000-000000000002",
    ]);
    expect(new Set(decisionCalls.map(([, init]) => init?.body))).toHaveLength(1);
  });

  it("explains a 409 assignment conflict and refreshes current instead of replaying", async () => {
    const replacement = {
      ...candidate,
      id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
      species: { ...candidate.species, scientific_name: "Replacement fish" },
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(candidate))
      .mockResolvedValueOnce(jsonResponse({}, 409))
      .mockResolvedValueOnce(jsonResponse(replacement));
    vi.stubGlobal("fetch", fetchMock);
    uuidSequence("10000000-0000-4000-8000-000000000001");
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Piscis probatio");
    await user.click(screen.getByRole("button", { name: "保留 (K)" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("当前图片的分配已变化");
    expect(await screen.findByText("Replacement fish")).toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/decision"))).toHaveLength(1);
  });

  it.each([401, 403])("delegates decision status %s to auth bootstrap without blind replay", async (status) => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(candidate))
      .mockResolvedValueOnce(jsonResponse({}, status));
    vi.stubGlobal("fetch", fetchMock);
    uuidSequence("10000000-0000-4000-8000-000000000001");
    const retryBootstrap = vi.fn(async () => undefined);
    const user = userEvent.setup();
    renderPage(retryBootstrap);
    await screen.findByText("Piscis probatio");
    await user.click(screen.getByRole("button", { name: "保留 (K)" }));

    await waitFor(() => expect(retryBootstrap).toHaveBeenCalledTimes(1));
    expect(fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/decision"))).toHaveLength(1);
    expect(screen.getByText("Piscis probatio")).toBeInTheDocument();
  });
});
