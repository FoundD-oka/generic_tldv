import type { MeetingData } from "@/types/vexa";

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
