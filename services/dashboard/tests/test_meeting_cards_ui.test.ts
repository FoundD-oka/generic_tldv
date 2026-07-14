import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const pageSource = readFileSync("src/app/meetings/page.tsx", "utf8");
const cardSource = readFileSync("src/components/meetings/meeting-card.tsx", "utf8");

describe("会議一覧カード", () => {
  it("参加者タイトルの後に会議コードを識別用fallbackとして使う", () => {
    expect(cardSource).toContain(
      'rawTitle || participantsTitle || meeting.platform_specific_id || "無題の会議"',
    );
    expect(cardSource).toContain('setEditedTitle(rawTitle || participantsTitle || "")');
  });

  it("開始前の会議でも作成日時を表示する", () => {
    expect(cardSource).toContain("meeting.start_time || meeting.created_at");
    expect(cardSource).toContain("parseUTCTimestamp(timeSource)");
  });

  it("1列から5列までのレスポンシブgridを維持する", () => {
    expect(pageSource).toContain(
      "grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5",
    );
  });

  it("参加者由来タイトルのローカライズ済みtemplateをカードへ渡す", () => {
    expect(pageSource).toContain("participantsTitleTemplate={copy.participantsMeetingTitle}");
  });
});
