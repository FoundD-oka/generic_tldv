// @vitest-environment jsdom

import { act, createRef } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AudioPlayer, type AudioFragment, type AudioPlayerHandle } from "@/components/recording/audio-player";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let container: HTMLDivElement | null = null;
let loadMock = vi.fn<() => void>();
let playMock = vi.fn<() => Promise<void>>();

async function renderPlayer(props: React.ComponentProps<typeof AudioPlayer>) {
  if (!root) {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  }
  await act(async () => {
    root?.render(<AudioPlayer {...props} />);
    await Promise.resolve();
  });
  return container!.querySelector("audio") as HTMLAudioElement;
}

async function fire(audio: HTMLAudioElement, type: string) {
  await act(async () => {
    audio.dispatchEvent(new Event(type));
  });
}

async function advance(ms = 1500) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  loadMock = vi.fn<() => void>();
  playMock = vi.fn<() => Promise<void>>().mockResolvedValue(undefined);
  vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(loadMock);
  vi.spyOn(HTMLMediaElement.prototype, "play").mockImplementation(playMock);
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
});

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  container?.remove();
  root = null;
  container = null;
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("R08 audio retry lifecycle", () => {
  it("R08 retries three times then reaches terminal error", async () => {
    const audio = await renderPlayer({ src: "/audio-a.wav" });

    for (let attempt = 0; attempt < 3; attempt += 1) {
      await fire(audio, "error");
      await advance();
    }
    await fire(audio, "error");
    await advance(60_000);

    expect(loadMock).toHaveBeenCalledTimes(3);
    expect(container?.textContent).toContain("音声の読み込みに失敗しました");
    expect(Array.from(container!.querySelectorAll("button")).some((button) => button.textContent === "再試行")).toBe(true);
    expect(container!.querySelector(".animate-spin")).toBeNull();
    expect((container!.querySelector("button") as HTMLButtonElement).disabled).toBe(false);
  });

  it("R08 repeated error events reserve one timer", async () => {
    const audio = await renderPlayer({ src: "/audio-a.wav" });

    for (let index = 0; index < 10; index += 1) await fire(audio, "error");

    expect(vi.getTimerCount()).toBe(1);
    await advance();
    expect(loadMock).toHaveBeenCalledTimes(1);
  });

  it("R08 source change and unmount cancel old retry", async () => {
    const audio = await renderPlayer({ src: "/audio-a.wav" });
    await fire(audio, "error");

    await renderPlayer({ src: "/audio-b.wav" });
    expect(loadMock).toHaveBeenCalledTimes(1);
    await advance();
    expect(loadMock).toHaveBeenCalledTimes(1);

    await fire(audio, "error");
    await act(async () => root?.unmount());
    root = null;
    expect(vi.getTimerCount()).toBe(0);
    await advance();
    expect(loadMock).toHaveBeenCalledTimes(1);
  });

  it("R08 manual retry and canplay reset budget", async () => {
    const audio = await renderPlayer({ src: "/audio-a.wav" });
    for (let attempt = 0; attempt < 3; attempt += 1) {
      await fire(audio, "error");
      await advance();
    }
    await fire(audio, "error");

    const retry = Array.from(container!.querySelectorAll("button")).find((button) => button.textContent === "再試行")!;
    await act(async () => retry.click());
    expect(loadMock).toHaveBeenCalledTimes(4);
    await fire(audio, "canplay");
    expect(container?.textContent).not.toContain("音声の読み込みに失敗しました");

    for (let attempt = 0; attempt < 3; attempt += 1) {
      await fire(audio, "error");
      await advance();
    }
    await fire(audio, "error");
    await advance(60_000);
    expect(loadMock).toHaveBeenCalledTimes(7);
  });

  it("R08 metadata and fragment seeking remain intact", async () => {
    vi.spyOn(HTMLMediaElement.prototype, "readyState", "get").mockReturnValue(HTMLMediaElement.HAVE_METADATA);
    vi.spyOn(HTMLMediaElement.prototype, "duration", "get").mockReturnValue(12);
    const playerRef = createRef<AudioPlayerHandle>();
    const onTimeUpdate = vi.fn();
    const onFragmentChange = vi.fn();
    const fragments: AudioFragment[] = [
      { src: "/one.wav", duration: 10, sessionUid: "one", createdAt: "2026-01-01T00:00:00Z" },
      { src: "/two.wav", duration: 20, sessionUid: "two", createdAt: "2026-01-01T00:01:00Z" },
    ];
    const audio = await renderPlayer({ ref: playerRef, fragments, onTimeUpdate, onFragmentChange });
    await act(async () => await Promise.resolve());

    expect(container?.textContent).toContain("0:32");
    await act(async () => playerRef.current?.seekToFragment(0, 3));
    expect(audio.currentTime).toBe(3);
    await fire(audio, "timeupdate");
    expect(onTimeUpdate).toHaveBeenLastCalledWith(3);

    await fire(audio, "ended");
    expect(onFragmentChange).toHaveBeenCalledWith(1);
    expect(audio.getAttribute("src")).toBe("/two.wav");
    await fire(audio, "loadedmetadata");
    expect(audio.currentTime).toBe(0);
    expect(playMock).toHaveBeenCalled();
  });
});
