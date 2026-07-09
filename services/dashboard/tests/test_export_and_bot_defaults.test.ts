import { describe, expect, it } from "vitest";
import { exportToTxt } from "@/lib/export";
import {
  DEFAULT_BOT_NAME,
  DEFAULT_TRANSCRIPTION_LANGUAGE,
  applyBotCreationDefaults,
  POST_MEETING_AUTO_STOP_TIMEOUT_MS,
  resolveDefaultBotName,
  withPostMeetingAutoStop,
} from "@/lib/bot-create-defaults";
import type { CreateBotRequestWithAutomaticLeave } from "@/lib/bot-create-defaults";
import type { CreateBotRequest, Meeting, TranscriptSegment } from "@/types/vexa";

describe("exportToTxt", () => {
  it("uses a Japanese text export template", () => {
    const meeting: Meeting = {
      id: "42",
      platform: "google_meet",
      platform_specific_id: "abc-defg-hij",
      status: "completed",
      start_time: "2026-06-25T10:00:00Z",
      end_time: "2026-06-25T10:32:00Z",
      bot_container_id: null,
      data: { participants: ["岡田", "佐藤"] },
      created_at: "2026-06-25T10:00:00Z",
    };
    const segments: TranscriptSegment[] = [
      {
        id: "seg-1",
        meeting_id: "42",
        start_time: 3,
        end_time: 8,
        absolute_start_time: "2026-06-25T10:00:03Z",
        absolute_end_time: "2026-06-25T10:00:08Z",
        text: "確認します。",
        speaker: "岡田",
        language: "ja",
        session_uid: "session-1",
        created_at: "2026-06-25T10:00:08Z",
      },
    ];

    const output = exportToTxt(meeting, segments);

    expect(output).toContain("会議文字起こし");
    expect(output).toContain("会議ID: abc-defg-hij");
    expect(output).toContain("日時: 2026年6月25日 19:00");
    expect(output).toContain("所要時間: 32分");
    expect(output).toContain("参加者: 岡田, 佐藤");
    expect(output).toContain("文字起こし");
    expect(output).toContain("[00:03] 岡田:");
    expect(output).toContain("生成元: カボス ダッシュボード");
  });
});

describe("withPostMeetingAutoStop", () => {
  it("adds the dashboard default leave-after-meeting timeout", () => {
    const request = withPostMeetingAutoStop({
      platform: "google_meet",
      native_meeting_id: "abc-defg-hij",
    });

    expect(request.automatic_leave).toEqual({
      max_time_left_alone: POST_MEETING_AUTO_STOP_TIMEOUT_MS,
    });
    expect(request.voice_agent_enabled).toBe(true);
  });

  it("preserves other automatic leave settings", () => {
    const baseRequest: CreateBotRequestWithAutomaticLeave = {
      platform: "zoom",
      native_meeting_id: "123456789",
      automatic_leave: {
        max_wait_for_admission: 300000,
      },
    };
    const request = withPostMeetingAutoStop(baseRequest);

    expect(request.automatic_leave).toEqual({
      max_wait_for_admission: 300000,
      max_time_left_alone: POST_MEETING_AUTO_STOP_TIMEOUT_MS,
    });
  });

  it("preserves an explicit voice agent override", () => {
    const request = withPostMeetingAutoStop({
      platform: "google_meet",
      native_meeting_id: "abc-defg-hij",
      voice_agent_enabled: false,
    });

    expect(request.voice_agent_enabled).toBe(false);
  });
});

describe("applyBotCreationDefaults", () => {
  it("applies Kabosu bot name, Japanese language, and voice agent default", () => {
    const request = applyBotCreationDefaults<CreateBotRequest>({
      platform: "google_meet",
      native_meeting_id: "abc-defg-hij",
    });

    expect(DEFAULT_BOT_NAME).toBe("カボス");
    expect(DEFAULT_TRANSCRIPTION_LANGUAGE).toBe("ja");
    expect(request.bot_name).toBe("カボス");
    expect(request.language).toBe("ja");
    expect(request.voice_agent_enabled).toBe(true);
  });

  it("uses runtime default bot name when provided", () => {
    expect(resolveDefaultBotName({ defaultBotName: "会議カボス" })).toBe("会議カボス");
    expect(applyBotCreationDefaults<CreateBotRequest>({
      platform: "zoom",
      native_meeting_id: "123456789",
    }, { defaultBotName: "会議カボス" }).bot_name).toBe("会議カボス");
  });
});
