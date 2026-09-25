import { useEffect, useRef } from "react";

import { startSingleFlightPolling } from "@/lib/single-flight-polling";

import type { Meeting } from "@/types/vexa";

export const MEETING_STATUS_POLL_INTERVAL_MS = 5000;
export const POST_MEETING_ARTIFACT_POLL_INTERVAL_MS = 2500;

type PollTask = () => void | Promise<unknown>;

export function startImmediateIntervalPolling(
  task: PollTask,
  intervalMs: number
): () => void {
  let cancelled = false;
  const run = (): void => {
    if (cancelled) return;
    void task();
  };

  run();
  const interval = globalThis.setInterval(run, intervalMs);
  return (): void => {
    cancelled = true;
    globalThis.clearInterval(interval);
  };
}

type FetchTranscripts = (
  platform: Meeting["platform"],
  nativeId: string,
  meetingId?: string,
  options?: { silent?: boolean }
) => void | Promise<unknown>;

export type UseMeetingPollingOptions = {
  meetingId: string;
  meetingPlatform: Meeting["platform"] | undefined;
  meetingNativeId: string | undefined;
  meetingNumericId: string | undefined;
  shouldPollMeetingStatus: boolean;
  shouldPollPostMeetingArtifacts: boolean;
  refreshMeeting: (meetingId: string) => void | Promise<unknown>;
  fetchTranscripts: FetchTranscripts;
  fetchChatMessages: (
    platform: Meeting["platform"],
    nativeId: string
  ) => void | Promise<unknown>;
};

export function useMeetingPolling({
  meetingId,
  meetingPlatform,
  meetingNativeId,
  meetingNumericId,
  shouldPollMeetingStatus,
  shouldPollPostMeetingArtifacts,
  refreshMeeting,
  fetchTranscripts,
  fetchChatMessages,
}: UseMeetingPollingOptions): void {
  const flightRef = useRef<{ meetingId: string; token: symbol } | null>(null);

  useEffect(() => {
    const mode = shouldPollPostMeetingArtifacts
      ? "artifacts"
      : shouldPollMeetingStatus
        ? "status"
        : null;
    if (!meetingId || !mode) return;
    if (mode === "artifacts" && (!meetingPlatform || !meetingNativeId)) return;

    const task = async (): Promise<void> => {
      if (flightRef.current?.meetingId === meetingId) return;

      const token = Symbol("meeting-poll-flight");
      flightRef.current = { meetingId, token };
      try {
        if (mode === "artifacts") {
          await Promise.allSettled([
            Promise.resolve().then(() => refreshMeeting(meetingId)),
            Promise.resolve().then(() =>
              fetchTranscripts(meetingPlatform!, meetingNativeId!, meetingNumericId, { silent: true })
            ),
            Promise.resolve().then(() => fetchChatMessages(meetingPlatform!, meetingNativeId!)),
          ]);
        } else {
          await refreshMeeting(meetingId);
        }
      } finally {
        if (flightRef.current?.token === token) flightRef.current = null;
      }
    };

    return startSingleFlightPolling(
      task,
      mode === "artifacts"
        ? POST_MEETING_ARTIFACT_POLL_INTERVAL_MS
        : MEETING_STATUS_POLL_INTERVAL_MS
    );
  }, [
    meetingId,
    meetingPlatform,
    meetingNativeId,
    meetingNumericId,
    shouldPollMeetingStatus,
    shouldPollPostMeetingArtifacts,
    refreshMeeting,
    fetchTranscripts,
    fetchChatMessages,
  ]);
}
