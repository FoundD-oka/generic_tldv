export type VoiceprintPreviewPhase = "idle" | "preparing" | "ready" | "failed";
export type VoiceprintPreviewEvent = "reset" | "recording_stopped" | "loaded" | "error" | "timeout";

export function nextVoiceprintPreviewPhase(
  _current: VoiceprintPreviewPhase,
  event: VoiceprintPreviewEvent
): VoiceprintPreviewPhase {
  if (event === "recording_stopped") return "preparing";
  if (event === "loaded") return "ready";
  if (event === "error" || event === "timeout") return "failed";
  return "idle";
}
