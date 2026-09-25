import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cookies } from "next/headers";
import { GET } from "@/app/api/auth/me/route";

vi.mock("next/headers", () => ({ cookies: vi.fn() }));

const deleteCookie = vi.fn();

beforeEach(() => {
  process.env.VEXA_API_URL = "https://gateway.example";
  deleteCookie.mockClear();
  vi.mocked(cookies).mockResolvedValue({
    get: () => ({ value: "COOKIE-TOKEN" }),
    delete: deleteCookie,
  } as never);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("R10 auth route preserves cookie on outage", () => {
  it("deletes the cookie only when the gateway returns 401", async () => {
    const fetchMock = vi.fn(async () => new Response("{}", { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET();

    expect(response.status).toBe(401);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(deleteCookie).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each([429, 503])("maps gateway %s to 503 without deleting the cookie", async (status) => {
    const fetchMock = vi.fn(async () => new Response("{}", { status }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET();

    expect(response.status).toBe(503);
    expect(deleteCookie).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("maps malformed and invalid successful identities to 503", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response("not-json", { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ email: "missing-id@example.com" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    expect((await GET()).status).toBe(503);
    expect((await GET()).status).toBe(503);
    expect(deleteCookie).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("returns 504 after exactly ten seconds and aborts the gateway request", async () => {
    vi.useFakeTimers();
    let signal: AbortSignal | undefined;
    const fetchMock = vi.fn((_url: RequestInfo | URL, init?: RequestInit) => {
      signal = init?.signal ?? undefined;
      return new Promise<Response>(() => undefined);
    });
    vi.stubGlobal("fetch", fetchMock);

    const responsePromise = GET();
    await vi.advanceTimersByTimeAsync(9_999);
    expect(signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);

    expect((await responsePromise).status).toBe(504);
    expect(signal?.aborted).toBe(true);
    expect(deleteCookie).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("keeps the ten-second deadline active while reading a successful body", async () => {
    vi.useFakeTimers();
    const response = new Response(JSON.stringify({ user_id: 1, email: "late@example.com" }), { status: 200 });
    vi.spyOn(response, "json").mockReturnValue(new Promise<never>(() => undefined));
    vi.stubGlobal("fetch", vi.fn(async () => response));

    const responsePromise = GET();
    await vi.advanceTimersByTimeAsync(10_000);

    expect((await responsePromise).status).toBe(504);
    expect(deleteCookie).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("retains the healthy response shape", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      user_id: 7,
      email: "healthy@example.com",
      name: "Healthy",
    }), { status: 200 })));

    const response = await GET();

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      authenticated: true,
      user: { id: 7, email: "healthy@example.com", name: "Healthy" },
      token: "COOKIE-TOKEN",
    });
    expect(deleteCookie).not.toHaveBeenCalled();
  });
});
