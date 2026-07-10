import { afterEach, describe, expect, it, vi } from "vitest";
import { vexaAPI } from "@/lib/api";

// issue #27 Phase4:声紋登録オファーの「登録する」/候補チップの「却下」から
// 呼ばれる2つのAPIメソッド。既存のupdateSpeakers/deleteMeetingと同じ
// リクエスト整形パターンで検証する。
describe("vexaAPI.enrollVoiceprintFromCluster", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("POSTs meeting_id/cluster_id/display_name to the enroll-from-cluster endpoint", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ status: "enrolled", profile_id: 7, voiceprint_id: 99 }),
      })
    );

    const result = await vexaAPI.enrollVoiceprintFromCluster(42, "lane:hash123:1", "田中");

    expect(fetch).toHaveBeenCalledWith("/api/vexa/voiceprints/enroll-from-cluster", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        meeting_id: 42,
        cluster_id: "lane:hash123:1",
        display_name: "田中",
      }),
    });
    expect(result).toEqual({ status: "enrolled", profile_id: 7, voiceprint_id: 99 });
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
