// @vitest-environment jsdom

import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useMeetingPlayback, type MeetingPlayback } from "@/hooks/use-meeting-playback";
import { vexaAPI } from "@/lib/api";
import type { RecordingData, TranscriptSegment } from "@/types/vexa";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function recording(id: number, options: { audio?: boolean; video?: boolean; createdAt?: string; session?: string; size?: number; meetingId?: number } = {}): RecordingData {
  const createdAt = options.createdAt ?? "2026-01-01T00:00:00Z";
  const audio = options.audio ?? true;
  const video = options.video ?? false;
  return {
    id, meeting_id: options.meetingId ?? 42, user_id: 5, session_uid: options.session ?? `session-${id}`,
    source: "bot", status: "completed", created_at: createdAt, completed_at: createdAt,
    playback_url: { audio: audio ? `/recordings/${id}/master?type=audio` : null, video: video ? `/recordings/${id}/master?type=video` : null },
    media_files: [
      ...(audio ? [{ id: id * 10, type: "audio" as const, format: "wav", storage_path: `${id}/master.wav`, storage_backend: "s3" as const, file_size_bytes: options.size ?? 10, duration_seconds: 12, finalized_by: "recording_finalizer.master", is_final: true, created_at: createdAt }] : []),
      ...(video ? [{ id: id * 10 + 1, type: "video" as const, format: "webm", storage_path: `${id}/master.webm`, storage_backend: "s3" as const, file_size_bytes: options.size ?? 20, duration_seconds: 12, finalized_by: "recording_finalizer.master", is_final: true, created_at: createdAt }] : []),
    ],
  };
}

function transcript(session: string, start = 3): TranscriptSegment {
  return { id: `${session}-${start}`, meeting_id: "42", start_time: start, end_time: start + 1,
    absolute_start_time: "2026-01-01T00:10:03Z", absolute_end_time: "2026-01-01T00:10:04Z",
    text: "test", speaker: "speaker", language: "ja", session_uid: session, created_at: "2026-01-01T00:10:03Z" };
}

type Props = { meetingId: string; recordings: RecordingData[]; transcripts?: TranscriptSegment[] };
let root: Root | null;
let container: HTMLDivElement | null;
let playback: MeetingPlayback;
function Harness(props: Props) {
  const value = useMeetingPlayback(props.meetingId, props.recordings, props.transcripts ?? []);
  useEffect(() => { playback = value; }, [value]);
  return null;
}
async function render(props: Props) {
  if (!root) {
    container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
  }
  await act(async () => { root?.render(<Harness {...props} />); await Promise.resolve(); await Promise.resolve(); });
}
async function advance(ms: number) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); await Promise.resolve(); });
}

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  container?.remove(); root = null; container = null;
  vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks();
});

