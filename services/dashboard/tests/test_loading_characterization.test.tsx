// @vitest-environment jsdom

import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AudioPlayerHandle } from "@/components/recording/audio-player";
import type { VideoPlayerHandle } from "@/components/recording/video-player";
import { useMeetingPlayback, type MeetingPlayback } from "@/hooks/use-meeting-playback";
import { vexaAPI } from "@/lib/api";
import { useMeetingsStore } from "@/stores/meetings-store";
import type { Meeting, RecordingData, TranscriptSegment } from "@/types/vexa";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function meeting(id: number, createdAt: string, redacted = false): Meeting {
  return {
    id: String(id),
    platform: "google_meet",
    platform_specific_id: `meeting-${id}`,
    status: "completed",
    start_time: null,
    end_time: null,
    bot_container_id: null,
    data: redacted ? { redacted: true } : {},
    created_at: createdAt,
  };
}

function rawMeeting(id: number, nativeId: string, createdAt: string) {
  return {
    id,
    user_id: 5,
    platform: "google_meet",
    native_meeting_id: nativeId,
    status: "completed",
    start_time: null,
    end_time: null,
    bot_container_id: null,
    data: {},
    created_at: createdAt,
  };
}

function recording(id: number, sessionUid: string, createdAt: string): RecordingData {
  return {
    id,
    meeting_id: 42,
    user_id: 5,
    session_uid: sessionUid,
    source: "bot",
    status: "completed",
    created_at: createdAt,
    completed_at: createdAt,
    media_files: [],
    playback_url: {
      audio: `/recordings/${id}/master?type=audio`,
      video: null,
    },
  };
}

function transcript(sessionUid: string, start: number, absolute: string): TranscriptSegment {
  return {
    id: `${sessionUid}-${start}`,
    meeting_id: "42",
    start_time: start,
    end_time: start + 1,
    absolute_start_time: absolute,
    absolute_end_time: new Date(new Date(absolute).getTime() + 1000).toISOString(),
    text: "characterization",
    speaker: "speaker-1",
    language: "ja",
    session_uid: sessionUid,
    created_at: absolute,
  };
}

let root: Root | null = null;
let container: HTMLDivElement | null = null;

beforeEach(() => {
  useMeetingsStore.setState(useMeetingsStore.getInitialState());
});

