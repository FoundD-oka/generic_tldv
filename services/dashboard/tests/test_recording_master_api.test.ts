import { afterEach, describe, expect, it, vi } from "vitest";
import { vexaAPI } from "@/lib/api";

describe("vexaAPI.getRecordingMasterStreamUrl", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns null while the canonical master is not ready", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        status: 404,
        ok: false,
      })
    );

    await expect(vexaAPI.getRecordingMasterStreamUrl(42, "audio")).resolves.toBeNull();
    expect(fetch).toHaveBeenCalledWith("/api/vexa/recordings/42/master?type=audio");
  });

  it("returns the dashboard master proxy URL for a resolved local master", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        status: 200,
        ok: true,
        json: async () => ({
          url: "/recordings/42/media/7/raw",
          duration_seconds: 12.5,
        }),
      })
    );

    await expect(vexaAPI.getRecordingMasterStreamUrl(42, "audio")).resolves.toEqual({
      url: "/api/vexa/recordings/42/master?type=audio&proxy=1",
      duration_seconds: 12.5,
    });
  });

  it("keeps playback on the master proxy when the response includes a raw route", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        status: 200,
        ok: true,
        json: async () => ({
          url: "http://localhost:42268/vexa-recordings/master.wav?X-Amz-Signature=abc",
          raw_url: "/recordings/42/media/7/raw",
          duration_seconds: 12.5,
        }),
      })
    );

    await expect(vexaAPI.getRecordingMasterStreamUrl(42, "audio")).resolves.toEqual({
      url: "/api/vexa/recordings/42/master?type=audio&proxy=1",
      duration_seconds: 12.5,
    });
  });

  it("keeps playback on the master proxy when only a presigned URL is returned", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        status: 200,
        ok: true,
        json: async () => ({
          url: "http://localhost:42268/vexa-recordings/master.wav?X-Amz-Signature=abc",
          duration_seconds: 12.5,
        }),
      })
    );

    await expect(vexaAPI.getRecordingMasterStreamUrl(42, "audio")).resolves.toEqual({
      url: "/api/vexa/recordings/42/master?type=audio&proxy=1",
      duration_seconds: 12.5,
    });
  });
});
