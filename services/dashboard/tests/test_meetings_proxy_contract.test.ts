import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cookies } from "next/headers";
import { GET } from "@/app/api/vexa/[...path]/route";
import { vexaAPI, VexaAPIError } from "@/lib/api";
import { useMeetingsStore } from "@/stores/meetings-store";
import type { Meeting } from "@/types/vexa";

const deleteCookie = vi.fn();

vi.mock("next/headers", () => ({
  cookies: vi.fn(async () => ({
    get: () => ({ value: "user-token" }),
    delete: deleteCookie,
  })),
}));

const params = { params: Promise.resolve({ path: ["meetings"] }) };

function request(query = "") {
  return new NextRequest(`https://dashboard.example/api/vexa/meetings${query}`, {
    method: "GET",
  });
}

function meeting(id: string): Meeting {
  return {
    id,
    platform: "google_meet",
    platform_specific_id: `native-${id}`,
    status: id === "42" ? "completed" : "active",
    start_time: null,
    end_time: null,
    bot_container_id: null,
    data: {},
    created_at: "2026-09-05T00:00:00Z",
  };
}

describe("meetings proxy contract", () => {
  beforeEach(() => {
    process.env.VEXA_API_URL = "https://gateway.example";
    process.env.VEXA_API_KEY = "environment-key";
    deleteCookie.mockClear();
    vi.mocked(cookies).mockResolvedValue({
      get: () => ({ value: "user-token" }),
      delete: deleteCookie,
    } as never);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    delete process.env.VEXA_API_URL;
    delete process.env.VEXA_API_KEY;
  });

  it("R02 completed history survives successful proxy", async () => {
    const meetings = [meeting("42"), meeting("43")];
    const fetchMock = vi.fn(async () => new Response(
      JSON.stringify({ meetings, has_more: true }),
      { status: 200, headers: { "Content-Type": "application/json" } }
    ));
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET(
      request("?limit=50&offset=50&search=%E5%AE%9A%E4%BE%8B&status=completed&platform=google_meet"),
      params
    );

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({ meetings, has_more: true });
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "https://gateway.example/bots?limit=50&offset=50&search=%E5%AE%9A%E4%BE%8B&status=completed&platform=google_meet",
      expect.objectContaining({
        headers: { "X-API-Key": "user-token" },
        cache: "no-store",
        signal: expect.any(AbortSignal),
      })
    );
  });

  it.each([401, 402, 403, 429, 503])(
    "R02 failures never fall back to running bots (%s)",
    async (status) => {
      const fetchMock = vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) => new Response(
        JSON.stringify({ detail: `failure-${status}` }),
        {
          status,
          headers: {
            "Content-Type": "application/json",
            ...(status === 429 ? { "Retry-After": "17" } : {}),
          },
        }
      ));
      vi.stubGlobal("fetch", fetchMock);

      const response = await GET(request(), params);

      expect(response.status).toBe(status);
      await expect(response.json()).resolves.toEqual({ detail: `failure-${status}` });
      expect(response.headers.get("cache-control")).toBe("no-store");
      expect(response.headers.get("retry-after")).toBe(status === 429 ? "17" : null);
      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(String(fetchMock.mock.calls[0][0])).not.toContain("/bots/status");
      expect(deleteCookie).not.toHaveBeenCalled();
    }
  );

  it("R02 preserves non-JSON failure status without exposing the upstream body", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("internal gateway address", { status: 503 })));

    const response = await GET(request(), params);

    expect(response.status).toBe(503);
    await expect(response.json()).resolves.toEqual({
      error: "Meetings request failed",
      status: 503,
    });
    expect(response.headers.get("cache-control")).toBe("no-store");
  });

  it.each(["headers", "body"])("R02 headers and body hangs both time out (%s)", async (phase) => {
    vi.useFakeTimers();
    let signal!: AbortSignal;
    const fetchMock = vi.fn((_url: unknown, init: RequestInit) => {
      signal = init.signal as AbortSignal;
      if (phase === "headers") {
        return new Promise<Response>((_resolve, reject) => {
          signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
        });
      }
      const response = new Response("", {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
      response.json = vi.fn(() => new Promise((_resolve, reject) => {
        signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
      }));
      return Promise.resolve(response);
    });
    vi.stubGlobal("fetch", fetchMock);

    let settled = false;
    const responsePromise = GET(request(), params).then((response) => {
      settled = true;
      return response;
    });
    await vi.advanceTimersByTimeAsync(4999);
    expect(settled).toBe(false);
    await vi.advanceTimersByTimeAsync(1);

    const response = await responsePromise;
    expect(response.status).toBe(504);
    await expect(response.json()).resolves.toEqual({ error: "Request timeout" });
    expect(signal.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each([
    ["invalid JSON", () => new Response("not-json", { status: 200 })],
    ["non-array meetings", () => new Response(JSON.stringify({ meetings: {} }), {
      status: 200, headers: { "Content-Type": "application/json" },
    })],
    ["non-boolean has_more", () => new Response(JSON.stringify({ meetings: [], has_more: "yes" }), {
      status: 200, headers: { "Content-Type": "application/json" },
    })],
  ])("R02 malformed success is an error (%s)", async (_label, makeResponse) => {
    vi.stubGlobal("fetch", vi.fn(async () => makeResponse()));

    const response = await GET(request(), params);

    expect(response.status).toBe(502);
    await expect(response.json()).resolves.toEqual({ error: "Invalid meetings response" });
    expect(response.headers.get("cache-control")).toBe("no-store");
  });

  it("R02 malformed success keeps an empty meetings array valid", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ meetings: [] }),
      { status: 200, headers: { "Content-Type": "application/json" } }
    )));

    const response = await GET(request(), params);

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({ meetings: [], has_more: false });
  });

  it.each([503, 402])("R02 store retains rows after proxy failure (%s)", async (status) => {
    const existing = meeting("42");
    vi.spyOn(vexaAPI, "getMeetings").mockRejectedValue(
      new VexaAPIError(`failure-${status}`, status)
    );
    useMeetingsStore.setState({
      meetings: [existing],
      error: null,
      subscriptionRequired: false,
      isLoadingMeetings: false,
      _offset: 50,
      _filters: {},
    });

    await useMeetingsStore.getState().fetchMeetings();

    const state = useMeetingsStore.getState();
    expect(state.meetings).toEqual([existing]);
    expect(state.isLoadingMeetings).toBe(false);
    if (status === 402) {
      expect(state.subscriptionRequired).toBe(true);
      expect(state.error).toBeNull();
    } else {
      expect(state.subscriptionRequired).toBe(false);
      expect(state.error).toBe("failure-503");
    }
  });
});
