import { describe, expect, it, vi } from "vitest";
import {
  calculateCenteredScrollTop,
  scrollTranscriptItemToCenter,
} from "@/lib/transcript-scroll";

describe("transcript playback scroll", () => {
  it("centers the active item by scrolling only the transcript container", () => {
    const scrollTo = vi.fn();
    const container = {
      clientHeight: 400,
      scrollHeight: 1200,
      scrollTop: 200,
      getBoundingClientRect: () => ({ top: 100 }),
      scrollTo,
    } as unknown as HTMLElement;
    const item = {
      getBoundingClientRect: () => ({ top: 500, height: 50 }),
    } as unknown as HTMLElement;

    scrollTranscriptItemToCenter(container, item);

    expect(scrollTo).toHaveBeenCalledWith({
      top: 425,
      behavior: "smooth",
    });
  });

  it("does not request a negative scroll position for an item near the top", () => {
    const scrollTo = vi.fn();
    const container = {
      clientHeight: 400,
      scrollHeight: 1200,
      scrollTop: 0,
      getBoundingClientRect: () => ({ top: 100 }),
      scrollTo,
    } as unknown as HTMLElement;
    const item = {
      getBoundingClientRect: () => ({ top: 110, height: 40 }),
    } as unknown as HTMLElement;

    scrollTranscriptItemToCenter(container, item, "auto");

    expect(scrollTo).toHaveBeenCalledWith({
      top: 0,
      behavior: "auto",
    });
  });

  it("clamps the requested position to the bottom of the transcript", () => {
    expect(calculateCenteredScrollTop({
      scrollTop: 700,
      scrollHeight: 1200,
      containerTop: 100,
      containerHeight: 400,
      itemTop: 750,
      itemHeight: 50,
    })).toBe(800);
  });

  it("stays at zero when the transcript does not need scrolling", () => {
    expect(calculateCenteredScrollTop({
      scrollTop: 0,
      scrollHeight: 300,
      containerTop: 100,
      containerHeight: 400,
      itemTop: 200,
      itemHeight: 50,
    })).toBe(0);
  });
});
