import fs from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

import { vexaAPI } from "@/lib/api";
import {
  canEnrollRecordedVoiceprint,
  selectVoiceprintRecorderMimeType,
  voiceprintMediaFormatFromMimeType,
} from "@/lib/voiceprint-recording";

describe("voiceprint pre-enrollment recording", () => {
  afterEach(() => vi.restoreAllMocks());

  it("requires a 5-30 second reviewed recording, consent, and a name", () => {
    const valid = {
      displayName: "田中",
      hasRecording: true,
      previewReady: true,
      durationSeconds: 8,
      audioReviewConfirmed: true,
      consentConfirmed: true,
      submitting: false,
    };
    expect(canEnrollRecordedVoiceprint(valid)).toBe(true);
    expect(canEnrollRecordedVoiceprint({ ...valid, durationSeconds: 4.9 })).toBe(false);
    expect(canEnrollRecordedVoiceprint({ ...valid, durationSeconds: 30.1 })).toBe(false);
    expect(canEnrollRecordedVoiceprint({ ...valid, audioReviewConfirmed: false })).toBe(false);
    expect(canEnrollRecordedVoiceprint({ ...valid, consentConfirmed: false })).toBe(false);
    expect(canEnrollRecordedVoiceprint({ ...valid, displayName: " " })).toBe(false);
    expect(canEnrollRecordedVoiceprint({ ...valid, previewReady: false })).toBe(false);
  });

  it("chooses a supported recorder format and maps it for the API", () => {
    expect(selectVoiceprintRecorderMimeType((mime) => mime === "audio/ogg;codecs=opus"))
      .toBe("audio/ogg;codecs=opus");
    expect(voiceprintMediaFormatFromMimeType("audio/ogg;codecs=opus")).toBe("ogg");
    expect(voiceprintMediaFormatFromMimeType("audio/mp4")).toBe("mp4");
    expect(voiceprintMediaFormatFromMimeType("audio/webm;codecs=opus")).toBe("webm");
  });

  it("shows exact review, consent, retention, and future-matching copy", () => {
    const source = fs.readFileSync(path.resolve("src/app/voiceprints/page.tsx"), "utf8");
    expect(source).toContain("登録する本人の声だけ");
    expect(source).toContain("本人から、今後の会議で話者候補を提示するための声紋登録に同意");
    expect(source).toContain("録音そのものは保存せず");
    expect(source).toContain("次のGemini会議後文字起こしから");
    expect(source).toContain("enrollVoiceprintFromAudio");
  });

  it("stops a late microphone permission result after unmount and fences double starts", () => {
    const source = fs.readFileSync(path.resolve("src/app/voiceprints/page.tsx"), "utf8");
    expect(source).toContain("recordingStartPendingRef.current");
    expect(source).toContain("recordingStartRunRef.current !== startRun");
    expect(source).toContain("stream.getTracks().forEach((track) => track.stop())");
    expect(source).toContain("disabled={isSubmitting || isStarting || isPreparingPreview}");
  });

  it("blocks interaction while confirmation playback is prepared and always releases the overlay", () => {
    const source = fs.readFileSync(path.resolve("src/app/voiceprints/page.tsx"), "utf8");
    expect(source).toContain("isPreparingPreview");
    expect(source).toContain("確認再生を準備しています");
    expect(source).toContain("fixed inset-0 z-[100]");
    expect(source).toContain("HTMLMediaElement.HAVE_METADATA");
    expect(source).toContain("10_000");
    expect(source).toContain("onLoadedMetadata={finishPreparingPreview}");
    expect(source).toContain("onError={failPreparingPreview}");
    expect(source).toContain("inert={isPreparingPreview ? true : undefined}");
    expect(source).toContain('aria-modal="true"');
    expect(source).toContain("!isPreviewReady");
  });

  it("sends only the reviewed recording fields and both confirmations", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        profile_id: 7,
        display_name: "田中",
        voiceprint_id: 8,
        consent_id: 9,
      }),
    }));

    await vexaAPI.enrollVoiceprintFromAudio({
      displayName: "田中",
      audioBase64: "UklGRg==",
      mediaFormat: "webm",
    });

    expect(fetch).toHaveBeenCalledWith("/api/vexa/voiceprints/enroll-from-audio", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        display_name: "田中",
        audio_base64: "UklGRg==",
        media_format: "webm",
        audio_review_confirmed: true,
        consent_confirmed: true,
      }),
    });
  });
});
