import { useEffect } from "react";

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
  useEffect(() => {
    if (!meetingId || !shouldPollMeetingStatus) return;
    return startImmediateIntervalPolling(
      () => refreshMeeting(meetingId),
      MEETING_STATUS_POLL_INTERVAL_MS
    );
  }, [meetingId, shouldPollMeetingStatus, refreshMeeting]);

  useEffect(() => {
    if (!meetingId || !meetingPlatform || !meetingNativeId) return;
    if (!shouldPollPostMeetingArtifacts) return;

    return startImmediateIntervalPolling(() => {
      refreshMeeting(meetingId);
      fetchTranscripts(meetingPlatform, meetingNativeId, meetingNumericId, { silent: true });
      fetchChatMessages(meetingPlatform, meetingNativeId);
    }, POST_MEETING_ARTIFACT_POLL_INTERVAL_MS);
  }, [
    meetingId,
    meetingPlatform,
    meetingNativeId,
    meetingNumericId,
    shouldPollPostMeetingArtifacts,
    refreshMeeting,
    fetchTranscripts,
    fetchChatMessages,
  ]);
}
