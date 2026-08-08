import { describe, expect, it } from "vitest";

import { getDetailedStatus } from "@/types/vexa";

describe("getDetailedStatus", () => {
  it.each([
    "stopped",
    "meeting_ended",
    "kicked",
    "removed",
    "unknown_reason",
  ])("文字起こしが存在し得る完了は reason に関わらず 完了 と表示する: %s", (completionReason) => {
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

  it("AT-001: 入室が許可されなかった完了扱いの会議を 参加失敗 と表示する", () => {
    const result = getDetailedStatus("completed", {
      completion_reason: "awaiting_admission_timeout",
    });
    expect(result).toMatchObject({
      label: "参加失敗",
      description: "ボットの入室が許可されませんでした。文字起こしはありません",
    });
    expect(result.color).toContain("text-red-600");
    expect(result.label).not.toBe("完了");
    expect(result.description).not.toBe("文字起こしが完了しました");
  });

  it("AT-002: 入室を拒否された完了扱いの会議を 参加失敗 と表示する", () => {
    expect(getDetailedStatus("completed", {
      completion_reason: "awaiting_admission_rejected",
    })).toMatchObject({
      label: "参加失敗",
      description: "ボットの入室が拒否されました。文字起こしはありません",
    });
  });

  it.each([
    ["join_failure", "会議への接続に失敗しました。文字起こしはありません"],
    ["validation_error", "会議情報の検証に失敗しました。文字起こしはありません"],
  ])("AT-003: 接続・検証に失敗した完了扱いの会議を 参加失敗 と表示する: %s", (completionReason, description) => {
    expect(getDetailedStatus("completed", { completion_reason: completionReason })).toMatchObject({
      label: "参加失敗",
      description,
    });
  });

  it("AT-004: 入室前に停止した会議は 完了 のまま説明だけを正直にする", () => {
    expect(getDetailedStatus("completed", {
      completion_reason: "stopped_before_admission",
    })).toMatchObject({
      label: "完了",
      description: "入室前に停止したため、文字起こしはありません",
    });
  });

  it.each(["queued", "running", "failed"])(
    "AT-005: 参加失敗は再文字起こしの状態より優先する: %s",
    (finalStatus) => {
      expect(getDetailedStatus("completed", {
        completion_reason: "awaiting_admission_timeout",
        final_transcription: { status: finalStatus },
      })).toMatchObject({
        label: "参加失敗",
        description: "ボットの入室が許可されませんでした。文字起こしはありません",
      });
    },
  );

  it.each([
    undefined,
    "stopped",
    "meeting_ended",
    "left_alone",
    "evicted",
    "unknown_reason",
  ])("AT-006: 失敗系でない reason は fail-open で 完了 のままにする: %s", (completionReason) => {
    expect(getDetailedStatus("completed", { completion_reason: completionReason })).toMatchObject({
      label: "完了",
      description: "文字起こしが完了しました",
    });
  });
});
