// @vitest-environment jsdom
import { act, createRef } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AudioPlayer, type AudioPlayerHandle } from "@/components/recording/audio-player";
import { VideoPlayer, type VideoPlayerHandle } from "@/components/recording/video-player";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root;
let container: HTMLDivElement;
let paused: WeakMap<HTMLMediaElement, boolean>;
const audioRef = createRef<AudioPlayerHandle>();

beforeEach(() => {
  paused = new WeakMap();
  vi.spyOn(HTMLMediaElement.prototype, "paused", "get").mockImplementation(function (this: HTMLMediaElement) { return paused.get(this) ?? true; });
  vi.spyOn(HTMLMediaElement.prototype, "duration", "get").mockReturnValue(100);
  vi.spyOn(HTMLMediaElement.prototype, "readyState", "get").mockReturnValue(4);
  vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => {});
  vi.spyOn(HTMLMediaElement.prototype, "play").mockImplementation(function (this: HTMLMediaElement) {
    paused.set(this, false);
    this.dispatchEvent(new Event("play"));
    this.dispatchEvent(new Event("playing"));
    return Promise.resolve();
  });
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(function (this: HTMLMediaElement) {
    paused.set(this, true);
    this.dispatchEvent(new Event("pause"));
  });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

async function mountAudio() {
  await act(async () => root.render(<AudioPlayer ref={audioRef} src="/audio.webm" />));
  return container.querySelector("audio")!;
}

async function advance(media: HTMLMediaElement, seconds: number) {
  await act(async () => {
    media.currentTime = seconds;
    media.dispatchEvent(new Event("timeupdate"));
  });
}

describe("transcript segment audio playback", () => {
  it("plays from the selected start, then stops at that segment's end", async () => {
    const audio = await mountAudio();
    await act(async () => audioRef.current!.seekTo(12, 15));
    expect(audio.currentTime).toBe(12);
    expect(audio.paused).toBe(false);
    await advance(audio, 14.9);
    expect(audio.paused).toBe(false);
    await advance(audio, 15.12);
    expect(audio.paused).toBe(true);
    expect(audio.currentTime).toBe(15);
  });

  it("replaces the old boundary when another segment or the same segment is clicked", async () => {
    const audio = await mountAudio();
    await act(async () => audioRef.current!.seekTo(12, 15));
    await act(async () => audioRef.current!.seekTo(20, 24));
    await advance(audio, 22);
    expect(audio.paused).toBe(false);
    await advance(audio, 24);
    expect(audio.paused).toBe(true);
    await act(async () => audioRef.current!.seekTo(20, 24));
    expect(audio.currentTime).toBe(20);
    expect(audio.paused).toBe(false);
  });

  it("the top play button resumes unrestricted playback after a segment stops", async () => {
    const audio = await mountAudio();
    await act(async () => audioRef.current!.seekTo(12, 15));
    await advance(audio, 15);
    await act(async () => container.querySelector("button")!.click());
    await advance(audio, 16);
    expect(audio.paused).toBe(false);
  });

  it("the top seek bar removes the segment boundary during playback", async () => {
    const audio = await mountAudio();
    await act(async () => audioRef.current!.seekTo(12, 15));
    const slider = container.querySelector("input[type=range]") as HTMLInputElement;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(slider, "40");
      slider.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(audio.currentTime).toBe(40);
    await advance(audio, 41);
    expect(audio.paused).toBe(false);
  });

  it("uses actual media progress after buffering, and also stops between timeupdate events", async () => {
    const audio = await mountAudio();
    vi.useFakeTimers();
    await act(async () => audioRef.current!.seekTo(12, 15));
    await act(async () => audio.dispatchEvent(new Event("waiting")));
    await act(async () => vi.advanceTimersByTime(5000));
    expect(audio.paused).toBe(false);
    expect(audio.currentTime).toBe(12);
    await act(async () => audio.dispatchEvent(new Event("playing")));
    audio.currentTime = 15;
    await act(async () => vi.advanceTimersByTime(3000));
    expect(audio.paused).toBe(true);
  });

  it("does not advance into another recording fragment at the end of a segment", async () => {
    const fragments = ["one", "two"].map(name => ({ src: `/${name}.webm`, duration: 100, sessionUid: name, createdAt: "" }));
    await act(async () => root.render(<AudioPlayer ref={audioRef} fragments={fragments} />));
    const audio = container.querySelector("audio")!;
    await act(async () => audioRef.current!.seekToFragment(0, 95, 100));
    await act(async () => audio.dispatchEvent(new Event("ended")));
    expect(audio.getAttribute("src")).toBe("/one.webm");
    await act(async () => audioRef.current!.seekToFragment(1, 10, 12));
    await act(async () => audio.dispatchEvent(new Event("loadedmetadata")));
    expect(audio.currentTime).toBe(10);
    expect(audio.getAttribute("src")).toBe("/two.webm");
    await advance(audio, 12);
    expect(audio.paused).toBe(true);
  });

  it("keeps automatic fragment advance for unrestricted playback", async () => {
    const fragments = ["one", "two"].map(name => ({ src: `/${name}.webm`, duration: 100, sessionUid: name, createdAt: "" }));
    await act(async () => root.render(<AudioPlayer ref={audioRef} fragments={fragments} />));
    const audio = container.querySelector("audio")!;
    await act(async () => audioRef.current!.seekTo(95));
    await act(async () => audio.dispatchEvent(new Event("ended")));
    expect(audio.getAttribute("src")).toBe("/two.webm");
  });

  it("rejects an invalid segment end instead of starting unrestricted playback", async () => {
    const audio = await mountAudio();
    await act(async () => audioRef.current!.seekTo(12, NaN));
    expect(audio.paused).toBe(true);
    await act(async () => audioRef.current!.seekTo(12, 11));
    expect(audio.paused).toBe(true);
  });
});

it("stops the accompanying video at the same boundary and allows top-level resume", async () => {
  const ref = createRef<VideoPlayerHandle>();
  await act(async () => root.render(<VideoPlayer ref={ref} src="/video.webm" />));
  const video = container.querySelector("video")!;
  await act(async () => video.dispatchEvent(new Event("loadedmetadata")));
  await act(async () => ref.current!.seekTo(12, 15));
  await advance(video, 15.1);
  expect(video.paused).toBe(true);
  expect(video.currentTime).toBe(15);
  await act(async () => container.querySelector("button")!.click());
  await advance(video, 16);
  expect(video.paused).toBe(false);
});