describe("R07 playback resolution", () => {
  it("R07 equal descriptors do not refetch or reload", async () => {
    const rec = recording(1, { video: true });
    const api = vi.spyOn(vexaAPI, "getRecordingMasterStreamUrl").mockImplementation(async (id, type) => ({ url: `/${id}/${type}`, duration_seconds: 12 }));
    await render({ meetingId: "42", recordings: [rec] });
    const originalSrc = playback.recordingFragments[0].src;
    for (let index = 0; index < 10; index += 1) await render({ meetingId: "42", recordings: structuredClone([rec]), transcripts: index === 9 ? [transcript("session-1")] : [] });
    expect(api).toHaveBeenCalledTimes(2);
    expect(playback.recordingFragments[0].src).toBe(originalSrc);
  });

  it("R07 changed master and meeting invalidate old resolution", async () => {
    let resolveA!: (value: { url: string; duration_seconds: number }) => void;
    const api = vi.spyOn(vexaAPI, "getRecordingMasterStreamUrl")
      .mockImplementationOnce(() => new Promise((resolve) => { resolveA = resolve; }))
      .mockResolvedValueOnce({ url: "/b", duration_seconds: 20 })
      .mockResolvedValueOnce({ url: "/b-changed", duration_seconds: 21 });
    await render({ meetingId: "1", recordings: [recording(1, { meetingId: 1 })] });
    await render({ meetingId: "2", recordings: [recording(2, { meetingId: 2 })] });
    expect(playback.recordingFragments[0].src).toBe("/b");
    await act(async () => { resolveA({ url: "/late-a", duration_seconds: 12 }); await Promise.resolve(); });
    expect(playback.recordingFragments[0].src).toBe("/b");
    const changed = recording(2, { size: 999, meetingId: 2 });
    await render({ meetingId: "2", recordings: [changed] });
    expect(api).toHaveBeenCalledTimes(3);
    expect(playback.recordingFragments[0].src).toBe("/b-changed");
  });

  it("R07 audio and video errors are independent", async () => {
    vi.useFakeTimers();
    const api = vi.spyOn(vexaAPI, "getRecordingMasterStreamUrl").mockImplementation(async (_id, type) => {
      if (type === "audio") throw Object.assign(new Error("audio unavailable"), { status: 503 });
      return { url: "/video", duration_seconds: 12 };
    });
    await render({ meetingId: "1", recordings: [recording(1, { video: true, meetingId: 1 })] });
    await advance(10_500);
    expect(api.mock.calls.filter((call) => call[1] === "audio")).toHaveLength(4);
    expect(playback.audioResolutionError).toContain("audio unavailable");
    expect(playback.videoSrc).toBe("/video");
    expect(playback.videoResolutionError).toBeNull();

    api.mockImplementation(async (_id, type) => {
      if (type === "video") throw Object.assign(new Error("video unavailable"), { status: 503 });
      return { url: "/audio", duration_seconds: 12 };
    });
    await render({ meetingId: "2", recordings: [recording(2, { video: true, meetingId: 2 })] });
    await advance(10_500);
    expect(playback.recordingFragments[0].src).toBe("/audio");
    expect(playback.audioResolutionError).toBeNull();
    expect(playback.videoResolutionError).toContain("video unavailable");
  });

  it("R07 retries are finite and manually recoverable", async () => {
    vi.useFakeTimers();
    let successful = false;
    const api = vi.spyOn(vexaAPI, "getRecordingMasterStreamUrl").mockImplementation(async () => {
      if (!successful) throw Object.assign(new Error("temporary"), { status: 503 });
      return { url: "/recovered", duration_seconds: 12 };
    });
    await render({ meetingId: "42", recordings: [recording(1)] });
    expect(api).toHaveBeenCalledTimes(1);
    await advance(1_499); expect(api).toHaveBeenCalledTimes(1);
    await advance(1); expect(api).toHaveBeenCalledTimes(2);
    await advance(3_000); expect(api).toHaveBeenCalledTimes(3);
    await advance(6_000); expect(api).toHaveBeenCalledTimes(4);
    await advance(60_000); expect(api).toHaveBeenCalledTimes(4);
    expect(playback.audioResolutionError).toContain("temporary");
    successful = true;
    await act(async () => { playback.retryPlayback(); await Promise.resolve(); await Promise.resolve(); });
    expect(api).toHaveBeenCalledTimes(5);
    expect(playback.audioResolutionError).toBeNull();
    expect(playback.recordingFragments[0].src).toBe("/recovered");
  });

  it("R07 no retry for authorization or invalid success", async () => {
    vi.useFakeTimers();
    for (const status of [401, 403, 422]) {
      const api = vi.spyOn(vexaAPI, "getRecordingMasterStreamUrl").mockRejectedValue(Object.assign(new Error(String(status)), { status }));
      await render({ meetingId: String(status), recordings: [recording(status, { meetingId: status })] });
      await advance(60_000);
      expect(api).toHaveBeenCalledTimes(1);
      api.mockRestore();
    }
    const invalid = vi.spyOn(vexaAPI, "getRecordingMasterStreamUrl").mockRejectedValue(new Error("response had no url"));
    await render({ meetingId: "51", recordings: [recording(1, { meetingId: 51 })] });
    await advance(60_000); expect(invalid).toHaveBeenCalledTimes(1); invalid.mockRestore();
    const notReady = vi.spyOn(vexaAPI, "getRecordingMasterStreamUrl").mockResolvedValue(null);
    await render({ meetingId: "404", recordings: [recording(1, { meetingId: 404 })] });
    await advance(10_500);
    expect(notReady).toHaveBeenCalledTimes(4);
    expect(playback.audioResolutionError).toContain("録音の準備");
    notReady.mockRestore();
    const noUrl = vi.spyOn(vexaAPI, "getRecordingMasterStreamUrl");
    await render({ meetingId: "52", recordings: [recording(1, { audio: false, meetingId: 52 })] });
    await advance(60_000); expect(noUrl).not.toHaveBeenCalled();
  });

  it("R07 preserves playlist order and seek contract", async () => {
    const api = vi.spyOn(vexaAPI, "getRecordingMasterStreamUrl").mockImplementation(async (id) => id === 2 ? null : ({ url: `/${id}`, duration_seconds: id === 1 ? 12 : 20 }));
    const first = recording(1, { createdAt: "2026-01-01T00:00:00Z", session: "session-a" });
    const missing = recording(2, { createdAt: "2026-01-01T00:05:00Z", session: "missing" });
    const second = recording(3, { createdAt: "2026-01-01T00:10:00Z", session: "session-b" });
    const segment = transcript("session-b");
    await render({ meetingId: "42", recordings: [second, missing, first], transcripts: [segment] });
    expect(playback.recordingFragments.map((fragment) => fragment.src)).toEqual(["/1", "/3"]);
    expect(playback.recordingFragments.map((fragment) => fragment.duration)).toEqual([12, 20]);
    const seekToFragment = vi.fn();
    (playback.audioPlayerRef as { current: { seekToFragment: typeof seekToFragment } | null }).current = { seekToFragment };
    await act(async () => playback.handleSegmentClick(3, segment.absolute_start_time));
    expect(seekToFragment).toHaveBeenCalledWith(1, 3);
    expect(playback.playbackTime).toBe(15);

    api.mockImplementation(async (id) => {
      if (id === 2) throw Object.assign(new Error("bad fragment"), { status: 422 });
      return { url: `/${id}`, duration_seconds: 12 };
    });
    await render({ meetingId: "43", recordings: [
      { ...first, meeting_id: 43 }, { ...missing, meeting_id: 43 }, { ...second, meeting_id: 43 },
    ] });
    expect(playback.recordingFragments).toEqual([]);
    expect(playback.audioResolutionError).toContain("bad fragment");
  });

  it("R07 aborts headers body and retries on cleanup", async () => {
    vi.useFakeTimers();
    const headerSignals: AbortSignal[] = [];
    const headerFetch = vi.fn((_url: string, options: { signal: AbortSignal }) => {
      headerSignals.push(options.signal);
      return new Promise((_resolve, reject) => options.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError"))));
    });
    vi.stubGlobal("fetch", headerFetch);
    await render({ meetingId: "53", recordings: [recording(1, { meetingId: 53 })] });
    await act(async () => root?.unmount()); root = null;
    expect(headerSignals[0].aborted).toBe(true);
    await advance(60_000);
    expect(headerFetch).toHaveBeenCalledTimes(1);

    const bodySignals: AbortSignal[] = [];
    const bodyFetch = vi.fn(async (_url: string, options: { signal: AbortSignal }) => {
      bodySignals.push(options.signal);
      return {
        status: 200,
        ok: true,
        json: () => new Promise((_resolve, reject) => options.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")))),
      };
    });
    vi.stubGlobal("fetch", bodyFetch);
    await render({ meetingId: "54", recordings: [recording(1, { meetingId: 54 })] });
    await act(async () => root?.unmount()); root = null;
    expect(bodySignals[0].aborted).toBe(true);
    await advance(60_000);
    expect(bodyFetch).toHaveBeenCalledTimes(1);

    const retryFetch = vi.fn(async () => ({ status: 503, ok: false }));
    vi.stubGlobal("fetch", retryFetch);
    await render({ meetingId: "55", recordings: [recording(1, { meetingId: 55 })] });
    expect(retryFetch).toHaveBeenCalledTimes(1);
    await act(async () => root?.unmount()); root = null;
    await advance(60_000);
    expect(retryFetch).toHaveBeenCalledTimes(1);
  });
});
