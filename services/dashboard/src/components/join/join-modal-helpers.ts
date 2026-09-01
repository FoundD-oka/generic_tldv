import type { CreateBotRequest, Platform } from "@/types/vexa";

/**
 * Join payload shape. Identical to the shared CreateBotRequest except that
 * native_meeting_id is optional: for white-label / enterprise URLs the parser
 * cannot extract an ID, and sending `""` makes the API reject the request
 * (native_meeting_id cannot be empty) even though (platform + meeting_url) is
 * a documented valid shape. Omitting the key is what we mean.
 */
export type JoinBotRequest = Omit<CreateBotRequest, "native_meeting_id"> & {
  native_meeting_id?: string;
};

export interface JoinBotRequestInput {
  platform: Platform;
  meetingId: string;
  wakeWordEnabled: boolean;
}

export function buildJoinBotRequest({
  platform,
  meetingId,
  wakeWordEnabled,
}: JoinBotRequestInput): JoinBotRequest {
  const nativeMeetingId = meetingId.trim();

  return {
    platform,
    ...(nativeMeetingId ? { native_meeting_id: nativeMeetingId } : {}),
    voice_agent_enabled: wakeWordEnabled,
  };
}

/**
 * Pull the human-readable messages out of a FastAPI 422 body
 * (`{ detail: [{ loc, msg, type }, ...] }`).
 *
 * Returns null for anything that isn't that shape, so the caller can fall
 * back to the generic error copy instead of showing a half-parsed message.
 */
export function extractFastApi422Messages(details: unknown): string[] | null {
  if (typeof details !== "object" || details === null) {
    return null;
  }

  const detail = (details as { detail?: unknown }).detail;
  if (!Array.isArray(detail) || detail.length === 0) {
    return null;
  }

  const messages: string[] = [];
  for (const entry of detail) {
    if (typeof entry !== "object" || entry === null) {
      return null;
    }
    const msg = (entry as { msg?: unknown }).msg;
    if (typeof msg !== "string") {
      return null;
    }
    const trimmed = msg.trim();
    if (trimmed) {
      messages.push(trimmed);
    }
  }

  return messages.length > 0 ? messages : null;
}
