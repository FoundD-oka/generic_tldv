"use client";

import { useCallback, useState } from "react";
import { FileText, FileJson } from "lucide-react";
import { BotFailedIndicator } from "@/components/meetings/bot-status-indicator";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { useRouter } from "next/navigation";
import { format } from "date-fns";
import { ja } from "date-fns/locale";
import { toast } from "sonner";
import { vexaAPI } from "@/lib/api";
import { applyBotCreationDefaults, withPostMeetingAutoStop } from "@/lib/bot-create-defaults";
import { exportToJson, exportToSrt, exportToTxt, exportToVtt, downloadFile, generateFilename } from "@/lib/export";
import { getCustomMeetingTitle } from "@/lib/meeting-title";
import { recordingMeetingTitle } from "@/lib/meeting-detail-title";
import { downloadRecordingInChunks, mp3MasterUrl } from "@/lib/meeting-detail-api";
import { parseUTCTimestamp } from "@/lib/utils";
import { useMeetingsStore } from "@/stores/meetings-store";
import type { CreateBotRequest, Meeting, TranscriptSegment } from "@/types/vexa";

export type MeetingActionsOptions = {
  meeting: Meeting | null;
  meetingId: string;
  transcripts: TranscriptSegment[];
  chatgptPrompt: string;
  recordingDownloadTarget: { recordingId: number; webmUrl: string } | null;
  refreshMeeting: (id: string) => Promise<unknown>;
  fetchTranscripts: (platform: Meeting["platform"], nativeId: string, meetingId?: string, options?: { silent?: boolean }) => Promise<void>;
  fetchChatMessages: (platform: Meeting["platform"], nativeId: string) => Promise<void>;
  updateMeetingStatus: (id: string, status: Meeting["status"]) => void;
  updateMeetingData: (platform: Meeting["platform"], nativeId: string, data: Record<string, unknown>) => Promise<void>;
  setCurrentLanguage: (language: string) => void;
  deleteMeeting: (platform: Meeting["platform"], nativeId: string, id?: string) => Promise<void>;
};

export function formatTranscriptForProvider(meeting: Meeting, segments: TranscriptSegment[]): string {
  let output = "会議文字起こし\n\n";
  const title = getCustomMeetingTitle(meeting.data);
  if (title) output += `タイトル: ${title}\n`;
  if (meeting.start_time) output += `日時: ${format(parseUTCTimestamp(meeting.start_time), "yyyy年M月d日 HH:mm", { locale: ja })}\n`;
  if (meeting.data?.participants?.length) output += `参加者: ${meeting.data.participants.join(", ")}\n`;
  output += "\n---\n\n";
  for (const segment of segments) {
    let timestamp = "";
    if (segment.absolute_start_time) {
      const date = parseUTCTimestamp(segment.absolute_start_time);
      timestamp = `${date.getFullYear().toString().padStart(4, "0")}-${(date.getMonth() + 1).toString().padStart(2, "0")}-${date.getDate().toString().padStart(2, "0")} ${date.getHours().toString().padStart(2, "0")}:${date.getMinutes().toString().padStart(2, "0")}:${date.getSeconds().toString().padStart(2, "0")}`;
    } else if (segment.start_time !== undefined) timestamp = `${Math.floor(segment.start_time / 60).toString().padStart(2, "0")}:${Math.floor(segment.start_time % 60).toString().padStart(2, "0")}`;
    output += timestamp ? `[${timestamp}] ${segment.speaker}: ${segment.text}\n\n` : `${segment.speaker}: ${segment.text}\n\n`;
  }
  return output;
}

