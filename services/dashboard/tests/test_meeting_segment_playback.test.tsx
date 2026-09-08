// @vitest-environment jsdom
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useMeetingPlayback, type MeetingPlayback } from "@/hooks/use-meeting-playback";
import type { RecordingData, TranscriptSegment } from "@/types/vexa";

const getMaster = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ vexaAPI: { getRecordingMasterStreamUrl: getMaster } }));
vi.mock("sonner", () => ({ toast: { error: vi.fn() } }));
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const audio = { seekTo: vi.fn(), seekToFragment: vi.fn() };
const video = { seekTo: vi.fn() };
let playback: MeetingPlayback;
let root: Root;
let container: HTMLDivElement;

const recordings = [1, 2].map(id => ({
  id, session_uid: `session-${id}`, status: "completed", created_at: `2026-09-08T0${id}:00:00Z`,
  playback_url: { audio: `/recording/${id}` },
})) as RecordingData[];
const transcripts = [{
  session_uid: "session-2", start_time: 5, end_time: 8,
  absolute_start_time: "2026-09-08T02:00:05Z",
}] as TranscriptSegment[];

function Harness({ rows }: { rows: RecordingData[] }) {
  const result = useMeetingPlayback(rows, transcripts);
  useEffect(() => {
    playback = result;
    playback.audioPlayerRef.current = audio;
    playback.videoPlayerRef.current = video;
  });
  return null;
}

beforeEach(() => {
  vi.clearAllMocks();
  getMaster.mockImplementation(async (id: number) => ({ url: `/audio/${id}`, duration_seconds: 100 }));
  container = document.createElement("div");
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  vi.useRealTimers();
});

it("passes both segment boundaries to the audio and video players", async () => {
  await act(async () => root.render(<Harness rows={recordings.slice(0, 1)} />));
  await act(async () => playback.handleSegmentClick(5, 8, transcripts[0].absolute_start_time));
  expect(audio.seekTo).toHaveBeenCalledWith(5, 8);
  expect(video.seekTo).toHaveBeenCalledWith(5, 8);
});

it("selects the correct session and translates both video boundaries on a stitched timeline", async () => {
  await act(async () => root.render(<Harness rows={recordings} />));
  await act(async () => playback.handleSegmentClick(5, 8, transcripts[0].absolute_start_time));
  expect(audio.seekToFragment).toHaveBeenCalledWith(1, 5, 8);
  expect(video.seekTo).toHaveBeenCalledWith(105, 108);
});

it("retains the latest segment's end and session when audio is still loading", async () => {
  vi.useFakeTimers();
  const resolve: Array<(value: { url: string; duration_seconds: number }) => void> = [];
  getMaster.mockImplementation(() => new Promise(done => resolve.push(done)));
  await act(async () => root.render(<Harness rows={recordings} />));
  await act(async () => playback.handleSegmentClick(1, 3));
  await act(async () => playback.handleSegmentClick(5, 8, transcripts[0].absolute_start_time));
  expect(audio.seekToFragment).not.toHaveBeenCalled();
  await act(async () => resolve.forEach((done, i) => done({ url: `/audio/${i}`, duration_seconds: 100 })));
  await act(async () => vi.runOnlyPendingTimers());
  expect(audio.seekToFragment).toHaveBeenCalledExactlyOnceWith(1, 5, 8);
  expect(video.seekTo).toHaveBeenCalledExactlyOnceWith(105, 108);
});

it("does not fall back to continuous playback for unknown or reversed segment boundaries", async () => {
  await act(async () => root.render(<Harness rows={recordings} />));
  await act(async () => {
    playback.handleSegmentClick(5, 0);
    playback.handleSegmentClick(5, NaN);
    playback.handleSegmentClick(-1, 5);
  });
  expect(audio.seekTo).not.toHaveBeenCalled();
  expect(audio.seekToFragment).not.toHaveBeenCalled();
  expect(video.seekTo).not.toHaveBeenCalled();
});
