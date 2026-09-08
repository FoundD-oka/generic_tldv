import { NextRequest } from "next/server";
import { cookies } from "next/headers";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GET } from "@/app/api/vexa/[...path]/route";
import {
  MEDIA_PROXY_RANGE_MAX_BYTES,
  boundMediaRangeHeader,
} from "@/lib/media-proxy-range";

vi.mock("next/headers", () => ({
  cookies: vi.fn(async () => ({
    get: (name: string) =>
      name === "test-vexa-token" ? { value: "vxa_bot_test_token" } : undefined,
    delete: vi.fn(),
  })),
}));

describe("boundMediaRangeHeader", () => {
  const max = MEDIA_PROXY_RANGE_MAX_BYTES;

  it("uses the fixed 8MiB cap", () => {
    expect(max).toBe(8388608);
  });

  it("returns null for absent or empty ranges", () => {
    expect(boundMediaRangeHeader(null)).toBeNull();
    expect(boundMediaRangeHeader("")).toBeNull();
    expect(boundMediaRangeHeader("   ")).toBeNull();
  });

  it.each([
    ["bytes=0-65535"],
    ["bytes=-1024"],
    ["bytes=-"],
    ["bytes=abc"],
    ["items=0-1"],
    ["bytes=0-1,5-9"],
    ["bytes=5-3"],
    // S / E が safe integer を超える
    ["bytes=9007199254740992-"],
    ["bytes=0-9007199254740992"],
    // S+max-1 が safe integer を超える (overflow guard)
    ["bytes=9007199254740000-"],
  ])("passes %s through unchanged", (input) => {
    expect(boundMediaRangeHeader(input)).toBe(input);
  });

  it("keeps an explicit range whose length is exactly the cap", () => {
    const input = `bytes=0-${max - 1}`; // length == 8388608
    expect(boundMediaRangeHeader(input)).toBe(input);
  });

  it("truncates an explicit range one byte over the cap", () => {
    expect(boundMediaRangeHeader(`bytes=0-${max}`)).toBe(`bytes=0-${max - 1}`); // length 8388609
  });

  it("truncates open-ended ranges", () => {
    expect(boundMediaRangeHeader("bytes=0-")).toBe("bytes=0-8388607");
    expect(boundMediaRangeHeader("bytes=30000000-")).toBe("bytes=30000000-38388607");
  });

  it("truncates large explicit ranges", () => {
    expect(boundMediaRangeHeader("bytes=0-99999999")).toBe("bytes=0-8388607");
  });

  it("trims surrounding whitespace before judging and returns the original when unchanged", () => {
    expect(boundMediaRangeHeader(" bytes=0- ")).toBe("bytes=0-8388607");
    expect(boundMediaRangeHeader(" bytes=0-65535 ")).toBe(" bytes=0-65535 ");
  });
});

