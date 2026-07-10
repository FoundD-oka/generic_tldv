import { afterEach, describe, expect, it, vi } from "vitest";
import { vexaAPI } from "@/lib/api";

// issue #26 Phase 3 / critique B-1: the REST mapper (RawSegment ->
// TranscriptSegment) must carry speaker_mapping_status through, in addition
// to speaker_cluster which was already mapped. Without this the deferred/PG
// transcript view never learns a segment is "needs_review".
describe("vexaAPI.getMeetingWithTranscripts REST mapper", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  const baseResponse = {
    id: 42,
    platform: "google_meet" as const,
    native_meeting_id: "abc-defg-hij",
    status: "completed",
    start_time: "2026-07-10T01:00:00",
    end_time: "2026-07-10T02:00:00",
    recordings: [],
  };

  it("carries speaker_mapping_status and speaker_cluster onto TranscriptSegment", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({
          ...baseResponse,
          segments: [
            {
              start: 0,
              end: 1.5,
              text: "こんにちは",
              speaker: null,
              language: "ja",
              absolute_start_time: "2026-07-10T01:00:00",
              absolute_end_time: "2026-07-10T01:00:01.5",
              created_at: "2026-07-10T01:00:01.5",
              segment_id: "deferred:42:1:0.000",
              speaker_cluster: "lane:hash123:1",
              speaker_auto: null,
              speaker_mapping_status: "needs_review",
            },
          ],
        }),
      })
    );

    const { segments } = await vexaAPI.getMeetingWithTranscripts("google_meet", "abc-defg-hij");

    expect(segments).toHaveLength(1);
    expect(segments[0].speaker_cluster).toBe("lane:hash123:1");
    expect(segments[0].speaker_mapping_status).toBe("needs_review");
    expect(segments[0].speaker).toBe("");
  });

  it("leaves speaker_mapping_status undefined for a normally-named segment", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({
          ...baseResponse,
          segments: [
            {
              start: 0,
              end: 1.5,
              text: "よろしくお願いします",
              speaker: "田中",
              language: "ja",
              absolute_start_time: "2026-07-10T01:00:00",
              absolute_end_time: "2026-07-10T01:00:01.5",
              created_at: "2026-07-10T01:00:01.5",
              segment_id: "deferred:42:2:0.000",
              speaker_cluster: "1",
              speaker_auto: "田中",
            },
          ],
        }),
      })
    );

    const { segments } = await vexaAPI.getMeetingWithTranscripts("google_meet", "abc-defg-hij");

    expect(segments[0].speaker).toBe("田中");
    expect(segments[0].speaker_cluster).toBe("1");
    expect(segments[0].speaker_mapping_status).toBeUndefined();
  });
});
