import { describe, expect, it, vi } from "vitest";

import { saveMeetingTitle, type SaveMeetingTitleOptions } from "@/lib/save-meeting-title";

function options(
  overrides: Partial<SaveMeetingTitleOptions> = {}
): SaveMeetingTitleOptions {
  return {
    platform: "google_meet",
    nativeId: "abc-defg-hij",
    title: "  Weekly sync  ",
    updateMeetingData: vi.fn().mockResolvedValue(undefined),
    setSaving: vi.fn(),
    onSaved: vi.fn(),
    notifySuccess: vi.fn(),
    notifyError: vi.fn(),
    ...overrides,
  };
}

describe("saveMeetingTitle", () => {
  it("trims the title and sends the existing meeting identity", async () => {
    const updateMeetingData = vi.fn().mockResolvedValue(undefined);
    await saveMeetingTitle(options({ updateMeetingData }));

    expect(updateMeetingData).toHaveBeenCalledWith(
      "google_meet",
      "abc-defg-hij",
      { name: "Weekly sync" }
    );
  });

  it("keeps the success callback and toast ordering", async () => {
    const events: string[] = [];
    const result = await saveMeetingTitle(options({
      setSaving: (saving) => events.push(`saving:${saving}`),
      updateMeetingData: async () => { events.push("update"); },
      onSaved: () => events.push("saved"),
      notifySuccess: (message) => events.push(message),
    }));

    expect(result).toBe(true);
    expect(events).toEqual([
      "saving:true",
      "update",
      "saved",
      "タイトルを更新しました",
      "saving:false",
    ]);
  });

  it("shows only the failure toast when the update fails", async () => {
    const onSaved = vi.fn();
    const notifySuccess = vi.fn();
    const notifyError = vi.fn();
    const result = await saveMeetingTitle(options({
      updateMeetingData: vi.fn().mockRejectedValue(new Error("failed")),
      onSaved,
      notifySuccess,
      notifyError,
    }));

    expect(result).toBe(false);
    expect(onSaved).not.toHaveBeenCalled();
    expect(notifySuccess).not.toHaveBeenCalled();
    expect(notifyError).toHaveBeenCalledWith("タイトルの更新に失敗しました");
  });

  it("always clears the saving state after a failure", async () => {
    const setSaving = vi.fn();
    await saveMeetingTitle(options({
      updateMeetingData: vi.fn().mockRejectedValue(new Error("failed")),
      setSaving,
    }));

    expect(setSaving.mock.calls).toEqual([[true], [false]]);
  });
});
