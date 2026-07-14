import { afterEach, describe, expect, it, vi } from "vitest";
import { vexaAPI } from "@/lib/api";

describe("selected segment voiceprint API", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("requests an exact WAV preview for the selected segment ids", async () => {
    const clipSha256 = "a".repeat(64);
    const sourceFingerprint = "b".repeat(64);
    const preview = {
      audio_base64: "UklGRg==",
      media_format: "wav",
      content_type: "audio/wav",
      duration_seconds: 8.4,
      selection_count: 2,
      clip_sha256: clipSha256,
      source_fingerprint: sourceFingerprint,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => preview,
      })
    );

    const result = await vexaAPI.previewVoiceprintFromSegments(42, ["seg-1", "seg-3"]);

    expect(fetch).toHaveBeenCalledWith("/api/vexa/voiceprints/preview-from-segments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        meeting_id: 42,
        segment_ids: ["seg-1", "seg-3"],
      }),
    });
    expect(result).toEqual(preview);
  });

  it("binds enrollment to the reviewed clip hash and both confirmations", async () => {
    const clipSha256 = "a".repeat(64);
    const sourceFingerprint = "b".repeat(64);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({
          profile_id: 7,
          display_name: "田中",
          voiceprint_id: 99,
          consent_id: 12,
        }),
      })
    );

    await vexaAPI.enrollVoiceprintFromSegments(
      42,
      ["seg-1", "seg-3"],
      "田中",
      clipSha256,
      sourceFingerprint
    );

    expect(fetch).toHaveBeenCalledWith("/api/vexa/voiceprints/enroll-from-segments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        meeting_id: 42,
        segment_ids: ["seg-1", "seg-3"],
        display_name: "田中",
        clip_sha256: clipSha256,
        source_fingerprint: sourceFingerprint,
        audio_review_confirmed: true,
        consent_confirmed: true,
      }),
    });
  });
});

describe("vexaAPI.rejectSpeakerSuggestion", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("DELETEs the meeting's speaker-suggestions/{cluster_id} endpoint", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 204,
        text: async () => "",
      })
    );

    await vexaAPI.rejectSpeakerSuggestion(42, "lane:hash123:1");

    expect(fetch).toHaveBeenCalledWith(
      "/api/vexa/meetings/42/speaker-suggestions/lane:hash123:1",
      { method: "DELETE" }
    );
  });

  it("throws when the reject request fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 404,
        statusText: "Not Found",
        text: async () => "",
      })
    );

    await expect(vexaAPI.rejectSpeakerSuggestion(42, "lane:hash123:1")).rejects.toThrow();
  });
});
