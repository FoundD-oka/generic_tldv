import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { cookies } from "next/headers";
import { GET } from "@/app/api/vexa/[...path]/route";

vi.mock("next/headers", () => ({
  cookies: vi.fn(async () => ({ get: () => undefined, delete: vi.fn() })),
}));

/**
 * 一覧 proxy は上流の失敗を隠さない。旧実装は /bots が落ちると
 * /bots/status(実行中コンテナのみ)へ落ちて 200 + 空配列を返し、
 * 「履歴が消えた」という沈黙障害を成功として表示していた。
 */
describe("GET /api/vexa/meetings のエラー伝播", () => {
  beforeEach(() => {
    process.env.VEXA_API_URL = "https://gateway.example";
    process.env.VEXA_API_KEY = "environment-fallback-key";
    vi.mocked(cookies).mockResolvedValue({
      get: () => undefined,
      delete: vi.fn(),
    } as never);
  });

  function listRequest() {
    return new NextRequest("https://dashboard.example/api/vexa/meetings", { method: "GET" });
  }

  function callList() {
    return GET(listRequest(), { params: Promise.resolve({ path: ["meetings"] }) });
  }

  it("上流 500 をそのまま返し、/bots/status へフォールバックしない", async () => {
    const fetchMock = vi.fn(async () => new Response(
      JSON.stringify({ detail: "database is down" }),
      { status: 500, headers: { "Content-Type": "application/json" } }
    ));
    vi.stubGlobal("fetch", fetchMock);

    const response = await callList();
    const body = await response.json();

    expect(response.status).toBe(500);
    expect(body.error).toBe("database is down");
    expect(body.upstream_status).toBe(500);
    expect(body.retryable).toBe(true);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const calledUrl = fetchMock.mock.calls[0][0] as string;
    expect(calledUrl.startsWith("https://gateway.example/bots?")).toBe(true);
    expect(calledUrl).not.toContain("/bots/status");
  });

  it("上流 401 は 401 のまま返し retryable=false", async () => {
    const fetchMock = vi.fn(async () => new Response(
      JSON.stringify({ detail: "Invalid API key" }),
      { status: 401, headers: { "Content-Type": "application/json" } }
    ));
    vi.stubGlobal("fetch", fetchMock);

    const response = await callList();
    const body = await response.json();

    expect(response.status).toBe(401);
    expect(body.error).toBe("Invalid API key");
    expect(body.retryable).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("タイムアウトは 504 + retryable", async () => {
    const abort = new Error("The operation was aborted");
    abort.name = "AbortError";
    const fetchMock = vi.fn(async () => { throw abort; });
    vi.stubGlobal("fetch", fetchMock);

    const response = await callList();
    const body = await response.json();

    expect(response.status).toBe(504);
    expect(body.error).toBe("Request timeout");
    expect(body.retryable).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("ネットワークエラーは 502 + retryable", async () => {
    const fetchMock = vi.fn(async () => { throw new TypeError("fetch failed"); });
    vi.stubGlobal("fetch", fetchMock);

    const response = await callList();
    const body = await response.json();

    expect(response.status).toBe(502);
    expect(body.error).toContain("Failed to connect to API");
    expect(body.retryable).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("上流 2xx はそのまま meetings と has_more を返す", async () => {
    const fetchMock = vi.fn(async () => new Response(
      JSON.stringify({ meetings: [{ id: 1 }], has_more: true }),
      { status: 200, headers: { "Content-Type": "application/json" } }
    ));
    vi.stubGlobal("fetch", fetchMock);

    const response = await callList();

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ meetings: [{ id: 1 }], has_more: true });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