export function useMeetingActions(options: MeetingActionsOptions) {
  const { meeting: currentMeeting, meetingId, transcripts, chatgptPrompt, recordingDownloadTarget } = options;
  const router = useRouter();
  const [isStoppingBot, setIsStoppingBot] = useState(false);
  const [isRetryingBot, setIsRetryingBot] = useState(false);
  const [isDeletingMeeting, setIsDeletingMeeting] = useState(false);
  const [isUpdatingConfig, setIsUpdatingConfig] = useState(false);
  const [isDownloadingRecording, setIsDownloadingRecording] = useState(false);
  const [forcePostMeetingMode, setForcePostMeetingMode] = useState(false);
  const handleStopBot = useCallback(async () => {
    if (!currentMeeting) return;
    setIsStoppingBot(true);
    try {
      await vexaAPI.stopBot(currentMeeting.platform, currentMeeting.platform_specific_id);
      setForcePostMeetingMode(true);
      options.updateMeetingStatus(String(currentMeeting.id), "stopping");
      void options.fetchTranscripts(currentMeeting.platform, currentMeeting.platform_specific_id, String(currentMeeting.id), { silent: true });
      toast.success("ボットを停止しました", { description: "文字起こしを停止しました。" });
      void options.refreshMeeting(meetingId);
    } catch (error) {
      await options.refreshMeeting(meetingId);
      const latestMeeting = useMeetingsStore.getState().currentMeeting;
      const latestStatus = latestMeeting && String(latestMeeting.id) === String(currentMeeting.id) ? latestMeeting.status : null;
      if (latestStatus === "stopping" || latestStatus === "completed" || latestStatus === "failed") {
        setForcePostMeetingMode(latestStatus !== "failed");
        if (latestStatus === "stopping") options.updateMeetingStatus(String(currentMeeting.id), "stopping");
        void options.fetchTranscripts(currentMeeting.platform, currentMeeting.platform_specific_id, String(currentMeeting.id), { silent: true });
        void options.fetchChatMessages(currentMeeting.platform, currentMeeting.platform_specific_id);
        toast.success(latestStatus === "stopping" ? "停止処理を確認しました" : "会議は終了済みです", { description: latestStatus === "failed" ? "最新の状態に更新しました。" : "記録画面に切り替えました。" });
        return;
      }
      toast.error("ボットの停止に失敗しました", { description: (error as Error).message });
    } finally { setIsStoppingBot(false); }
  }, [currentMeeting, meetingId, options]);
  const handleLanguageChange = useCallback(async (language: string) => {
    if (!currentMeeting) return;
    setIsUpdatingConfig(true);
    try {
      await vexaAPI.updateBotConfig(currentMeeting.platform, currentMeeting.platform_specific_id, { language: language === "auto" ? undefined : language, task: "transcribe" });
      options.setCurrentLanguage(language);
      options.updateMeetingData(currentMeeting.platform, currentMeeting.platform_specific_id, { languages: [language] });
      toast.success("言語設定を更新しました");
    } catch (error) { toast.error("言語設定の更新に失敗しました", { description: (error as Error).message }); }
    finally { setIsUpdatingConfig(false); }
  }, [currentMeeting, options]);
  const handleRetryBot = useCallback(async () => {
    if (!currentMeeting || isRetryingBot) return;
    setIsRetryingBot(true);
    try {
      const data = currentMeeting.data ?? {};
      const request: CreateBotRequest = { platform: currentMeeting.platform, native_meeting_id: currentMeeting.platform_specific_id };
      if (typeof data.passcode === "string" && data.passcode) request.passcode = data.passcode;
      if (typeof data.meeting_url === "string" && data.meeting_url) request.meeting_url = data.meeting_url;
      if (data.transcribe_enabled === false) request.transcribe_enabled = false;
      const meeting = await vexaAPI.createBot(applyBotCreationDefaults(withPostMeetingAutoStop(request)));
      toast.success("新しいボットをリクエストしました"); router.push(`/meetings/${meeting.id}`);
    } catch (error) { toast.error("ボットの再リクエストに失敗しました", { description: (error as Error).message }); }
    finally { setIsRetryingBot(false); }
  }, [currentMeeting, isRetryingBot, router]);
  const handleDeleteMeeting = useCallback(async () => {
    if (!currentMeeting) return;
    setIsDeletingMeeting(true);
    try { await options.deleteMeeting(currentMeeting.platform, currentMeeting.platform_specific_id, currentMeeting.id); toast.success("会議を削除しました"); router.push("/meetings"); }
    catch (error) { toast.error("会議の削除に失敗しました", { description: (error as Error).message }); }
    finally { setIsDeletingMeeting(false); }
  }, [currentMeeting, options, router]);
  const handleExport = useCallback((kind: "txt" | "json" | "srt" | "vtt") => {
    if (!currentMeeting) { toast.error("会議が選択されていません"); return; }
    if (!transcripts.length) { toast.info("文字起こしはまだありません", { description: "会議が始まり、文字起こしが開始されると表示されます。" }); return; }
    const values = { txt: [exportToTxt(currentMeeting, transcripts), "text/plain"], json: [exportToJson(currentMeeting, transcripts), "application/json"], srt: [exportToSrt(transcripts), "text/plain"], vtt: [exportToVtt(transcripts), "text/vtt"] } as const;
    downloadFile(values[kind][0], generateFilename(currentMeeting, kind), values[kind][1]);
  }, [currentMeeting, transcripts]);
  const handleOpenInProvider = useCallback(async (provider: "chatgpt" | "perplexity") => {
    if (!currentMeeting) { toast.error("会議が選択されていません"); return; }
    if (!transcripts.length) { toast.info("文字起こしはまだありません", { description: "会議が始まり、文字起こしが開始されると表示されます。" }); return; }
    try {
      const share = await vexaAPI.createTranscriptShare(currentMeeting.platform, currentMeeting.platform_specific_id, meetingId);
      const publicBase = process.env.NEXT_PUBLIC_TRANSCRIPT_SHARE_BASE_URL?.replace(/\/$/, "");
      const shareUrl = publicBase && share.share_id ? `${publicBase}/public/transcripts/${share.share_id}.txt` : share.url;
      const prompt = chatgptPrompt.replace(/{url}/g, shareUrl);
      const providerUrl = provider === "chatgpt" ? `https://chatgpt.com/?hints=search&q=${encodeURIComponent(prompt)}` : `https://www.perplexity.ai/search?q=${encodeURIComponent(prompt)}`;
      window.open(providerUrl, "_blank", "noopener,noreferrer");
      return;
    } catch (err) {
      console.error("Failed to create transcript share link:", err);
    }
    try {
      const transcriptText = formatTranscriptForProvider(currentMeeting, transcripts);
      await navigator.clipboard.writeText(transcriptText);
      toast.success("文字起こしをクリップボードにコピーしました", { description: `${provider === "chatgpt" ? "ChatGPT" : "Perplexity"}を開きます。必要に応じて文字起こしを貼り付けてください。` });
      const q = "会議の文字起こしをクリップボードにコピーしました。これから貼り付けるので、その内容について質問できるようにしてください。";
      const providerUrl = provider === "chatgpt" ? `https://chatgpt.com/?hints=search&q=${encodeURIComponent(q)}` : `https://www.perplexity.ai/search?q=${encodeURIComponent(q)}`;
      setTimeout(() => window.open(providerUrl, "_blank", "noopener,noreferrer"), 100);
    } catch {
      toast.error("文字起こしのコピーに失敗しました", { description: "もう一度試すか、手動でコピーしてください。" });
    }
  }, [currentMeeting, transcripts, meetingId, chatgptPrompt]);
  const handleDownloadRecordingAudio = useCallback(async (kind: "webm" | "mp3") => {
    if (!currentMeeting) return;
    if (!recordingDownloadTarget || isDownloadingRecording) { if (!recordingDownloadTarget) toast.error("音声ファイルがまだ準備できていません"); return; }
    const label = kind === "mp3" ? "MP3" : "WebM"; const toastId = toast.loading(`${label}ファイルを準備しています`); setIsDownloadingRecording(true);
    try {
      const source = kind === "mp3" ? mp3MasterUrl(recordingDownloadTarget.recordingId) : recordingDownloadTarget.webmUrl;
      const blob = await downloadRecordingInChunks(source, kind === "mp3" ? "audio/mpeg" : "audio/webm", (progress) => toast.loading(`${label}をダウンロード中... ${progress}%`, { id: toastId }));
      const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url;
      link.download = `${String(recordingMeetingTitle(currentMeeting)).trim().replace(/[\/:*?"<>|]+/g, "-").replace(/\s+/g, "_") || "recording"}_audio.${kind}`; link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      toast.success(`${label}をダウンロードしました`, { id: toastId });
    } catch (error) { console.error("Failed to download recording audio:", error); toast.error(`${label}のダウンロードに失敗しました`, { id: toastId, description: "通信が不安定な場合は、少し時間をおいてもう一度試してください。" }); }
    finally { setIsDownloadingRecording(false); }
  }, [currentMeeting, recordingDownloadTarget, isDownloadingRecording]);
  return { isStoppingBot, isRetryingBot, isDeletingMeeting, isUpdatingConfig, isDownloadingRecording, forcePostMeetingMode, setForcePostMeetingMode, handleStopBot, handleLanguageChange, handleRetryBot, handleDeleteMeeting, handleExport, handleOpenInProvider, handleDownloadRecordingAudio };
}

