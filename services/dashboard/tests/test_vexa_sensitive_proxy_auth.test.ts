import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { cookies } from "next/headers";
import {
  DELETE,
  GET,
  PATCH,
  POST,
  PUT,
  readBoundedVoiceprintProxyBody,
} from "@/app/api/vexa/[...path]/route";

vi.mock("next/headers", () => ({
  cookies: vi.fn(async () => ({ get: () => undefined, delete: vi.fn() })),
}));

describe("dashboard Vexa proxy auth", () => {
  beforeEach(() => {
    process.env.VEXA_API_URL = "https://gateway.example";
    process.env.VEXA_API_KEY = "environment-fallback-key";
    vi.mocked(cookies).mockResolvedValue({
      get: () => undefined,
      delete: vi.fn(),
    } as never);
  });

  it.each([
    ["GET", "/api/vexa/transcription-dictionary", ["transcription-dictionary"]],
    ["GET", "/api/vexa/meetings/42/transcription-status", ["meetings", "42", "transcription-status"]],
    ["POST", "/api/vexa/meetings/42/transcribe", ["meetings", "42", "transcribe"]],
    ["PATCH", "/api/vexa/meetings/42/transcripts/speakers", ["meetings", "42", "transcripts", "speakers"]],
    ["DELETE", "/api/vexa/meetings/42/speaker-suggestions/g%3Ademo%3As0", ["meetings", "42", "speaker-suggestions", "g:demo:s0"]],
    ["POST", "/api/vexa/voiceprints/enroll-from-cluster", ["voiceprints", "enroll-from-cluster"]],
    ["POST", "/api/vexa/voiceprints/preview-from-segments", ["voiceprints", "preview-from-segments"]],
    ["POST", "/api/vexa/voiceprints/enroll-from-segments", ["voiceprints", "enroll-from-segments"]],
    ["POST", "/api/vexa/voiceprints/enroll-from-audio", ["voiceprints", "enroll-from-audio"]],
    ["GET", "/api/vexa/speaker-profiles", ["speaker-profiles"]],
    ["DELETE", "/api/vexa/speaker-profiles/9", ["speaker-profiles", "9"]],
    ["GET", "/api/vexa/transcripts/google_meet/abc", ["transcripts", "google_meet", "abc"]],
    ["GET", "/api/vexa/recordings/123/master", ["recordings", "123", "master"]],
    ["POST", "/api/vexa/bots", ["bots"]],
    ["PUT", "/api/vexa/bots/google_meet/abc/config", ["bots", "google_meet", "abc", "config"]],
    ["DELETE", "/api/vexa/meetings/google_meet/abc", ["meetings", "google_meet", "abc"]],
  ])("returns 401 without calling upstream for %s %s", async (method, path, parts) => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const request = new NextRequest(`https://dashboard.example${path}`, { method });
    const handlers = { DELETE, GET, PATCH, POST, PUT } as const;
    const handler = handlers[method as keyof typeof handlers];
    const response = await handler(request, { params: Promise.resolve({ path: parts }) });
    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("uses the environment API key only for the unauthenticated meetings list", async () => {
    const fetchMock = vi.fn(async () => new Response(
      JSON.stringify({ meetings: [], has_more: false }),
      { status: 200, headers: { "Content-Type": "application/json" } }
    ));
    vi.stubGlobal("fetch", fetchMock);
    const request = new NextRequest("https://dashboard.example/api/vexa/meetings?limit=25", {
      method: "GET",
    });

    const response = await GET(request, {
      params: Promise.resolve({ path: ["meetings"] }),
    });

    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "https://gateway.example/bots?limit=25&offset=0",
      expect.objectContaining({
        headers: { "X-API-Key": "environment-fallback-key" },
      })
    );
  });

  it("stops a chunked voiceprint body as soon as the bounded reader exceeds its cap", async () => {
    const sentinel = "SENSITIVE-AUDIO-SENTINEL";
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode("12345678"));
        controller.enqueue(new TextEncoder().encode(sentinel));
        controller.close();
      },
    });
    const request = new Request("https://dashboard.example/api/vexa/voiceprints/enroll-from-audio", {
      method: "POST",
      body: stream,
      duplex: "half",
    } as RequestInit & { duplex: "half" });

    await expect(readBoundedVoiceprintProxyBody(request, 8)).rejects.toThrow(
      "Voiceprint audio request is too large"
    );
  });

  it("returns 413 before forwarding an oversized authenticated voiceprint body", async () => {
    vi.mocked(cookies).mockResolvedValue({
      get: () => ({ value: "user-token" }),
      delete: vi.fn(),
    } as never);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const request = new NextRequest(
      "https://dashboard.example/api/vexa/voiceprints/enroll-from-audio",
      {
        method: "POST",
        headers: { "content-length": String(32 * 1024 * 1024) },
        body: JSON.stringify({ audio_base64: "SENSITIVE-AUDIO-SENTINEL" }),
      }
    );

    const response = await POST(request, {
      params: Promise.resolve({ path: ["voiceprints", "enroll-from-audio"] }),
    });

    expect(response.status).toBe(413);
    expect(await response.text()).not.toContain("SENSITIVE-AUDIO-SENTINEL");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    "preview-from-segments",
    "enroll-from-segments",
  ])("applies the 64 KiB selected-audio cap before forwarding %s", async (route) => {
    vi.mocked(cookies).mockResolvedValue({
      get: () => ({ value: "user-token" }),
      delete: vi.fn(),
    } as never);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const sentinel = "SENSITIVE-SELECTED-AUDIO-SENTINEL";
    const request = new NextRequest(
      `https://dashboard.example/api/vexa/voiceprints/${route}`,
      {
        method: "POST",
        headers: { "content-length": String(64 * 1024 + 1) },
        body: JSON.stringify({ segment_ids: [sentinel] }),
      }
    );

    const response = await POST(request, {
      params: Promise.resolve({ path: ["voiceprints", route] }),
    });

    expect(response.status).toBe(413);
    expect(await response.text()).not.toContain(sentinel);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("returns 429 without reading a second direct-audio body and accepts it after release", async () => {
    vi.mocked(cookies).mockResolvedValue({
      get: () => ({ value: "user-token" }),
      delete: vi.fn(),
    } as never);

    let resolveFirstFetch!: (response: Response) => void;
    const firstFetchStarted = new Promise<void>((resolve) => {
      const fetchMock = vi.fn()
        .mockImplementationOnce(() => {
          resolve();
          return new Promise<Response>((resolveFetch) => {
            resolveFirstFetch = resolveFetch;
          });
        })
        .mockResolvedValue(new Response(
          JSON.stringify({ ok: true }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        ));
      vi.stubGlobal("fetch", fetchMock);
    });

    const firstRequest = new NextRequest(
      "https://dashboard.example/api/vexa/voiceprints/enroll-from-audio",
      { method: "POST", body: JSON.stringify({ audio_base64: "first" }) }
    );
    const firstResponsePromise = POST(firstRequest, {
      params: Promise.resolve({ path: ["voiceprints", "enroll-from-audio"] }),
    });
    await firstFetchStarted;

    const secondBodyGetReader = vi.fn(() => {
      throw new Error("busy request body must not be read");
    });
    const secondRequest = new NextRequest(
      "https://dashboard.example/api/vexa/voiceprints/enroll-from-audio",
      { method: "POST", body: "{}" }
    );
    Object.defineProperty(secondRequest, "body", {
      configurable: true,
      value: { getReader: secondBodyGetReader },
    });

    const busy = await POST(secondRequest, {
      params: Promise.resolve({ path: ["voiceprints", "enroll-from-audio"] }),
    });
    expect(busy.status).toBe(429);
    expect(busy.headers.get("retry-after")).toBe("1");
    expect(busy.headers.get("cache-control")).toBe("no-store");
    expect(secondBodyGetReader).not.toHaveBeenCalled();
    expect(fetch).toHaveBeenCalledTimes(1);

    resolveFirstFetch(new Response(
      JSON.stringify({ ok: true }),
      { status: 200, headers: { "Content-Type": "application/json" } }
    ));
    expect((await firstResponsePromise).status).toBe(200);

    const accepted = await POST(new NextRequest(
      "https://dashboard.example/api/vexa/voiceprints/enroll-from-audio",
      { method: "POST", body: JSON.stringify({ audio_base64: "third" }) }
    ), {
      params: Promise.resolve({ path: ["voiceprints", "enroll-from-audio"] }),
    });
    expect(accepted.status).toBe(200);
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("releases direct-audio admission after an upstream exception", async () => {
    vi.mocked(cookies).mockResolvedValue({
      get: () => ({ value: "user-token" }),
      delete: vi.fn(),
    } as never);
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new Error("simulated upstream failure"))
      .mockResolvedValueOnce(new Response(
        JSON.stringify({ ok: true }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      ));
    vi.stubGlobal("fetch", fetchMock);

    const first = await POST(new NextRequest(
      "https://dashboard.example/api/vexa/voiceprints/enroll-from-audio",
      { method: "POST", body: "{}" }
    ), {
      params: Promise.resolve({ path: ["voiceprints", "enroll-from-audio"] }),
    });
    expect(first.status).toBe(502);

    const accepted = await POST(new NextRequest(
      "https://dashboard.example/api/vexa/voiceprints/enroll-from-audio",
      { method: "POST", body: "{}" }
    ), {
      params: Promise.resolve({ path: ["voiceprints", "enroll-from-audio"] }),
    });
    expect(accepted.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
