import type { Meeting, MeetingData } from "@/types/vexa";
import { startSingleFlightPolling } from "@/lib/single-flight-polling";

export type RetranscriptionStatus = "idle" | "queued" | "running" | "succeeded" | "failed";

export function normalizeRetranscriptionStatus(status?: string | null): RetranscriptionStatus {
  if (status === "queued" || status === "running" || status === "succeeded") return status;
  if (status === "completed") return "succeeded";
  if (status === "failed" || status === "unknown_manual_reconcile") return "failed";
  return "idle";
}

export function getRetranscriptionStatus(data?: MeetingData): RetranscriptionStatus {
  return normalizeRetranscriptionStatus(
    data?.final_transcription?.status || data?.final_transcription_status
  );
}

export function isRetranscriptionInProgress(data?: MeetingData): boolean {
  const status = getRetranscriptionStatus(data);
  return status === "queued" || status === "running";
}

export function startRetranscriptionStatusPolling(
  refresh: () => Promise<Meeting | null>,
  intervalMs = 2500
): () => void {
  return startSingleFlightPolling(
    async () => {
      const meeting = await refresh();
      return meeting === null ? null : isRetranscriptionInProgress(meeting.data);
    },
    intervalMs,
    (inProgress) => inProgress !== false
  );
}
