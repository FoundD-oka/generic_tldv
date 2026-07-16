import { afterEach, describe, expect, it, vi } from "vitest";

import { vexaAPI } from "@/lib/api";
import { useMeetingsStore } from "@/stores/meetings-store";
import type { Meeting } from "@/types/vexa";

function meeting(finalStatus: string): Meeting {
  return {
    id: "1",
    platform: "google_meet",
    platform_specific_id: "abc-defg-hij",
    status: "completed",
    start_time: null,
    end_time: null,
    bot_container_id: null,
    data: { final_transcription: { status: finalStatus } },
    created_at: "2026-07-16T00:00:00Z",
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

describe("meetings refresh ordering", () => {
  afterEach(() => vi.restoreAllMocks());

  it("does not let an older silent response overwrite a newer terminal state", async () => {
    const oldResponse = deferred<{ meetings: Meeting[]; has_more: boolean }>();
    const newResponse = deferred<{ meetings: Meeting[]; has_more: boolean }>();
    vi.spyOn(vexaAPI, "getMeetings")
      .mockImplementationOnce(() => oldResponse.promise)
      .mockImplementationOnce(() => newResponse.promise);

    useMeetingsStore.setState({ meetings: [meeting("queued")], _offset: 50, _filters: {} });
    const silentRequest = useMeetingsStore.getState().fetchMeetings(undefined, { silent: true });
    const foregroundRequest = useMeetingsStore.getState().fetchMeetings();

    newResponse.resolve({ meetings: [meeting("succeeded")], has_more: false });
    await foregroundRequest;
    oldResponse.resolve({ meetings: [meeting("queued")], has_more: false });
    await silentRequest;

    expect(useMeetingsStore.getState().meetings[0].data.final_transcription?.status).toBe("succeeded");
  });
});
