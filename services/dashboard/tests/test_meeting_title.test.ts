import { describe, expect, it } from "vitest";

import {
  UNTITLED_MEETING_LABEL,
  getCustomMeetingTitle,
  resolveMeetingTitle,
} from "@/lib/meeting-title";
import type { MeetingData } from "@/types/vexa";

describe("resolveMeetingTitle / getCustomMeetingTitle", () => {
  it("手動編集名(name)を最優先で使う", () => {
    const data: MeetingData = {
      name: "手動タイトル",
      title: "APIタイトル",
      calendar_title: "カレンダータイトル",
      calendar_event: { title: "イベントタイトル" },
    };
    expect(getCustomMeetingTitle(data)).toBe("手動タイトル");
    expect(resolveMeetingTitle(data, "abc-defg-hij")).toBe("手動タイトル");
  });

  it("nameが無ければtitleを使う", () => {
    const data: MeetingData = {
      title: "APIタイトル",
      calendar_title: "カレンダータイトル",
    };
    expect(getCustomMeetingTitle(data)).toBe("APIタイトル");
    expect(resolveMeetingTitle(data, "abc-defg-hij")).toBe("APIタイトル");
  });

  it("name/titleが無ければcalendar_titleを使う", () => {
    const data: MeetingData = {
      calendar_title: "カレンダータイトル",
      calendar_event: { title: "イベントタイトル" },
    };
    expect(getCustomMeetingTitle(data)).toBe("カレンダータイトル");
    expect(resolveMeetingTitle(data, "abc-defg-hij")).toBe("カレンダータイトル");
  });

  it("calendar_titleが無ければcalendar_event.titleを使う", () => {
    const data: MeetingData = { calendar_event: { title: "イベントタイトル" } };
    expect(getCustomMeetingTitle(data)).toBe("イベントタイトル");
    expect(resolveMeetingTitle(data, "abc-defg-hij")).toBe("イベントタイトル");
  });

  it("空文字は候補として飛ばし、次の候補へ進む", () => {
    const data: MeetingData = {
      name: "",
      title: "",
      calendar_title: "",
      calendar_event: { title: "イベントタイトル" },
    };
    expect(getCustomMeetingTitle(data)).toBe("イベントタイトル");

    const empty: MeetingData = { name: "", title: "", calendar_title: "" };
    expect(getCustomMeetingTitle(empty)).toBe("");
    expect(resolveMeetingTitle(empty, "abc-defg-hij")).toBe("abc-defg-hij");
  });

  it("タイトル候補が無ければ会議コード、それも無ければ「無題の会議」へ落とす", () => {
    expect(resolveMeetingTitle({}, "abc-defg-hij")).toBe("abc-defg-hij");
    expect(resolveMeetingTitle({}, "")).toBe(UNTITLED_MEETING_LABEL);
    expect(resolveMeetingTitle({}, null)).toBe(UNTITLED_MEETING_LABEL);
    expect(UNTITLED_MEETING_LABEL).toBe("無題の会議");
  });

  it("dataが未定義・nullでも落ちない", () => {
    expect(getCustomMeetingTitle(undefined)).toBe("");
    expect(getCustomMeetingTitle(null)).toBe("");
    expect(resolveMeetingTitle(undefined, "abc-defg-hij")).toBe("abc-defg-hij");
    expect(resolveMeetingTitle(null, undefined)).toBe(UNTITLED_MEETING_LABEL);
  });
});
