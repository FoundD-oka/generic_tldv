import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";

import { VexaAPIError, vexaAPI } from "@/lib/api";
import { useMeetingsStore } from "@/stores/meetings-store";
import type { ChatMessage, Meeting, RecordingData, TranscriptSegment } from "@/types/vexa";

function meeting(id: string, nativeId = `native-${id}`, recordings: RecordingData[] = []): Meeting {
  return {
    id,
    platform: "google_meet",
    platform_specific_id: nativeId,
    status: "completed",
    start_time: null,
    end_time: null,
    bot_container_id: null,
    data: { recordings },
    created_at: "2026-09-05T00:00:00Z",
    updated_at: `2026-09-05T00:00:0${id}Z`,
  };
}

function recording(id: number): RecordingData {
  return {
    id,
    meeting_id: id,
    user_id: 1,
    session_uid: `session-${id}`,
    source: "bot",
    status: "completed",
    created_at: "2026-09-05T00:00:00Z",
    completed_at: "2026-09-05T00:01:00Z",
    media_files: [{
      id: id + 100,
      type: "audio",
      format: "webm",
      storage_path: `recordings/${id}/master.webm`,
      storage_backend: "minio",
      file_size_bytes: 100,
      duration_seconds: 10,
      finalized_by: "recording_finalizer.master",
      is_final: true,
      created_at: "2026-09-05T00:01:00Z",
    }],
    playback_url: { audio: `/recordings/${id}/master?type=audio`, video: null },
  };
}

function segment(meetingId: string, text: string): TranscriptSegment {
  return {
    id: `${meetingId}-${text}`,
    meeting_id: meetingId,
    start_time: 0,
    end_time: 1,
    absolute_start_time: "2026-09-05T00:00:00Z",
    absolute_end_time: "2026-09-05T00:00:01Z",
    text,
    speaker: "speaker",
    language: "ja",
    session_uid: `session-${meetingId}`,
    created_at: "2026-09-05T00:00:00Z",
  };
}

