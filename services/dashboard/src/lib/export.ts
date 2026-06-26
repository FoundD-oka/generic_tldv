import type { Meeting, TranscriptSegment } from "@/types/vexa";
import { format } from "date-fns";
import { ja } from "date-fns/locale";
import { parseUTCTimestamp } from "@/lib/utils";

// Format seconds to HH:MM:SS
function formatTimestamp(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);

  if (hours > 0) {
    return `${hours.toString().padStart(2, "0")}:${minutes.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
  }
  return `${minutes.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
}

// Format for SRT timestamps (HH:MM:SS,mmm)
function formatSrtTime(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);
  const ms = Math.floor((seconds % 1) * 1000);

  return `${hours.toString().padStart(2, "0")}:${minutes.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")},${ms.toString().padStart(3, "0")}`;
}

function formatPlatform(platform: Meeting["platform"]): string {
  switch (platform) {
    case "google_meet":
      return "Google Meet";
    case "teams":
      return "Microsoft Teams";
    case "zoom":
      return "Zoom";
    case "browser_session":
      return "ブラウザセッション";
    default:
      return platform;
  }
}

// Calculate meeting duration
function formatDuration(startTime: string | null, endTime: string | null): string {
  if (!startTime || !endTime) return "所要時間不明";

  const start = parseUTCTimestamp(startTime);
  const end = parseUTCTimestamp(endTime);
  const durationMs = end.getTime() - start.getTime();
  const minutes = Math.floor(durationMs / 60000);

  if (minutes < 60) {
    return `${minutes}分`;
  }
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return `${hours}時間 ${remainingMinutes}分`;
}

export function exportToTxt(meeting: Meeting, segments: TranscriptSegment[]): string {
  let output = "=".repeat(60) + "\n";
  output += `会議文字起こし\n`;
  output += "=".repeat(60) + "\n\n";

  output += `会議ID: ${meeting.platform_specific_id}\n`;
  output += `プラットフォーム: ${formatPlatform(meeting.platform)}\n`;

  if (meeting.start_time) {
    output += `日時: ${format(parseUTCTimestamp(meeting.start_time), "yyyy年M月d日 HH:mm", { locale: ja })}\n`;
  }

  if (meeting.start_time && meeting.end_time) {
    output += `所要時間: ${formatDuration(meeting.start_time, meeting.end_time)}\n`;
  }

  if (meeting.data?.participants?.length) {
    output += `参加者: ${meeting.data.participants.join(", ")}\n`;
  }

  output += "\n" + "-".repeat(60) + "\n";
  output += "文字起こし\n";
  output += "-".repeat(60) + "\n\n";

  for (const segment of segments) {
    const time = formatTimestamp(segment.start_time);
    output += `[${time}] ${segment.speaker}:\n`;
    output += `${segment.text}\n\n`;
  }

  output += "\n" + "=".repeat(60) + "\n";
  output += `エクスポート日時: ${format(new Date(), "yyyy年M月d日 HH:mm", { locale: ja })}\n`;
  output += `生成元: カボス ダッシュボード\n`;
  output += "=".repeat(60) + "\n";

  return output;
}

export function exportToJson(meeting: Meeting, segments: TranscriptSegment[]): string {
  const exportData = {
    meeting: {
      id: meeting.id,
      platform: meeting.platform,
      platform_specific_id: meeting.platform_specific_id,
      status: meeting.status,
      start_time: meeting.start_time,
      end_time: meeting.end_time,
      participants: meeting.data?.participants || [],
      languages: meeting.data?.languages || [],
    },
    segments: segments.map((s) => ({
      speaker: s.speaker,
      text: s.text,
      start_time: s.start_time,
      end_time: s.end_time,
      absolute_start_time: s.absolute_start_time,
      absolute_end_time: s.absolute_end_time,
      language: s.language,
    })),
    exported_at: new Date().toISOString(),
  };

  return JSON.stringify(exportData, null, 2);
}

export function exportToSrt(segments: TranscriptSegment[]): string {
  return segments
    .map((segment, index) => {
      const start = formatSrtTime(segment.start_time);
      const end = formatSrtTime(segment.end_time);
      return `${index + 1}\n${start} --> ${end}\n${segment.speaker}: ${segment.text}\n`;
    })
    .join("\n");
}

export function exportToVtt(segments: TranscriptSegment[]): string {
  let output = "WEBVTT\n\n";

  output += segments
    .map((segment, index) => {
      const start = formatTimestamp(segment.start_time) + ".000";
      const end = formatTimestamp(segment.end_time) + ".000";
      return `${index + 1}\n${start} --> ${end}\n${segment.speaker}: ${segment.text}\n`;
    })
    .join("\n");

  return output;
}

// Download helper
export function downloadFile(content: string, filename: string, mimeType: string): void {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

export function generateFilename(meeting: Meeting, extension: string): string {
  const date = meeting.start_time
    ? format(parseUTCTimestamp(meeting.start_time), "yyyy-MM-dd")
    : format(new Date(), "yyyy-MM-dd");
  const id = meeting.platform_specific_id.replace(/[^a-zA-Z0-9]/g, "-");
  return `transcript-${date}-${id}.${extension}`;
}
