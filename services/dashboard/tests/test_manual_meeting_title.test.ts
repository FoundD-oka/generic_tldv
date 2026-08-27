import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import {
  MANUAL_MEETING_TITLE_MAX,
  isManualMeetingTitleTooLong,
  normalizeManualMeetingTitle,
} from "@/lib/manual-meeting-title";

const joinModalSource = readFileSync("src/components/join/join-modal.tsx", "utf8");

describe("normalizeManualMeetingTitle", () => {
  it("前後の空白を落として返す", () => {
    expect(normalizeManualMeetingTitle("  週次定例  ")).toBe("週次定例");
  });

  it("空文字はundefinedにする", () => {
    expect(normalizeManualMeetingTitle("")).toBeUndefined();
  });

  it("空白のみもundefinedにする", () => {
    expect(normalizeManualMeetingTitle("   ")).toBeUndefined();
    expect(normalizeManualMeetingTitle("\n\t ")).toBeUndefined();
  });

  it("上限ちょうどの入力を切り詰めない", () => {
    const title = "あ".repeat(MANUAL_MEETING_TITLE_MAX);
    expect(normalizeManualMeetingTitle(title)).toBe(title);
  });

  it("上限超過でも切り詰めない(長さ判定は別関数)", () => {
    const title = "あ".repeat(MANUAL_MEETING_TITLE_MAX + 1);
    expect(normalizeManualMeetingTitle(title)).toBe(title);
  });
});

describe("isManualMeetingTitleTooLong", () => {
  it("上限は200文字", () => {
    expect(MANUAL_MEETING_TITLE_MAX).toBe(200);
  });

  it("200文字は許可する", () => {
    expect(isManualMeetingTitleTooLong("あ".repeat(200))).toBe(false);
  });

  it("201文字は拒否する", () => {
    expect(isManualMeetingTitleTooLong("あ".repeat(201))).toBe(true);
  });

  it("前後空白付きの200文字はtrim後判定で許可する", () => {
    expect(isManualMeetingTitleTooLong(`  ${"あ".repeat(200)}  `)).toBe(false);
  });

  it("空文字・空白のみは長すぎない", () => {
    expect(isManualMeetingTitleTooLong("")).toBe(false);
    expect(isManualMeetingTitleTooLong("   ")).toBe(false);
  });
});

describe("JoinModal のタイトル入力配線", () => {
  it("空タイトルではmeeting_titleキー自体を載せない", () => {
    expect(joinModalSource).toContain("const normalizedTitle = normalizeManualMeetingTitle(meetingTitle);");
    expect(joinModalSource).toContain("if (normalizedTitle) {");
    expect(joinModalSource).toContain("request.meeting_title = normalizedTitle;");
  });

  it("入力欄へmaxLengthを設定している", () => {
    expect(joinModalSource).toContain("maxLength={MANUAL_MEETING_TITLE_MAX}");
  });

  it("長すぎる入力はtoastで止める", () => {
    expect(joinModalSource).toContain("isManualMeetingTitleTooLong(meetingTitle)");
    expect(joinModalSource).toContain("copy.meetingTitleTooLongTitle");
  });
});
