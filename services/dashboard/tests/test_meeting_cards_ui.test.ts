import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const pageSource = readFileSync("src/app/meetings/page.tsx", "utf8");
const cardSource = readFileSync("src/components/meetings/meeting-card.tsx", "utf8");

describe("会議一覧カード", () => {
  it("タイトル解決を共通ヘルパーへ統一する", () => {
    expect(cardSource).toContain('from "@/lib/meeting-title"');
    expect(cardSource).toContain(
      "resolveMeetingTitle(meeting.data, meeting.platform_specific_id)",
    );
    expect(cardSource).toContain("getCustomMeetingTitle(meeting.data)");
    expect(cardSource).toContain("setEditedTitle(customTitle)");
    expect(cardSource).not.toContain("meeting.data?.name");
    expect(cardSource).not.toContain("participantsTitle");
  });

  it("タイトルありのときは会議コードを補助行で表示する", () => {
    expect(cardSource).toContain("hasCustomTitle && meeting.platform_specific_id");
    expect(cardSource).toContain("font-mono");
  });

  it("参加者を最大3名まで表示し、4名以上は「ほかN名」にまとめる", () => {
    expect(cardSource).toContain('participants.slice(0, 3).join(", ")');
    expect(cardSource).toContain("participants.length > 3");
    expect(cardSource).toContain("ほか${participants.length - 3}名");
  });

  it("開始前の会議でも作成日時を表示する", () => {
    expect(cardSource).toContain("meeting.start_time || meeting.created_at");
    expect(cardSource).toContain("parseUTCTimestamp(timeSource)");
  });

  it("年付きの絶対日付と相対日時を併記する", () => {
    expect(cardSource).toContain('"yyyy年M月d日"');
    expect(cardSource).toContain(
      "formatDistanceToNow(parseUTCTimestamp(timeSource), { addSuffix: true, locale: ja })",
    );
  });

  it("タイトルクリックは詳細遷移、編集は専用の鉛筆ボタンだけにする", () => {
    expect(cardSource).toContain('aria-label="タイトルを編集"');
    expect(cardSource).not.toContain("<button");
    expect(cardSource).toContain("isEditingTitle && event.preventDefault()");
  });

  it("メモがある会議にはメモインジケータと先頭100文字のツールチップを出す", () => {
    expect(cardSource).toContain("<span>メモ</span>");
    expect(cardSource).toContain("meeting.data.notes.substring(0, 100)");
  });

  it("1列から5列までのレスポンシブgridを維持する", () => {
    expect(pageSource).toContain(
      "grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5",
    );
  });

  it("一覧ページからカードへ会議データだけを渡す", () => {
    expect(pageSource).toContain("<MeetingCard meeting={meeting} />");
  });
});