describe("vexa proxy route range bounding", () => {
  beforeEach(() => {
    process.env.VEXA_API_URL = "https://gateway.example";
    process.env.VEXA_AUTH_COOKIE_NAME = "test-vexa-token";
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    delete process.env.VEXA_API_URL;
    delete process.env.VEXA_AUTH_COOKIE_NAME;
  });

  const masterJsonResponse = () =>
    new Response(
      JSON.stringify({
        raw_url: "/recordings/841188337344/media/7/raw",
        filename: "841188337344_audio.webm",
        content_type: "audio/webm",
      }),
      { status: 200, headers: { "content-type": "application/json" } }
    );

  // 経路 A: lib/api.ts の `/api/vexa/recordings/{id}/master?type=audio&proxy=1`
  const masterRequest = (range?: string) =>
    new NextRequest(
      "https://dashboard.example/api/vexa/recordings/841188337344/master?type=audio&proxy=1",
      range ? { headers: { range } } : undefined
    );
  const masterParams = {
    params: Promise.resolve({ path: ["recordings", "841188337344", "master"] }),
  };

  const partialResponse = () =>
    new Response("audio-chunk", {
      status: 206,
      headers: {
        "content-type": "audio/webm",
        "content-range": "bytes 0-8388607/52676203",
        "content-length": "8388608",
        "accept-ranges": "bytes",
      },
    });

  it("bounds an open-ended range on the master proxy path and passes 206 through", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(masterJsonResponse())
      .mockResolvedValueOnce(partialResponse());
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET(masterRequest("bytes=0-"), masterParams);

    expect(response.status).toBe(206);
    expect(response.headers.get("content-type")).toBe("audio/webm");
    expect(response.headers.get("content-range")).toBe("bytes 0-8388607/52676203");
    expect(response.headers.get("content-length")).toBe("8388608");
    expect(response.headers.get("accept-ranges")).toBe("bytes");
    await expect(response.text()).resolves.toBe("audio-chunk");

    expect(fetchMock.mock.calls[1][1]).toMatchObject({
      headers: { Range: "bytes=0-8388607", "X-API-Key": "vxa_bot_test_token" },
    });
  });

  it("keeps a small explicit range unchanged on the master proxy path", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(masterJsonResponse())
      .mockResolvedValueOnce(
        new Response("audio-chunk", {
          status: 206,
          headers: {
            "content-type": "audio/webm",
            "content-range": "bytes 0-65535/52676203",
            "content-length": "65536",
            "accept-ranges": "bytes",
          },
        })
      );
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET(masterRequest("bytes=0-65535"), masterParams);

    expect(response.status).toBe(206);
    expect(response.headers.get("content-range")).toBe("bytes 0-65535/52676203");
    expect(fetchMock.mock.calls[1][1].headers.Range).toBe("bytes=0-65535");
  });

  it("truncates an oversized explicit range on the master proxy path", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(masterJsonResponse())
      .mockResolvedValueOnce(partialResponse());
    vi.stubGlobal("fetch", fetchMock);

    await GET(masterRequest("bytes=0-99999999"), masterParams);

    expect(fetchMock.mock.calls[1][1].headers.Range).toBe("bytes=0-8388607");
  });

  it("forwards suffix ranges unchanged on the master proxy path", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(masterJsonResponse())
      .mockResolvedValueOnce(partialResponse());
    vi.stubGlobal("fetch", fetchMock);

    await GET(masterRequest("bytes=-1024"), masterParams);

    expect(fetchMock.mock.calls[1][1].headers.Range).toBe("bytes=-1024");
  });

  it("sends no Range header and passes 200 through when the client sends none", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(masterJsonResponse())
      .mockResolvedValueOnce(
        new Response("audio-chunk", {
          status: 200,
          headers: { "content-type": "audio/webm", "content-length": "11" },
        })
      );
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET(masterRequest(), masterParams);

    expect(response.status).toBe(200);
    expect(response.headers.get("content-length")).toBe("11");
    expect(fetchMock.mock.calls[1][1].headers).not.toHaveProperty("Range");
  });

  it("passes an upstream 416 through on the master proxy path", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(masterJsonResponse())
      .mockResolvedValueOnce(
        new Response("", {
          status: 416,
          headers: {
            "content-type": "audio/webm",
            "content-range": "bytes */52676203",
          },
        })
      );
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET(masterRequest("bytes=99999999999-"), masterParams);

    expect(response.status).toBe(416);
    expect(response.headers.get("content-range")).toBe("bytes */52676203");
  });

  it("bounds an open-ended range on the direct raw media path", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(
      new Response("audio-chunk", {
        status: 206,
        headers: {
          "content-type": "audio/webm",
          "content-range": "bytes 0-8388607/52676203",
          "content-length": "8388608",
          "accept-ranges": "bytes",
        },
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const request = new NextRequest(
      "https://dashboard.example/api/vexa/recordings/42/media/7/raw",
      { headers: { range: "bytes=0-" } }
    );
    const response = await GET(request, {
      params: Promise.resolve({ path: ["recordings", "42", "media", "7", "raw"] }),
    });

    expect(response.status).toBe(206);
    expect(response.headers.get("content-type")).toBe("audio/webm");
    expect(response.headers.get("content-range")).toBe("bytes 0-8388607/52676203");
    expect(fetchMock.mock.calls[0][1].headers.Range).toBe("bytes=0-8388607");
  });

  // 対象外経路: mp3 取得。src/lib/meeting-detail-api.ts の mp3MasterUrl が
  // `/api/vexa/recordings/{id}/master/mp3?type=audio` を生成し、route.ts の
  // isMp3MediaRequest (/^recordings\/\d+\/master\/mp3$/) 分岐で扱われる。
  it.each([["bytes=0-"], ["bytes=0-8388607"]])(
    "forwards %s unchanged on the mp3 download path",
    async (range) => {
      const fetchMock = vi.fn().mockResolvedValueOnce(
        new Response("mp3-chunk", {
          status: 206,
          headers: {
            "content-type": "audio/mpeg",
            "content-range": "bytes 0-8/12345",
            "accept-ranges": "bytes",
          },
        })
      );
      vi.stubGlobal("fetch", fetchMock);

      const request = new NextRequest(
        "https://dashboard.example/api/vexa/recordings/42/master/mp3?type=audio",
        { headers: { range } }
      );
      const response = await GET(request, {
        params: Promise.resolve({ path: ["recordings", "42", "master", "mp3"] }),
      });

      expect(response.status).toBe(206);
      expect(fetchMock.mock.calls[0][1].headers.Range).toBe(range);
    }
  );

  it("returns 401 without calling upstream when the auth cookie is missing", async () => {
    vi.mocked(cookies).mockResolvedValueOnce({
      get: () => undefined,
      delete: vi.fn(),
    } as unknown as Awaited<ReturnType<typeof cookies>>);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET(masterRequest("bytes=0-"), masterParams);

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
