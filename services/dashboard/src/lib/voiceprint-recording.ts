export const VOICEPRINT_MIN_RECORDING_SECONDS = 5;
export const VOICEPRINT_MAX_RECORDING_SECONDS = 30;

const RECORDER_MIME_TYPES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/ogg;codecs=opus",
  "audio/mp4",
] as const;

export function selectVoiceprintRecorderMimeType(
  isTypeSupported: ((mimeType: string) => boolean) | undefined
): string | undefined {
  if (!isTypeSupported) return undefined;
  return RECORDER_MIME_TYPES.find((mimeType) => isTypeSupported(mimeType));
}

export function voiceprintMediaFormatFromMimeType(mimeType: string): string {
  const normalized = mimeType.toLowerCase();
  if (normalized.includes("ogg")) return "ogg";
  if (normalized.includes("mp4") || normalized.includes("m4a")) return "mp4";
  if (normalized.includes("wav")) return "wav";
  return "webm";
}

export function canEnrollRecordedVoiceprint(input: {
  displayName: string;
  hasRecording: boolean;
  durationSeconds: number;
  audioReviewConfirmed: boolean;
  consentConfirmed: boolean;
  submitting: boolean;
}): boolean {
  return (
    input.displayName.trim().length > 0 &&
    input.hasRecording &&
    input.durationSeconds >= VOICEPRINT_MIN_RECORDING_SECONDS &&
    input.durationSeconds <= VOICEPRINT_MAX_RECORDING_SECONDS &&
    input.audioReviewConfirmed &&
    input.consentConfirmed &&
    !input.submitting
  );
}

export async function blobToBase64(blob: Blob): Promise<string> {
  return await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error || new Error("録音の読み込みに失敗しました"));
    reader.onload = () => {
      const result = String(reader.result || "");
      const separator = result.indexOf(",");
      if (separator < 0) {
        reject(new Error("録音の形式を読み取れませんでした"));
        return;
      }
      resolve(result.slice(separator + 1));
    };
    reader.readAsDataURL(blob);
  });
}
