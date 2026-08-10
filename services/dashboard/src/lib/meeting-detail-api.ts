import { withBasePath } from "@/lib/base-path";

export async function downloadRecordingInChunks(sourceUrl: string, fallbackContentType: string, onProgress: (progress: number) => void): Promise<Blob> {
  const probe = await fetch(sourceUrl, { headers: { Range: "bytes=0-0" }, cache: "no-store" });
  if (probe.status !== 206) throw new Error(`Unexpected audio probe response: ${probe.status}`);
  const totalMatch = (probe.headers.get("content-range") || "").match(/\/(\d+)$/);
  const totalBytes = totalMatch ? Number(totalMatch[1]) : 0;
  const contentType = probe.headers.get("content-type") || fallbackContentType;
  await probe.arrayBuffer();
  if (!Number.isFinite(totalBytes) || totalBytes <= 0) throw new Error("Audio size is unavailable");
  const chunks: BlobPart[] = [];
  const chunkSize = 8 * 1024 * 1024;
  for (let start = 0; start < totalBytes; start += chunkSize) {
    const end = Math.min(start + chunkSize - 1, totalBytes - 1);
    const response = await fetch(sourceUrl, { headers: { Range: `bytes=${start}-${end}` }, cache: "no-store" });
    if (response.status !== 206) throw new Error(`Audio chunk request failed: ${response.status}`);
    chunks.push(await response.blob());
    onProgress(Math.round(((end + 1) / totalBytes) * 100));
  }
  return new Blob(chunks, { type: contentType });
}

export function mp3MasterUrl(recordingId: number): string {
  return withBasePath(`/api/vexa/recordings/${recordingId}/master/mp3?type=audio`);
}

export async function saveBrowserState(url: string): Promise<void> {
  const response = await fetch(url, { method: "POST" });
  if (!response.ok) throw new Error(await response.text());
}

export async function speakInMeeting(platform: string, nativeId: string, text: string): Promise<void> {
  const response = await fetch(`/api/vexa/bots/${platform}/${nativeId}/speak`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, voice: "alloy" }) });
  if (!response.ok) throw new Error(await response.text());
}

export async function stopSpeakingInMeeting(platform: string, nativeId: string): Promise<void> {
  await fetch(`/api/vexa/bots/${platform}/${nativeId}/speak`, { method: "DELETE" });
}
