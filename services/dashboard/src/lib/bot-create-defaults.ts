import type { CreateBotRequest } from "@/types/vexa";

export const POST_MEETING_AUTO_STOP_TIMEOUT_MS = 1000;
export const DEFAULT_BOT_NAME = "カボス";
export const DEFAULT_TRANSCRIPTION_LANGUAGE = "ja";

export interface BotDefaultConfig {
  defaultBotName?: string | null;
}

export type CreateBotRequestWithAutomaticLeave = CreateBotRequest & {
  automatic_leave?: {
    max_bot_time?: number;
    max_wait_for_admission?: number;
    max_time_left_alone?: number;
    no_one_joined_timeout?: number;
  };
};

export function withPostMeetingAutoStop(
  request: CreateBotRequest
): CreateBotRequestWithAutomaticLeave {
  const automaticLeave = (request as CreateBotRequestWithAutomaticLeave).automatic_leave;

  return {
    ...request,
    voice_agent_enabled: request.voice_agent_enabled ?? true,
    automatic_leave: {
      ...automaticLeave,
      max_time_left_alone: POST_MEETING_AUTO_STOP_TIMEOUT_MS,
    },
  };
}

export function resolveDefaultBotName(config?: BotDefaultConfig | null): string {
  return config?.defaultBotName?.trim() || DEFAULT_BOT_NAME;
}

export function applyBotCreationDefaults<T extends CreateBotRequest>(
  request: T,
  config?: BotDefaultConfig | null
): T {
  return {
    ...request,
    bot_name: request.bot_name?.trim() || resolveDefaultBotName(config),
    language: request.language || DEFAULT_TRANSCRIPTION_LANGUAGE,
    voice_agent_enabled: request.voice_agent_enabled ?? true,
  } as T;
}
