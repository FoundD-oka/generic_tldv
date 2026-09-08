"use client";

import { useCallback, useEffect, useRef, type RefObject } from "react";

/** Keep transcript playback within its end time, including between timeupdate events. */
export function useMediaPlaybackRange(mediaRef: RefObject<HTMLMediaElement | null>) {
  const endRef = useRef<number | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cancelTimer = useCallback(() => {
    if (timerRef.current !== null) clearTimeout(timerRef.current);
    timerRef.current = null;
  }, []);
  const setPlaybackEnd = useCallback((end?: number) => {
    cancelTimer();
    endRef.current = end ?? null;
  }, [cancelTimer]);
  const hasPlaybackEnd = useCallback(() => endRef.current !== null, []);

  useEffect(() => {
    const media = mediaRef.current;
    if (!media) return;
    const checkBoundary = () => {
      cancelTimer();
      const end = endRef.current;
      if (end === null || media.paused || media.seeking) return;
      const remaining = end - media.currentTime;
      if (remaining <= 0) {
        media.pause();
        media.currentTime = end;
        return;
      }
      // A stalled download must not use elapsed wall time as playback progress.
      if (media.readyState >= HTMLMediaElement.HAVE_FUTURE_DATA && media.playbackRate > 0) {
        timerRef.current = setTimeout(checkBoundary, Math.max(10, remaining * 1000 / media.playbackRate));
      }
    };
    const progressEvents = ["playing", "timeupdate", "seeked", "ratechange"];
    const stopEvents = ["pause", "waiting", "ended", "emptied"];
    progressEvents.forEach(event => media.addEventListener(event, checkBoundary));
    stopEvents.forEach(event => media.addEventListener(event, cancelTimer));
    return () => {
      cancelTimer();
      progressEvents.forEach(event => media.removeEventListener(event, checkBoundary));
      stopEvents.forEach(event => media.removeEventListener(event, cancelTimer));
    };
  }, [mediaRef, cancelTimer]);

  return { setPlaybackEnd, hasPlaybackEnd };
}
