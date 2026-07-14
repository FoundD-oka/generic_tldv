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
});