function chat(text: string): ChatMessage {
  return { sender: "speaker", text, timestamp: 1, is_from_bot: false };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

beforeEach(() => {
  useMeetingsStore.getState().clearCurrentMeeting();
  useMeetingsStore.setState({ meetings: [], error: null, subscriptionRequired: false });
});

afterEach(() => vi.restoreAllMocks());

describe("meeting detail request scope", () => {
  it("R05 ignores stale detail success and failure", async () => {
    const staleOutcomes: Array<Meeting | Error> = [
      meeting("1", "native-a", [recording(1)]),
      new VexaAPIError("Not Found", 404),
      new VexaAPIError("Payment Required", 402),
      new VexaAPIError("Unavailable", 503),
    ];

    for (const outcome of staleOutcomes) {
      useMeetingsStore.getState().clearCurrentMeeting();
      useMeetingsStore.setState({ meetings: [], error: null, subscriptionRequired: false });
      const stale = deferred<Meeting>();
      const current = meeting("2", "native-b", [recording(2)]);
      vi.spyOn(vexaAPI, "getMeeting")
        .mockImplementationOnce(() => stale.promise)
        .mockResolvedValueOnce(current);

      const requestA = useMeetingsStore.getState().fetchMeeting("1");
      const requestB = useMeetingsStore.getState().fetchMeeting("2");
      await requestB;
      const before = useMeetingsStore.getState();

      if (outcome instanceof Error) stale.reject(outcome);
      else stale.resolve(outcome);
      await requestA;

      const after = useMeetingsStore.getState();
      expect(after.currentMeeting?.id).toBe("2");
      expect(after.recordings).toEqual(before.recordings);
      expect(after.error).toBe(before.error);
      expect(after.subscriptionRequired).toBe(before.subscriptionRequired);
      expect(after.isLoadingMeeting).toBe(false);
      vi.restoreAllMocks();
    }
  });

  it("R05 clearing invalidates every channel", async () => {
    const detail = deferred<Meeting>();
    const transcripts = deferred<{ meeting: Meeting; segments: TranscriptSegment[]; recordings: RecordingData[] }>();
    const messages = deferred<{ messages: ChatMessage[]; meeting_id: number }>();
    vi.spyOn(vexaAPI, "getMeeting").mockImplementation(() => detail.promise);
    vi.spyOn(vexaAPI, "getMeetingWithTranscripts").mockImplementation(() => transcripts.promise);
    vi.spyOn(vexaAPI, "getChatMessages").mockImplementation(() => messages.promise);

    const owner = meeting("1", "native-a");
    useMeetingsStore.getState().setCurrentMeeting(owner);
    const bootstrap = vi.spyOn(useMeetingsStore.getState(), "bootstrapTranscripts");
    const detailRequest = useMeetingsStore.getState().fetchMeeting("1");
    const transcriptRequest = useMeetingsStore.getState().fetchTranscripts("google_meet", "native-a", "1");
    const chatRequest = useMeetingsStore.getState().fetchChatMessages("google_meet", "native-a");

    useMeetingsStore.getState().clearCurrentMeeting();
    messages.resolve({ messages: [chat("old")], meeting_id: 1 });
    detail.resolve(meeting("1", "native-a", [recording(1)]));
    transcripts.resolve({ meeting: owner, segments: [segment("1", "old")], recordings: [recording(1)] });
    await Promise.all([detailRequest, transcriptRequest, chatRequest]);

    expect(useMeetingsStore.getState()).toMatchObject({
      currentMeeting: null,
      transcripts: [],
      recordings: [],
      chatMessages: [],
      isLoadingMeeting: false,
      isLoadingTranscripts: false,
    });
    expect(bootstrap).not.toHaveBeenCalled();
  });

  it("R05 late transcript and chat cannot enter another meeting", async () => {
    const oldTranscripts = deferred<{ meeting: Meeting; segments: TranscriptSegment[]; recordings: RecordingData[] }>();
    const oldChat = deferred<{ messages: ChatMessage[]; meeting_id: number }>();
    const ownerA = meeting("1", "shared-native", [recording(1)]);
    const ownerB = meeting("2", "shared-native", [recording(2)]);
    vi.spyOn(vexaAPI, "getMeetingWithTranscripts")
      .mockImplementationOnce(() => oldTranscripts.promise)
      .mockResolvedValueOnce({ meeting: ownerB, segments: [segment("2", "new")], recordings: [recording(2)] });
    vi.spyOn(vexaAPI, "getChatMessages")
      .mockImplementationOnce(() => oldChat.promise)
      .mockResolvedValueOnce({ messages: [chat("new")], meeting_id: 2 });

    useMeetingsStore.getState().setCurrentMeeting(ownerA);
    const transcriptA = useMeetingsStore.getState().fetchTranscripts("google_meet", "shared-native", "1");
    const chatA = useMeetingsStore.getState().fetchChatMessages("google_meet", "shared-native");
    useMeetingsStore.getState().setCurrentMeeting(ownerB);
    await useMeetingsStore.getState().fetchTranscripts("google_meet", "shared-native", "2");
    await useMeetingsStore.getState().fetchChatMessages("google_meet", "shared-native");

    oldChat.resolve({ messages: [chat("old")], meeting_id: 1 });
    oldTranscripts.resolve({ meeting: ownerA, segments: [segment("1", "old")], recordings: [recording(1)] });
    await Promise.all([transcriptA, chatA]);

    const state = useMeetingsStore.getState();
    expect(state.currentMeeting?.id).toBe("2");
    expect(state.transcripts.map((item) => item.text)).toEqual(["new"]);
    expect(state.recordings.map((item) => item.id)).toEqual([2]);
    expect(state.chatMessages.map((item) => item.text)).toEqual(["new"]);
  });

  it("R05 latest response owns loading and recording metadata", async () => {
    const oldDetail = deferred<Meeting>();
    const newDetail = deferred<Meeting>();
    vi.spyOn(vexaAPI, "getMeeting")
      .mockImplementationOnce(() => oldDetail.promise)
      .mockImplementationOnce(() => newDetail.promise);

    useMeetingsStore.getState().setCurrentMeeting(meeting("1", "native-a"));
    const oldRequest = useMeetingsStore.getState().fetchMeeting("1");
    const newRequest = useMeetingsStore.getState().fetchMeeting("1");
    oldDetail.reject(new Error("old detail failed"));
    await oldRequest;
    expect(useMeetingsStore.getState().isLoadingMeeting).toBe(true);
    newDetail.resolve(meeting("1", "native-a"));
    await newRequest;

    const oldTranscript = deferred<{ meeting: Meeting; segments: TranscriptSegment[]; recordings: RecordingData[] }>();
    vi.spyOn(vexaAPI, "getMeetingWithTranscripts").mockImplementation(() => oldTranscript.promise);
    const transcriptRequest = useMeetingsStore.getState().fetchTranscripts("google_meet", "native-a", "1", { silent: true });
    const completed = meeting("1", "native-a", [recording(10)]);
    vi.spyOn(vexaAPI, "getMeeting").mockResolvedValueOnce(completed);
    await useMeetingsStore.getState().fetchMeeting("1", { silent: true });
    oldTranscript.resolve({ meeting: completed, segments: [segment("1", "current text")], recordings: [] });
    await transcriptRequest;

    expect(useMeetingsStore.getState().recordings.map((item) => item.id)).toEqual([10]);
    expect((useMeetingsStore.getState().currentMeeting?.data.recordings as RecordingData[]).map((item) => item.id)).toEqual([10]);
  });

  it("R05 current empty recordings remain authoritative", async () => {
    const owner = meeting("1", "native-a", [recording(1)]);
    useMeetingsStore.getState().setCurrentMeeting(owner);
    vi.spyOn(vexaAPI, "getMeetingWithTranscripts").mockResolvedValue({
      meeting: owner,
      segments: [],
      recordings: [],
    });

    await useMeetingsStore.getState().fetchTranscripts("google_meet", "native-a", "1");

    expect(useMeetingsStore.getState().recordings).toEqual([]);
    expect(useMeetingsStore.getState().currentMeeting?.data.recordings).toEqual([]);
  });

  it("R05 refresh accepts existing current meeting without new scope", async () => {
    const owner = meeting("1", "native-a");
    const updated = { ...owner, status: "failed" as const, updated_at: "2026-09-05T00:01:00Z" };
    useMeetingsStore.setState({ currentMeeting: owner });
    const getMeeting = vi.spyOn(vexaAPI, "getMeeting").mockResolvedValue(updated);

    await expect(useMeetingsStore.getState().refreshMeeting("1")).resolves.toEqual(updated);
    expect(useMeetingsStore.getState().currentMeeting?.status).toBe("failed");
    expect(getMeeting).toHaveBeenCalledTimes(1);

    useMeetingsStore.getState().clearCurrentMeeting();
    await expect(useMeetingsStore.getState().refreshMeeting("1")).resolves.toBeNull();
    expect(getMeeting).toHaveBeenCalledTimes(1);
  });
});
