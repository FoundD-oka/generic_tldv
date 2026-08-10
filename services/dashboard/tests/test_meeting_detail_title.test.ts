import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const pageSource = readFileSync("src/lib/meeting-detail-title.ts", "utf8");

const countOf = (needle: string) => pageSource.split(needle).length - 1;

describe("会議詳細ページのタイトル解決", () => {
  it("一覧と同じ共通ヘルパーを読み込む", () => {
    expect(pageSource).toContain('from "@/lib/meeting-title"');
  });

  it("表示タイトルの解決をヘルパーへ統一する", () => {
    expect(countOf("resolveMeetingTitle(")).toBeGreaterThanOrEqual(4);
    expect(countOf("getCustomMeetingTitle(")).toBeGreaterThanOrEqual(3);
  });

  it("インラインのタイトル解決を残さない", () => {
    expect(pageSource).not.toContain("data?.name ||");
  });

  it("タイトル編集の初期値もヘルパーから取る", () => {
    expect(pageSource).toContain(
      "setEditedTitle(getCustomMeetingTitle(currentMeeting.data))",
    );
  });
});
