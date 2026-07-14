import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { canSubmitSelectedAudioVoiceprint } from "@/components/transcript/selected-audio-voiceprint-dialog";
import { normalizeVoiceprintSelectionTiming } from "@/lib/voiceprint-selection";

describe("selected-audio voiceprint enrollment contract", () => {
  it("requires a preview plus both audio review and biometric consent", () => {
    expect(canSubmitSelectedAudioVoiceprint("田中", false, true, true, false)).toBe(false);
    expect(canSubmitSelectedAudioVoiceprint("田中", true, false, true, false)).toBe(false);
    expect(canSubmitSelectedAudioVoiceprint("田中", true, true, false, false)).toBe(false);
    expect(canSubmitSelectedAudioVoiceprint("", true, true, true, false)).toBe(false);
    expect(canSubmitSelectedAudioVoiceprint("田中", true, true, true, true)).toBe(false);
    expect(canSubmitSelectedAudioVoiceprint(" 田中 ", true, true, true, false)).toBe(true);
  });

  it("reuses selected segment ids and removes whole-cluster enrollment offers", () => {
    const source = fs.readFileSync(
      path.resolve("src/components/transcript/transcript-viewer.tsx"),
      "utf8"
    );
    expect(source).toContain("selectedSegmentIds");
    expect(source).toContain("選択した音声で声紋登録");
    expect(source).toContain("previewVoiceprintFromSegments");
    expect(source).toContain("enrollVoiceprintFromSegments");
    expect(source).toContain("selectedAudioPreview.clip_sha256");
    expect(source).toContain("selectedAudioPreview.source_fingerprint");
    expect(source).not.toContain("voiceprintEnrollmentCandidates");
    expect(source).not.toContain("openVoiceprintEnrollment");
    expect(source).not.toContain("次回の話者候補に使う声を登録");

    const apiSource = fs.readFileSync(path.resolve("src/lib/api.ts"), "utf8");
    expect(apiSource).not.toContain("enrollVoiceprintFromCluster");
    expect(fs.existsSync(path.resolve("src/components/transcript/voiceprint-enrollment-dialog.tsx"))).toBe(false);
  });

  it("clamps a negative Vexa start to master audio zero for display and validation", () => {
    const timing = normalizeVoiceprintSelectionTiming([
      { start_time: -0.8, end_time: 12.2 },
    ]);

    expect(timing.ranges).toEqual([{ start: 0, end: 12.2 }]);
    expect(timing.durationSeconds).toBeCloseTo(12.2);
    expect(timing.hasInvalidTiming).toBe(false);
    expect(timing.hasOverlap).toBe(false);

    const overlap = normalizeVoiceprintSelectionTiming([
      { start_time: -0.8, end_time: 5 },
      { start_time: 4, end_time: 9 },
    ]);
    expect(overlap.hasOverlap).toBe(true);

    const nonFinite = normalizeVoiceprintSelectionTiming([
      { start_time: Number.NEGATIVE_INFINITY, end_time: 12.2 },
    ]);
    expect(nonFinite.hasInvalidTiming).toBe(true);
  });

  it("plays the exact preview WAV and shows both confirmation statements", () => {
    const source = fs.readFileSync(
      path.resolve("src/components/transcript/selected-audio-voiceprint-dialog.tsx"),
      "utf8"
    );
    expect(source).toContain("選択した音声だけを使います");
    expect(source).toContain("preview.audio_base64");
    expect(source).toContain("preview?.source_fingerprint");
    expect(source).toContain("key={`${preview?.source_fingerprint}:${preview?.clip_sha256}`}");
    expect(source).toContain("<audio");
    expect(source).toContain("すべて同じ本人の声だけで");
    expect(source).toContain("今後の会議で話者候補を提示するための声紋登録");
    expect(source).toContain("暗号化して保存");
    expect(source).toContain("確認した音声で登録");
  });
});
