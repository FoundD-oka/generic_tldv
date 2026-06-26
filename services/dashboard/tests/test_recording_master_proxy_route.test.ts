import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GET } from "@/app/api/vexa/[...path]/route";

vi.mock("next/headers", () => ({
  cookies: vi.fn(async () => ({
    get: (name: string) =>
      name === "test-vexa-token" ? { value: "vxa_bot_test_token" } : undefined,
    delete: vi.fn(),
  })),
}));

describe("recording master proxy route", () => {
  beforeEach(() => {
    process.env.VEXA_API_URL = "https://gateway.example";
    process.env.VEXA_AUTH_COOKIE_NAME = "test-vexa-token";
  });

  afterEach(() => {
    vi.restoreAllMocks();
    delete process.env.VEXA_API_URL;
    delete process.env.VEXA_AUTH_COOKIE_NAME;
  });

  it("streams via backend raw_url when the master endpoint returns an internal MinIO URL", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            url: "http://minio:9000/vexa/recordings/3/42/session/audio/master.webm?X-Amz-Signature=abc",
            raw_url: "/recordings/42/media/7/raw",
            filename: "42_audio.webm",
            content_type: "audio/webm",
          }),
          {
            status: 200,
            headers: { "content-type": "application/json" },
          }
        )
      )
      .mockResolvedValueOnce(
        new Response("audio-chunk", {
          status: 206,
          headers: {
            "content-type": "audio/webm",
            "content-range": "bytes 0-10/115112529",
            "content-length": "11",
            "accept-ranges": "bytes",
          },
        })
      );
    vi.stubGlobal("fetch", fetchMock);

    const request = new NextRequest(
      "https://dashboard.example/api/vexa/recordings/42/master?type=audio&proxy=1",
      { headers: { range: "bytes=0-10" } }
    );

    const response = await GET(request, {
      params: Promise.resolve({ path: ["recordings", "42", "master"] }),
    });

    expect(response.status).toBe(206);
    expect(response.headers.get("content-type")).toBe("audio/webm");
    expect(response.headers.get("content-range")).toBe("bytes 0-10/115112529");
    await expect(response.text()).resolves.toBe("audio-chunk");

    const secondCall = fetchMock.mock.calls[1];
    expect(String(secondCall[0])).toBe("https://gateway.example/recordings/42/media/7/raw");
    expect(secondCall[1]).toMatchObject({
      headers: {
        Range: "bytes=0-10",
        "X-API-Key": "vxa_bot_test_token",
      },
    });
  });

  it("rejects internal presigned URLs when no backend raw route is available", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          url: "http://minio:9000/vexa/recordings/3/42/session/audio/master.webm?X-Amz-Signature=abc",
          content_type: "audio/webm",
        }),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        }
      )
    );
    vi.stubGlobal("fetch", fetchMock);

    const request = new NextRequest(
      "https://dashboard.example/api/vexa/recordings/42/master?type=audio&proxy=1"
    );

    const response = await GET(request, {
      params: Promise.resolve({ path: ["recordings", "42", "master"] }),
    });

    expect(response.status).toBe(502);
    await expect(response.json()).resolves.toEqual({
      error: "Internal presigned media URL is not proxy-safe; backend raw_url missing",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
