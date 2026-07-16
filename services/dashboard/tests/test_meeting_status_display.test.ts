import { describe, expect, it } from "vitest";

import { getDetailedStatus } from "@/types/vexa";

describe("getDetailedStatus", () => {
  it.each([
    "stopped",
    "meeting_ended",
    "kicked",
    "removed",
    "awaiting_admission_rejected",
    "unknown_reason",
  ])("shows completed meetings as 完了 regardless of completion reason: %s", (completionReason) => {
    expect(getDetailedStatus("completed", { completion_reason: completionReason })).toMatchObject({
      label: "完了",
      description: "文字起こしが完了しました",
    });
  });

  it("keeps stopping distinct while finalization is still running", () => {
    expect(getDetailedStatus("stopping")).toMatchObject({
      label: "停止中",
    });
  });

  it.each(["queued", "running"])("shows completed meetings as 処理中 during reprocessing: %s", (finalStatus) => {
    expect(getDetailedStatus("completed", {
      final_transcription: { status: finalStatus },
    })).toMatchObject({
      label: "処理中",
      description: "辞書を反映して再文字起こし中です",
    });
  });

  it("shows a reprocessing-specific failure while keeping the existing transcript usable", () => {
    expect(getDetailedStatus("completed", {
      final_transcription: { status: "failed" },
    })).toMatchObject({
      label: "再処理失敗",
      description: "再文字起こしに失敗しました。既存の文字起こしは引き続き確認できます",
    });
  });

  it("treats manual reconciliation as a reprocessing failure after reload", () => {
    expect(getDetailedStatus("completed", {
      final_transcription: { status: "unknown_manual_reconcile" },
    })).toMatchObject({
      label: "再処理失敗",
    });
  });

  it("returns to 完了 after reprocessing succeeds", () => {
    expect(getDetailedStatus("completed", {
      final_transcription: { status: "succeeded" },
    })).toMatchObject({
      label: "完了",
    });
  });
});
