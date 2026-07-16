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

  it("skips a silent refresh while a foreground request owns the loading state", async () => {
    const foregroundResponse = deferred<{ meetings: Meeting[]; has_more: boolean }>();
    const getMeetings = vi.spyOn(vexaAPI, "getMeetings").mockImplementationOnce(() => foregroundResponse.promise);

    useMeetingsStore.setState({ meetings: [], isLoadingMeetings: false, _offset: 0, _filters: {} });
    const foregroundRequest = useMeetingsStore.getState().fetchMeetings();
    await useMeetingsStore.getState().fetchMeetings(undefined, { silent: true });
    expect(getMeetings).toHaveBeenCalledTimes(1);

    foregroundResponse.resolve({ meetings: [meeting("succeeded")], has_more: false });
    await foregroundRequest;
    expect(useMeetingsStore.getState().isLoadingMeetings).toBe(false);
  });

  it("ignores an older foreground error after a newer request succeeds", async () => {
    const oldResponse = deferred<{ meetings: Meeting[]; has_more: boolean }>();
    const newResponse = deferred<{ meetings: Meeting[]; has_more: boolean }>();
    vi.spyOn(vexaAPI, "getMeetings")
      .mockImplementationOnce(() => oldResponse.promise)
      .mockImplementationOnce(() => newResponse.promise);

    useMeetingsStore.setState({ meetings: [], error: null, isLoadingMeetings: false, _offset: 0, _filters: {} });
    const oldRequest = useMeetingsStore.getState().fetchMeetings();
    const newRequest = useMeetingsStore.getState().fetchMeetings();
    newResponse.resolve({ meetings: [meeting("succeeded")], has_more: false });
    await newRequest;
    oldResponse.resolve(Promise.reject(new Error("stale failure")) as never);
    await oldRequest;

    expect(useMeetingsStore.getState().error).toBeNull();
    expect(useMeetingsStore.getState().meetings[0].data.final_transcription?.status).toBe("succeeded");
  });

  it("does not start a silent refresh while pagination is in flight", async () => {
    const moreResponse = deferred<{ meetings: Meeting[]; has_more: boolean }>();
    const getMeetings = vi.spyOn(vexaAPI, "getMeetings").mockImplementationOnce(() => moreResponse.promise);
    useMeetingsStore.setState({
      meetings: [meeting("running")],
      hasMore: true,
      isLoadingMore: false,
      isLoadingMeetings: false,
      _offset: 50,
      _filters: {},
    });

    const moreRequest = useMeetingsStore.getState().fetchMoreMeetings();
    await useMeetingsStore.getState().fetchMeetings(undefined, { silent: true });
    expect(getMeetings).toHaveBeenCalledTimes(1);

    moreResponse.resolve({ meetings: [], has_more: false });
    await moreRequest;
    expect(useMeetingsStore.getState().isLoadingMore).toBe(false);
  });
});
