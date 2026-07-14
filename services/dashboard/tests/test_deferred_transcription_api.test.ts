import { afterEach, describe, expect, it, vi } from "vitest";
import { vexaAPI } from "@/lib/api";

describe("deferred transcription API", () => {
  afterEach(() => vi.restoreAllMocks());

  it("enqueues replace mode and polls status", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ status: "queued", run_id: "r1", meeting_id: 42 }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ status: "completed", run_id: "r1", segment_count: 3 }) });
    vi.stubGlobal("fetch", fetchMock);
    await vexaAPI.transcribeMeeting(42, "ja", "replace");
    const state = await vexaAPI.getTranscriptionStatus(42);
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ body: JSON.stringify({ mode: "replace", language: "ja" }) });
    expect(state.status).toBe("completed");
  });
});