export type MeetingExportItemsProps = { handleExport: (format: "txt" | "json" | "srt" | "vtt") => void; variant: "desktop" | "mobile" };
export function MeetingExportItems({ handleExport, variant }: MeetingExportItemsProps) {
  if (variant === "desktop") return <>
    <DropdownMenuItem onClick={() => handleExport("srt")}><FileText className="h-4 w-4 mr-2" />.srtをダウンロード</DropdownMenuItem>
    <DropdownMenuItem onClick={() => handleExport("vtt")}><FileText className="h-4 w-4 mr-2" />.vttをダウンロード</DropdownMenuItem>
  </>;
  return <>
    <DropdownMenuItem onClick={() => handleExport("srt")}><FileText className="h-4 w-4 mr-2" />.srtをダウンロード</DropdownMenuItem>
    <DropdownMenuItem onClick={() => handleExport("vtt")}><FileJson className="h-4 w-4 mr-2" />.vttをダウンロード</DropdownMenuItem>
  </>;
}
export type MeetingFailedActionsProps = { currentMeeting: Meeting; transcripts: TranscriptSegment[]; handleRetryBot: () => void };
export function MeetingFailedActions({ currentMeeting, transcripts, handleRetryBot }: MeetingFailedActionsProps) {
  if (currentMeeting.status !== "failed" || transcripts.length !== 0) return null;
  return <BotFailedIndicator status={currentMeeting.status} errorMessage={(() => { const ed = currentMeeting.data?.error_details; if (typeof ed === "string" && ed.length > 0) { const match = ed.match(/['"]error_message['"]\s*:\s*['"]([^'"]+)['"]/); if (match) return match[1]; return ed.length > 240 ? ed.slice(0, 240) + "…" : ed; } return currentMeeting.data?.error || currentMeeting.data?.failure_reason || currentMeeting.data?.status_message; })()} errorCode={currentMeeting.data?.error_code || (typeof currentMeeting.data?.failure_stage === "string" ? currentMeeting.data.failure_stage : undefined)} onRetry={handleRetryBot} />;
}
