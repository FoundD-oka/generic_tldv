import { describe, expect, it, vi } from "vitest";

// タイムスタンプのパース失敗を再現するため、parseUTCTimestamp だけ差し替える。
vi.mock("@/lib/utils", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/utils")>();
  return {
    ...actual,
    parseUTCTimestamp: (timestamp: string) => {
      if (timestamp === "broken-timestamp") throw new RangeError("invalid timestamp");
      return actual.parseUTCTimestamp(timestamp);
    },
  };
});

import {
  formatTranscriptForProvider,
  recordingDownloadBaseName,
} from "@/hooks/use-meeting-actions";
import type { Meeting, TranscriptSegment } from "@/types/vexa";

const segment = (overrides: Partial<TranscriptSegment>): TranscriptSegment =>
  ({
    speaker: "話者A",
    text: "こんにちは",
    start_time: 0,
    ...overrides,
  }) as TranscriptSegment;

const meeting = (): Meeting => ({ data: {} }) as Meeting;

describe("録音ダウンロードのファイル名サニタイズ", () => {
  it("バックスラッシュを含むタイトルを置換する", () => {
    const name = recordingDownloadBaseName("2026\\05\\01 定例");
    expect(name).toBe("2026-05-01_定例");
    expect(name).not.toContain("\\");
  });

  it("バックスラッシュと他の禁止文字の連続をまとめて1つに置換する", () => {
    expect(recordingDownloadBaseName('a\\/b:c*d?e"f<g>h|i')).toBe("a-b-c-d-e-f-g-h-i");
  });

  it("サニタイズ後に空になる場合は recording へフォールバックする", () => {
    expect(recordingDownloadBaseName("   ")).toBe("recording");
    expect(recordingDownloadBaseName("")).toBe("recording");
  });
});

describe("formatTranscriptForProvider のタイムスタンプ整形", () => {
  it("パースに失敗しても例外を投げず生の文字列へフォールバックする", () => {
    const segments = [segment({ absolute_start_time: "broken-timestamp" })];
    let output = "";
    expect(() => {
      output = formatTranscriptForProvider(meeting(), segments);
    }).not.toThrow();
    expect(output).toContain("[broken-timestamp] 話者A: こんにちは");
  });

  it("不正なセグメントがあっても後続のセグメントを整形し続ける", () => {
    const output = formatTranscriptForProvider(meeting(), [
      segment({ absolute_start_time: "broken-timestamp" }),
      segment({ absolute_start_time: "2026-05-01T05:32:11", speaker: "話者B", text: "つづき" }),
    ]);
    expect(output).toContain("[broken-timestamp] 話者A: こんにちは");
    expect(output).toContain("[2026-05-01 14:32:11] 話者B: つづき");
  });
});