afterEach(async () => {
  if (root) {
    await act(async () => root?.unmount());
  }
  container?.remove();
  root = null;
  container = null;
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("loading and playback characterization", () => {
  it("R00 list preserves page cursor after redaction", async () => {
    const firstPage = Array.from({ length: 50 }, (_, index) =>
      meeting(index + 1, new Date(Date.UTC(2026, 0, 1, 0, 0, 50 - index)).toISOString(), index < 2)
    );
    const secondPage = Array.from({ length: 5 }, (_, index) =>
      meeting(index + 51, new Date(Date.UTC(2025, 11, 31, 23, 59, 59 - index)).toISOString())
    );
    const getMeetings = vi
      .spyOn(vexaAPI, "getMeetings")
      .mockResolvedValueOnce({ meetings: firstPage, has_more: true })
      .mockResolvedValueOnce({ meetings: secondPage, has_more: false });

    await useMeetingsStore.getState().fetchMeetings();
    expect(useMeetingsStore.getState().meetings).toHaveLength(48);
    expect(useMeetingsStore.getState()._offset).toBe(50);

    await useMeetingsStore.getState().fetchMoreMeetings();
    const finalState = useMeetingsStore.getState();
    expect(getMeetings).toHaveBeenNthCalledWith(2, { limit: 50, offset: 50 });
    expect(finalState.meetings).toHaveLength(53);
    expect(new Set(finalState.meetings.map((item) => item.id)).size).toBe(53);
  });

  it("R00 list maps wire identity without changing order", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            meetings: [
              rawMeeting(41, "older-aaaa-bbb", "2026-01-01T00:00:01Z"),
              rawMeeting(42, "abc-defg-hij", "2026-01-01T00:00:03Z"),
              rawMeeting(43, "middle-aaa-bbb", "2026-01-01T00:00:02Z"),
            ],
            has_more: true,
          }),
          { status: 200 }
        )
      )
    );

    await useMeetingsStore.getState().fetchMeetings();
    const state = useMeetingsStore.getState();
    expect(state.meetings.map((item) => item.id)).toEqual(["42", "43", "41"]);
    expect(state.meetings[0].platform_specific_id).toBe("abc-defg-hij");
    expect(state.meetings[0].status).toBe("completed");
    expect(state.hasMore).toBe(true);
  });

  it("R00 master keeps same origin and not ready semantics", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            url: "https://storage.example.invalid/signed-master.wav",
            raw_url: "/recordings/42/media/7/raw",
            duration_seconds: 12.5,
          }),
          { status: 200 }
        )
      )
      .mockResolvedValueOnce(new Response("not ready", { status: 404 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(vexaAPI.getRecordingMasterStreamUrl(42, "audio")).resolves.toEqual({
      url: "/api/vexa/recordings/42/master?type=audio&proxy=1",
      duration_seconds: 12.5,
    });
    await expect(vexaAPI.getRecordingMasterStreamUrl(42, "audio")).resolves.toBeNull();
    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/vexa/recordings/42/master?type=audio");
  });

  it("R00 playback keeps fragment seek coordinates", async () => {
    const sessionA = recording(1001, "session-a", "2026-01-01T00:00:00Z");
    const sessionB = recording(1002, "session-b", "2026-01-01T00:10:00Z");
    const segmentB = transcript("session-b", 3, "2026-01-01T00:10:03Z");
    vi.spyOn(vexaAPI, "getRecordingMasterStreamUrl").mockImplementation(async (id) => ({
      url: `/api/vexa/recordings/${id}/master?type=audio&proxy=1`,
      duration_seconds: id === 1001 ? 12 : 20,
    }));

    let playback!: MeetingPlayback;
    function Harness({ recordings }: { recordings: RecordingData[] }) {
      const currentPlayback = useMeetingPlayback("42", recordings, [segmentB]);
      useEffect(() => {
        playback = currentPlayback;
      }, [currentPlayback]);
      return null;
    }

    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root?.render(<Harness recordings={[sessionA, sessionB]} />);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(playback.recordingFragments.map((item) => item.duration)).toEqual([12, 20]);

    const audio = { seekToFragment: vi.fn(), seekTo: vi.fn() } satisfies AudioPlayerHandle;
    const video = { seekTo: vi.fn() } satisfies VideoPlayerHandle;
    (playback.audioPlayerRef as { current: AudioPlayerHandle | null }).current = audio;
    (playback.videoPlayerRef as { current: VideoPlayerHandle | null }).current = video;
    await act(async () => playback.handleSegmentClick(3, 4, segmentB.absolute_start_time));
    expect(audio.seekToFragment).toHaveBeenCalledWith(1, 3, 4);
    expect(video.seekTo).toHaveBeenCalledWith(15, 16);
    expect(playback.playbackTime).toBe(15);

    await act(async () => {
      root?.render(<Harness recordings={[sessionB]} />);
      await Promise.resolve();
      await Promise.resolve();
    });
    const singleAudio = { seekToFragment: vi.fn(), seekTo: vi.fn() } satisfies AudioPlayerHandle;
    (playback.audioPlayerRef as { current: AudioPlayerHandle | null }).current = singleAudio;
    (playback.videoPlayerRef as { current: VideoPlayerHandle | null }).current = video;
    await act(async () => playback.handleSegmentClick(3, 4, segmentB.absolute_start_time));
    expect(singleAudio.seekTo).toHaveBeenCalledWith(3, 4);
    expect(video.seekTo).toHaveBeenLastCalledWith(3, 4);
  });

  it("R00 auth never trusts persisted credentials", async () => {
    vi.resetModules();
    localStorage.setItem(
      "vexa-auth",
      JSON.stringify({
        state: {
          user: {
            id: "5",
            email: "stale@example.com",
            name: "Stale",
            max_concurrent_bots: 1,
            created_at: "2026-01-01T00:00:00Z",
          },
          token: "STALE-TOKEN",
          isAuthenticated: true,
          didLogout: false,
        },
      })
    );
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ error: "unauthorized" }), { status: 401 }))
    );

    const { useAuthStore } = await import("@/stores/auth-store");
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
    expect(useAuthStore.getState().token).toBeNull();

    await useAuthStore.getState().checkAuth();
    expect(useAuthStore.getState()).toMatchObject({
      user: null,
      token: null,
      isAuthenticated: false,
      isLoading: false,
    });
  });
});
