"use client";

import { useEffect, useState, useRef, useCallback, useMemo, type CSSProperties } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import Image from "next/image";
import { format } from "date-fns";
import { ja } from "date-fns/locale";
import {
  ArrowLeft,
  Calendar,
  Clock,
  Users,
  Globe,
  Video,
  Pencil,
  Check,
  X,
  Sparkles,
  Loader2,
  FileText,
  StopCircle,
  FileJson,
  ChevronDown,
  ExternalLink,
  Trash2,
  Download,
  ClipboardCopy,
  Share,
  Volume2,
  Send,
  Bot,
  AlertTriangle,
  Monitor,
  Save,
} from "lucide-react";
import { AudioPlayer, type AudioPlayerHandle, type AudioFragment } from "@/components/recording/audio-player";
import { VideoPlayer, type VideoPlayerHandle } from "@/components/recording/video-player";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Separator } from "@/components/ui/separator";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ErrorState } from "@/components/ui/error-state";
import { TranscriptViewer } from "@/components/transcript/transcript-viewer";
import { BotStatusIndicator, BotFailedIndicator } from "@/components/meetings/bot-status-indicator";
import { WsEventLog, RestTranscriptsPreview, RestRecordingsPreview } from "@/components/meetings/ws-event-log";
// ChatPanel removed — chat messages now render inline in TranscriptViewer
import { AIChatPanel } from "@/components/ai";
import { useMeetingsStore } from "@/stores/meetings-store";
import { useAuthStore } from "@/stores/auth-store";
import { useLiveTranscripts } from "@/hooks/use-live-transcripts";
import { PLATFORM_CONFIG, getDetailedStatus } from "@/types/vexa";
import type { MeetingStatus, Meeting, RecordingData } from "@/types/vexa";
import { StatusHistory } from "@/components/meetings/status-history";
import { cn, parseUTCTimestamp } from "@/lib/utils";
import { vexaAPI } from "@/lib/api";
import { withBasePath } from "@/lib/base-path";
import { toast } from "sonner";
import { LanguagePicker } from "@/components/language-picker";
import { WHISPER_LANGUAGE_CODES, getLanguageDisplayName } from "@/lib/languages";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";
import {
  exportToTxt,
  exportToJson,
  exportToSrt,
  exportToVtt,
  downloadFile,
  generateFilename,
} from "@/lib/export";
import { getCookie, setCookie } from "@/lib/cookies";
import { DocsLink } from "@/components/docs/docs-link";
import { MeetingAgentPanel } from "@/components/agent/meeting-agent-panel";
import { WebhookDeliverySection } from "@/components/webhooks/webhook-delivery-section";
import { BrowserSessionView } from "@/components/meetings/browser-session-view";
import { useRuntimeConfig } from "@/hooks/use-runtime-config";

export default function MeetingDetailPage() {
  const params = useParams();
  const router = useRouter();
  const searchParams = useSearchParams();
  const idParam = (params as { id?: string | string[] } | null)?.id;
  const meetingId = Array.isArray(idParam) ? idParam[0] : (idParam ?? "");

  const {
    currentMeeting,
    transcripts,
    recordings,
    chatMessages,
    isLoadingMeeting,
    isLoadingTranscripts,
    isUpdatingMeeting,
    error,
    fetchMeeting,
    refreshMeeting,
    fetchTranscripts,
    fetchChatMessages,
    updateMeetingStatus,
    updateMeetingData,
    deleteMeeting,
    clearCurrentMeeting,
  } = useMeetingsStore();
  const authToken = useAuthStore((s) => s.token);
  const { config: runtimeConfig, isLoading: isRuntimeConfigLoading } = useRuntimeConfig();
  const apiBaseUrl = runtimeConfig?.apiUrl || "";
  const gatewayBrowserBase = apiBaseUrl.replace(/\/+$/, "");
  const browserRouteUrl = useCallback(
    (path: string) => {
      if (isRuntimeConfigLoading) return "";
      return gatewayBrowserBase ? `${gatewayBrowserBase}${path}` : withBasePath(path);
    },
    [gatewayBrowserBase, isRuntimeConfigLoading]
  );

  // Agent panel state
  const [agentPanelOpen, setAgentPanelOpen] = useState(false);

  // Browser view mode state
  const [viewMode, setViewMode] = useState<'transcript' | 'browser'>('transcript');

  // API view toggle state — default ON when coming from onboarding (?apiView=1)
  const [apiViewOpen, setApiViewOpen] = useState(() => searchParams?.get("apiView") === "1");
  const [apiButtonHighlight, setApiButtonHighlight] = useState(false);
  const apiButtonRef = useRef<HTMLButtonElement>(null);

  // Title editing state
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [editedTitle, setEditedTitle] = useState("");
  const [isSavingTitle, setIsSavingTitle] = useState(false);

  // Notes editing state
  const [isEditingNotes, setIsEditingNotes] = useState(false);
  const [editedNotes, setEditedNotes] = useState("");
  const [isSavingNotes, setIsSavingNotes] = useState(false);
  const [isNotesExpanded, setIsNotesExpanded] = useState(false);
  const notesTextareaRef = useRef<HTMLTextAreaElement>(null);
  const shouldSetCursorToEnd = useRef(false);

  // ChatGPT prompt editing state
  const [chatgptPrompt, setChatgptPrompt] = useState(() => {
    if (typeof window !== "undefined") {
      return getCookie("vexa-chatgpt-prompt") || "{url} を読んで、この会議内容について質問できるようにしてください。";
    }
    return "{url} を読んで、この会議内容について質問できるようにしてください。";
  });
  const [isChatgptPromptExpanded, setIsChatgptPromptExpanded] = useState(false);
  const [editedChatgptPrompt, setEditedChatgptPrompt] = useState(chatgptPrompt);
  const chatgptPromptTextareaRef = useRef<HTMLTextAreaElement>(null);

  // Bot control state
  const [isStoppingBot, setIsStoppingBot] = useState(false);
  const [isDeletingMeeting, setIsDeletingMeeting] = useState(false);
  const [deleteConfirmText, setDeleteConfirmText] = useState("");
  const [forcePostMeetingMode, setForcePostMeetingMode] = useState(false);
  
  // Bot config state
  const [currentLanguage, setCurrentLanguage] = useState<string | undefined>(
    currentMeeting?.data?.languages?.[0] || "auto"
  );
  const [isUpdatingConfig, setIsUpdatingConfig] = useState(false);

  // Audio playback state
  const audioPlayerRef = useRef<AudioPlayerHandle>(null);
  const videoPlayerRef = useRef<VideoPlayerHandle>(null);
  const [playbackTime, setPlaybackTime] = useState<number | null>(null);
  const [isPlaybackActive, setIsPlaybackActive] = useState(false);
  const [pendingSeekTime, setPendingSeekTime] = useState<number | null>(null);
  const [activeFragmentIndex, setActiveFragmentIndex] = useState(0);
  const [isDownloadingRecording, setIsDownloadingRecording] = useState(false);

  // Build ordered recording fragments for multi-fragment playback.
  // Each recording has a session_uid, created_at, and media_files with duration.
  // Sort by created_at so fragments play sequentially.
  //
  // Pack U.8 (v0.10.6, re-applies reverted Pack D-3 — commit a62d658 — on
  // top of the new master-recording contract from Pack U.5+U.6): resolve the
  // canonical master route, then use the dashboard same-origin master proxy.
  // This keeps playback/download usable when the object-store URL is internal.
  //
  // The async fetch happens once per recordings change. While in flight,
  // recordingFragments is the previous (or empty) array — the AudioPlayer
  // shows a "Preparing audio…" state.
  const [recordingFragments, setRecordingFragments] = useState<AudioFragment[]>([]);
  const [videoSrc, setVideoSrc] = useState<string | null>(null);
  // Surface connection errors from the master-stream-URL lookup. This is
  // distinct from "master not ready yet" (404 -> null -> finalizing UI).
  // v0.10.6.1 — a non-null value here means a real network/HTTP failure
  // that the user should see, not be silently retried-into-empty-state.
  const [playbackConnectionError, setPlaybackConnectionError] = useState<string | null>(null);

  // v0.10.6.1 — ADR-2 canonical playback path. Dashboard reads
  // `recording.playback_url.audio` (a stable backend route) and calls
  // vexaAPI.getRecordingMasterStreamUrl() to resolve it to a same-origin
  // streaming URL. No client-side picking from media_files[]. Null playback_url
  // → render "finalizing" UI state (no silent fallback to chunk 0).
  //
  // The signature pattern from pre-fix avoided URL refetch storms when
  // recordings[] reference changed but content didn't — kept here on
  // the playback_url field directly.
  const audioMediaSignature = useMemo(() => {
    return recordings
      .filter(r => (r.status === "completed" || r.status === "in_progress"))
      .filter(r => r.playback_url?.audio)
      .sort((a, b) => a.created_at.localeCompare(b.created_at))
      .map(r => `${r.id}:${r.playback_url?.audio ?? ""}`)
      .join("|");
  }, [recordings]);
  const [recordingDownloadTarget, setRecordingDownloadTarget] = useState<{
    recordingId: number;
    webmUrl: string;
  } | null>(null);

  useEffect(() => {
    if (!audioMediaSignature) {
      setRecordingFragments([]);
      setRecordingDownloadTarget(null);
      setPlaybackConnectionError(null);
      return;
    }
    let cancelled = false;
    (async () => {
      const availableRecordings = recordings
        .filter(r => (r.status === "completed" || r.status === "in_progress") && r.playback_url?.audio)
        .sort((a, b) => a.created_at.localeCompare(b.created_at));
      try {
        const results = await Promise.all(availableRecordings.map(async rec => {
          const result = await vexaAPI.getRecordingMasterStreamUrl(rec.id, "audio");
          if (!result) {
            // 404 — master not ready for this recording yet.
            return null;
          }
          return {
            recordingId: rec.id,
            fragment: {
              src: result.url,
              duration: result.duration_seconds ?? 0,
              sessionUid: rec.session_uid,
              createdAt: rec.created_at,
            } as AudioFragment,
          };
        }));
        if (!cancelled) {
          const resolved = results.filter((f): f is { recordingId: number; fragment: AudioFragment } => f !== null);
          setRecordingFragments(resolved.map((entry) => entry.fragment));
          setRecordingDownloadTarget(
            resolved[0]
              ? { recordingId: resolved[0].recordingId, webmUrl: resolved[0].fragment.src }
              : null
          );
          setPlaybackConnectionError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setPlaybackConnectionError(err instanceof Error ? err.message : String(err));
          setRecordingFragments([]);
          setRecordingDownloadTarget(null);
        }
      }
    })();
    return () => { cancelled = true; };
  }, [audioMediaSignature, recordings]);

  const hasRecordingAudio = recordingFragments.length > 0;

  // Find the first finalized video master across all recordings for the VideoPlayer.
  // v0.10.6.1 ADR-2: read recording.playback_url.video; no client-side
  // selection from media_files[].
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        for (const rec of recordings) {
          if (rec.status !== "completed" && rec.status !== "in_progress") continue;
          if (!rec.playback_url?.video) continue;
          const result = await vexaAPI.getRecordingMasterStreamUrl(rec.id, "video");
          if (!result) {
            // 404 — video master not ready for this recording yet; try the next.
            continue;
          }
          if (!cancelled) {
            setVideoSrc(result.url);
            setPlaybackConnectionError(null);
          }
          return;
        }
        if (!cancelled) setVideoSrc(null);
      } catch (err) {
        if (!cancelled) {
          setPlaybackConnectionError(err instanceof Error ? err.message : String(err));
          setVideoSrc(null);
        }
      }
    })();
    return () => { cancelled = true; };
  }, [recordings]);

  // Derive each session's start time (wall-clock ms) from segment data.
  // segment.start_time is relative to session start, and segment.absolute_start_time
  // is wall-clock UTC. So: sessionStart = absolute_start_time - start_time.
  // We compute one per session_uid to support multi-fragment meetings.
  const sessionStartMsBySessionUid = useMemo((): Map<string, number> => {
    const map = new Map<string, number>();
    for (const seg of transcripts) {
      if (!seg.absolute_start_time || seg.start_time == null) continue;
      const uid = seg.session_uid || "";
      if (map.has(uid)) continue; // use the first segment per session
      const absMs = new Date(seg.absolute_start_time).getTime();
      const sessionMs = absMs - seg.start_time * 1000;
      map.set(uid, sessionMs);
    }
    return map;
  }, [transcripts]);

  const handlePlaybackTimeUpdate = useCallback((time: number) => {
    setPlaybackTime(time);
    setIsPlaybackActive(true);
  }, []);

  const handleFragmentChange = useCallback((index: number) => {
    setActiveFragmentIndex(index);
  }, []);

  // Map a segment click to the correct recording fragment and seek position.
  //
  // segment.start_time is relative to session start — the same reference point
  // as the audio recording (both start from session start). So start_time IS
  // the correct seek position within the recording fragment.
  //
  // For multi-fragment: use absolute_start_time + session_uid to find the right fragment,
  // then use start_time as the seek offset within that fragment.
  //
  // KNOWN ISSUE: Audio playback seek is off by a few seconds when clicking segments.
  // segment.start_time is relative to SegmentPublisher session start, but the recording
  // (MediaRecorder) starts slightly later. resetSessionStart() in recording.ts reduces
  // the gap but doesn't eliminate it — there's still a few-second delta between when
  // the session start is reset and when MediaRecorder.start() actually fires inside
  // page.evaluate(). A precise fix would require the browser to signal the exact
  // MediaRecorder start timestamp back to Node.js.
  const handleSegmentClick = useCallback((startTimeSeconds: number, absoluteStartTime?: string) => {
    if (!hasRecordingAudio) {
      setPendingSeekTime(startTimeSeconds);
      return;
    }

    if (recordingFragments.length <= 1) {
      // Single recording — start_time is the seek position
      audioPlayerRef.current?.seekTo(startTimeSeconds);
      videoPlayerRef.current?.seekTo(startTimeSeconds);
      setPlaybackTime(startTimeSeconds);
      setIsPlaybackActive(true);
      return;
    }

    // Multi-fragment: find which fragment this segment belongs to
    let targetFragmentIndex = 0;
    if (absoluteStartTime) {
      const segTimeMs = new Date(absoluteStartTime).getTime();
      const matchingSegment = transcripts.find(
        s => s.absolute_start_time === absoluteStartTime
      );
      if (matchingSegment?.session_uid) {
        const uidIndex = recordingFragments.findIndex(
          f => f.sessionUid === matchingSegment.session_uid
        );
        if (uidIndex >= 0) targetFragmentIndex = uidIndex;
      } else {
        // Fallback: find fragment by derived session start
        for (let i = recordingFragments.length - 1; i >= 0; i--) {
          const uid = recordingFragments[i].sessionUid;
          const sessionStart = sessionStartMsBySessionUid.get(uid);
          if (sessionStart != null && sessionStart <= segTimeMs) {
            targetFragmentIndex = i;
            break;
          }
        }
      }
    }

    audioPlayerRef.current?.seekToFragment(targetFragmentIndex, startTimeSeconds);
    const virtualOffset = recordingFragments
      .slice(0, targetFragmentIndex)
      .reduce((sum, f) => sum + (f.duration || 0), 0);
    videoPlayerRef.current?.seekTo(virtualOffset + startTimeSeconds);
    setPlaybackTime(virtualOffset + startTimeSeconds);
    setIsPlaybackActive(true);
  }, [hasRecordingAudio, recordingFragments, transcripts, sessionStartMsBySessionUid]);

  useEffect(() => {
    if (!hasRecordingAudio || pendingSeekTime == null) return;
    const timer = setTimeout(() => {
      audioPlayerRef.current?.seekTo(pendingSeekTime);
      videoPlayerRef.current?.seekTo(pendingSeekTime);
      setPlaybackTime(pendingSeekTime);
      setIsPlaybackActive(true);
      setPendingSeekTime(null);
    }, 0);
    return () => clearTimeout(timer);
  }, [hasRecordingAudio, pendingSeekTime]);

  // Track if initial load is complete to prevent animation replays
  const hasLoadedRef = useRef(false);

  // Handle meeting status change from WebSocket
  const handleStatusChange = useCallback((status: MeetingStatus) => {
    // Refetch when status changes so we get latest data and post-meeting artifacts.
    if (status === "active" || status === "needs_human_help" || status === "stopping" || status === "completed" || status === "failed") {
      fetchMeeting(meetingId);
    }
    if (
      (status === "stopping" || status === "completed") &&
      currentMeeting?.platform &&
      currentMeeting?.platform_specific_id
    ) {
      fetchTranscripts(currentMeeting.platform, currentMeeting.platform_specific_id, String(currentMeeting.id));
    }
  }, [fetchMeeting, fetchTranscripts, meetingId, currentMeeting?.platform, currentMeeting?.platform_specific_id, currentMeeting?.id]);

  // Handle stopping the bot
  const handleStopBot = useCallback(async () => {
    if (!currentMeeting) return;
    setIsStoppingBot(true);
    try {
      await vexaAPI.stopBot(currentMeeting.platform, currentMeeting.platform_specific_id);
      // Optimistic transition to post-meeting UI immediately after stop is accepted.
      setForcePostMeetingMode(true);
      updateMeetingStatus(String(currentMeeting.id), "stopping");
      void fetchTranscripts(currentMeeting.platform, currentMeeting.platform_specific_id, String(currentMeeting.id), { silent: true });
      toast.success("ボットを停止しました", {
        description: "文字起こしを停止しました。",
      });
      void refreshMeeting(meetingId);
    } catch (error) {
      await refreshMeeting(meetingId);
      const latestMeeting = useMeetingsStore.getState().currentMeeting;
      const latestStatus =
        latestMeeting && String(latestMeeting.id) === String(currentMeeting.id)
          ? latestMeeting.status
          : null;

      if (latestStatus === "stopping" || latestStatus === "completed" || latestStatus === "failed") {
        setForcePostMeetingMode(latestStatus !== "failed");
        if (latestStatus === "stopping") {
          updateMeetingStatus(String(currentMeeting.id), "stopping");
        }
        void fetchTranscripts(currentMeeting.platform, currentMeeting.platform_specific_id, String(currentMeeting.id), { silent: true });
        void fetchChatMessages(currentMeeting.platform, currentMeeting.platform_specific_id);
        toast.success(latestStatus === "stopping" ? "停止処理を確認しました" : "会議は終了済みです", {
          description: latestStatus === "failed" ? "最新の状態に更新しました。" : "記録画面に切り替えました。",
        });
        return;
      }

      toast.error("ボットの停止に失敗しました", {
        description: (error as Error).message,
      });
    } finally {
      setIsStoppingBot(false);
    }
  }, [currentMeeting, fetchChatMessages, fetchTranscripts, meetingId, refreshMeeting, updateMeetingStatus]);

  // Handle language change
  const handleLanguageChange = useCallback(async (newLanguage: string) => {
    if (!currentMeeting) return;
    setIsUpdatingConfig(true);
    try {
      await vexaAPI.updateBotConfig(currentMeeting.platform, currentMeeting.platform_specific_id, {
        language: newLanguage === "auto" ? undefined : newLanguage,
        task: "transcribe",
      });
      setCurrentLanguage(newLanguage);
      updateMeetingData(currentMeeting.platform, currentMeeting.platform_specific_id, {
        languages: [newLanguage],
      });
      toast.success("言語設定を更新しました");
    } catch (error) {
      toast.error("言語設定の更新に失敗しました", {
        description: (error as Error).message,
      });
    } finally {
      setIsUpdatingConfig(false);
    }
  }, [currentMeeting, updateMeetingData]);


  const handleDeleteMeeting = useCallback(async () => {
    if (!currentMeeting) return;
    setIsDeletingMeeting(true);
    try {
      await deleteMeeting(
        currentMeeting.platform,
        currentMeeting.platform_specific_id,
        currentMeeting.id
      );
      toast.success("会議を削除しました");
      router.push("/meetings");
    } catch (error) {
      toast.error("会議の削除に失敗しました", {
        description: (error as Error).message,
      });
    } finally {
      setIsDeletingMeeting(false);
    }
  }, [currentMeeting, deleteMeeting, router]);

  // Handle export
  const handleExport = useCallback((format: "txt" | "json" | "srt" | "vtt") => {
    if (!currentMeeting) {
      toast.error("会議が選択されていません");
      return;
    }
    if (transcripts.length === 0) {
      toast.info("文字起こしはまだありません", {
        description: "会議が始まり、文字起こしが開始されると表示されます。",
      });
      return;
    }
    
    let content: string;
    let mimeType: string;

    switch (format) {
      case "txt":
        content = exportToTxt(currentMeeting, transcripts);
        mimeType = "text/plain";
        break;
      case "json":
        content = exportToJson(currentMeeting, transcripts);
        mimeType = "application/json";
        break;
      case "srt":
        content = exportToSrt(transcripts);
        mimeType = "text/plain";
        break;
      case "vtt":
        content = exportToVtt(transcripts);
        mimeType = "text/vtt";
        break;
    }

    const filename = generateFilename(currentMeeting, format);
    downloadFile(content, filename, mimeType);
  }, [currentMeeting, transcripts]);

  const handleDownloadRecordingAudio = useCallback(async (format: "webm" | "mp3" = "webm") => {
    if (!recordingDownloadTarget) {
      toast.error("音声ファイルがまだ準備できていません");
      return;
    }
    if (isDownloadingRecording) return;

    const label = format === "mp3" ? "MP3" : "WebM";
    const sourceUrl =
      format === "mp3"
        ? withBasePath(`/api/vexa/recordings/${recordingDownloadTarget.recordingId}/master/mp3?type=audio`)
        : recordingDownloadTarget.webmUrl;
    const recordingBaseName =
      currentMeeting?.data?.name ||
      currentMeeting?.data?.title ||
      currentMeeting?.platform_specific_id ||
      "recording";
    const safeRecordingBaseName = String(recordingBaseName)
      .trim()
      .replace(/[\\/:*?"<>|]+/g, "-")
      .replace(/\s+/g, "_") || "recording";
    const filename = `${safeRecordingBaseName}_audio.${format}`;
    const fallbackContentType = format === "mp3" ? "audio/mpeg" : "audio/webm";
    const chunkSize = 8 * 1024 * 1024;
    const toastId = toast.loading(`${label}ファイルを準備しています`);

    setIsDownloadingRecording(true);
    try {
      const probe = await fetch(sourceUrl, {
        headers: { Range: "bytes=0-0" },
        cache: "no-store",
      });
      if (probe.status !== 206) {
        throw new Error(`Unexpected audio probe response: ${probe.status}`);
      }

      const contentRange = probe.headers.get("content-range") || "";
      const totalMatch = contentRange.match(/\/(\d+)$/);
      const totalBytes = totalMatch ? Number(totalMatch[1]) : 0;
      const contentType = probe.headers.get("content-type") || fallbackContentType;
      await probe.arrayBuffer();

      if (!Number.isFinite(totalBytes) || totalBytes <= 0) {
        throw new Error("Audio size is unavailable");
      }

      const chunks: BlobPart[] = [];
      for (let start = 0; start < totalBytes; start += chunkSize) {
        const end = Math.min(start + chunkSize - 1, totalBytes - 1);
        const response = await fetch(sourceUrl, {
          headers: { Range: `bytes=${start}-${end}` },
          cache: "no-store",
        });
        if (response.status !== 206) {
          throw new Error(`Audio chunk request failed: ${response.status}`);
        }
        chunks.push(await response.blob());

        const progress = Math.round(((end + 1) / totalBytes) * 100);
        toast.loading(`${label}をダウンロード中... ${progress}%`, { id: toastId });
      }

      const objectUrl = URL.createObjectURL(new Blob(chunks, { type: contentType }));
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = filename;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
      toast.success(`${label}をダウンロードしました`, { id: toastId });
    } catch (error) {
      console.error("Failed to download recording audio:", error);
      toast.error(`${label}のダウンロードに失敗しました`, {
        id: toastId,
        description: "通信が不安定な場合は、少し時間をおいてもう一度試してください。",
      });
    } finally {
      setIsDownloadingRecording(false);
    }
  }, [currentMeeting, isDownloadingRecording, recordingDownloadTarget]);

  // Format transcript for ChatGPT
  const formatTranscriptForChatGPT = useCallback((meeting: Meeting, segments: typeof transcripts): string => {
    let output = "会議文字起こし\n\n";
    
    if (meeting.data?.name || meeting.data?.title) {
      output += `タイトル: ${meeting.data?.name || meeting.data?.title}\n`;
    }
    
    if (meeting.start_time) {
      output += `日時: ${format(parseUTCTimestamp(meeting.start_time), "yyyy年M月d日 HH:mm", { locale: ja })}\n`;
    }
    
    if (meeting.data?.participants?.length) {
      output += `参加者: ${meeting.data.participants.join(", ")}\n`;
    }
    
    output += "\n---\n\n";
    
    for (const segment of segments) {
      // Use absolute timestamp if available
      let timestamp = "";
      if (segment.absolute_start_time) {
        try {
          // v0.10.5.3 Pack D-1 follow-up: parse as UTC then format in
          // browser-local tz so the copied transcript matches what the user
          // sees on screen (e.g. "2026-05-01 14:32:11" not "11:32:11").
          const date = parseUTCTimestamp(segment.absolute_start_time);
          const yyyy = date.getFullYear().toString().padStart(4, "0");
          const mo = (date.getMonth() + 1).toString().padStart(2, "0");
          const dd = date.getDate().toString().padStart(2, "0");
          const hh = date.getHours().toString().padStart(2, "0");
          const mm = date.getMinutes().toString().padStart(2, "0");
          const ss = date.getSeconds().toString().padStart(2, "0");
          timestamp = `${yyyy}-${mo}-${dd} ${hh}:${mm}:${ss}`;
        } catch {
          timestamp = segment.absolute_start_time;
        }
      } else if (segment.start_time !== undefined) {
        // Fallback to relative timestamp
        const minutes = Math.floor(segment.start_time / 60);
        const seconds = Math.floor(segment.start_time % 60);
        timestamp = `${minutes.toString().padStart(2, "0")}:${seconds.toString().padStart(2, "0")}`;
      }
      
      if (timestamp) {
        output += `[${timestamp}] ${segment.speaker}: ${segment.text}\n\n`;
      } else {
        output += `${segment.speaker}: ${segment.text}\n\n`;
      }
    }
    
    return output;
  }, []);

  // Handle opening transcript in AI provider
  const handleOpenInProvider = useCallback(async (provider: "chatgpt" | "perplexity") => {
    if (!currentMeeting) {
      toast.error("会議が選択されていません");
      return;
    }
    if (transcripts.length === 0) {
      toast.info("文字起こしはまだありません", {
        description: "会議が始まり、文字起こしが開始されると表示されます。",
      });
      return;
    }

    // Prefer link-based flow (like "Read from https://..." in ChatGPT/Perplexity)
    try {
      const share = await vexaAPI.createTranscriptShare(
        currentMeeting.platform,
        currentMeeting.platform_specific_id,
        meetingId
      );

      // If the gateway is accessed via localhost (dev), providers still need a PUBLIC URL.
      // Allow overriding the public base via NEXT_PUBLIC_TRANSCRIPT_SHARE_BASE_URL.
      const publicBase = process.env.NEXT_PUBLIC_TRANSCRIPT_SHARE_BASE_URL?.replace(/\/$/, "");
      const shareUrl =
        publicBase && share.share_id
          ? `${publicBase}/public/transcripts/${share.share_id}.txt`
          : share.url;

      // Use custom prompt from cookie, replacing {url} placeholder
      const prompt = chatgptPrompt.replace(/{url}/g, shareUrl);
      
      let providerUrl: string;
      if (provider === "chatgpt") {
        providerUrl = `https://chatgpt.com/?hints=search&q=${encodeURIComponent(prompt)}`;
      } else {
        // Perplexity format: https://www.perplexity.ai/search?q={query}
        providerUrl = `https://www.perplexity.ai/search?q=${encodeURIComponent(prompt)}`;
      }
      
      window.open(providerUrl, "_blank", "noopener,noreferrer");
      return;
    } catch (err) {
      // Fall back to clipboard flow if share-link creation fails
      console.error("Failed to create transcript share link:", err);
    }

    try {
      const transcriptText = formatTranscriptForChatGPT(currentMeeting, transcripts);
      await navigator.clipboard.writeText(transcriptText);
      toast.success("文字起こしをクリップボードにコピーしました", {
        description: `${provider === "chatgpt" ? "ChatGPT" : "Perplexity"}を開きます。必要に応じて文字起こしを貼り付けてください。`,
      });
      const q = "会議の文字起こしをクリップボードにコピーしました。これから貼り付けるので、その内容について質問できるようにしてください。";
      let providerUrl: string;
      if (provider === "chatgpt") {
        providerUrl = `https://chatgpt.com/?hints=search&q=${encodeURIComponent(q)}`;
      } else {
        providerUrl = `https://www.perplexity.ai/search?q=${encodeURIComponent(q)}`;
      }
      setTimeout(() => window.open(providerUrl, "_blank", "noopener,noreferrer"), 100);
    } catch (error) {
      toast.error("文字起こしのコピーに失敗しました", {
        description: "もう一度試すか、手動でコピーしてください。",
      });
    }
  }, [currentMeeting, transcripts, formatTranscriptForChatGPT, meetingId, chatgptPrompt]);

  // Handle sending transcript to ChatGPT (for main button)
  const handleSendToChatGPT = useCallback(() => {
    handleOpenInProvider("chatgpt");
  }, [handleOpenInProvider]);

  // Handle saving ChatGPT prompt to cookie
  const handleChatgptPromptBlur = useCallback(() => {
    const trimmed = editedChatgptPrompt.trim();
    if (trimmed && trimmed !== chatgptPrompt) {
      setChatgptPrompt(trimmed);
      setCookie("vexa-chatgpt-prompt", trimmed);
    }
  }, [editedChatgptPrompt, chatgptPrompt]);

  // Live transcripts and status updates via WebSocket (for active and early states)
  const isEarlyState =
    currentMeeting?.status === "requested" ||
    currentMeeting?.status === "joining" ||
    currentMeeting?.status === "awaiting_admission";
  const isStoppingState = currentMeeting?.status === "stopping";
  const isBrowserSession = currentMeeting?.platform === "browser_session" || currentMeeting?.data?.mode === "browser_session";
  const shouldUseWebSocket =
    !isBrowserSession &&
    (currentMeeting?.status === "active" || isEarlyState || isStoppingState);
  
  const {
    isConnecting: wsConnecting,
    isConnected: wsConnected,
    connectionError: wsError,
    reconnectAttempts,
  } = useLiveTranscripts({
    platform: currentMeeting?.platform ?? "google_meet",
    nativeId: currentMeeting?.platform_specific_id ?? "",
    meetingId: meetingId,
    isActive: shouldUseWebSocket,
    onStatusChange: handleStatusChange,
  });

  useEffect(() => {
    if (meetingId) {
      setForcePostMeetingMode(false);
      fetchMeeting(meetingId);
    }

    return () => {
      clearCurrentMeeting();
      hasLoadedRef.current = false;
    };
  }, [meetingId, fetchMeeting, clearCurrentMeeting]);

  // Mark as loaded once we have data
  useEffect(() => {
    if (currentMeeting && !hasLoadedRef.current) {
      hasLoadedRef.current = true;
    }
  }, [currentMeeting]);

  // Show detected language from backend first (meeting.data.languages or from segments), then user can change via toggle
  const validLangCodes = useMemo(
    () => new Set(WHISPER_LANGUAGE_CODES),
    []
  );
  useEffect(() => {
    if (!currentMeeting) return;
    const fromData = currentMeeting.data?.languages?.[0];
    if (fromData && fromData !== "auto") {
      setCurrentLanguage(fromData);
      return;
    }
    // When not set by backend, use first detected language from segments (backend returns it per segment)
    const fromSegment = transcripts.find(
      (t) => t.language && t.language !== "unknown" && validLangCodes.has(t.language)
    )?.language;
    setCurrentLanguage(fromSegment || "auto");
  }, [currentMeeting, transcripts, validLangCodes]);

  // Fetch transcripts when meeting is loaded
  // Use specific properties as dependencies to avoid unnecessary refetches
  const meetingPlatform = currentMeeting?.platform;
  const meetingNativeId = currentMeeting?.platform_specific_id;
  const meetingNumericId = currentMeeting?.id ? String(currentMeeting.id) : undefined;
  const meetingStatus = currentMeeting?.status;
  const isPostMeetingStatus =
    forcePostMeetingMode || meetingStatus === "stopping" || meetingStatus === "completed";
  const shouldPollMeetingStatus =
    meetingStatus === "requested" ||
    meetingStatus === "joining" ||
    meetingStatus === "awaiting_admission" ||
    meetingStatus === "active" ||
    meetingStatus === "needs_human_help" ||
    meetingStatus === "stopping";
  const shouldPollPostMeetingArtifacts =
    isPostMeetingStatus &&
    currentMeeting?.data?.recording_enabled !== false &&
    !hasRecordingAudio &&
    !playbackConnectionError;

  useEffect(() => {
    if (!meetingId || !shouldPollMeetingStatus) return;

    let cancelled = false;
    const reconcileMeetingStatus = () => {
      if (cancelled) return;
      void refreshMeeting(meetingId);
    };

    reconcileMeetingStatus();
    const interval = window.setInterval(reconcileMeetingStatus, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [meetingId, shouldPollMeetingStatus, refreshMeeting]);

  useEffect(() => {
    // Active browser sessions use VNC — no transcript fetch needed.
    // Fetching transcripts while active would hit /transcripts which requires 'tx' scope;
    // if the cookie is unavailable the fallback VEXA_API_KEY (bot-scoped) causes 403.
    if (isBrowserSession && meetingStatus !== "stopping" && meetingStatus !== "completed") {
      return;
    }

    // Always refresh transcript/recording artifacts when entering post-meeting flow.
    if ((meetingStatus === "stopping" || meetingStatus === "completed") && meetingPlatform && meetingNativeId) {
      fetchTranscripts(meetingPlatform, meetingNativeId, meetingNumericId);
      fetchChatMessages(meetingPlatform, meetingNativeId);
      return;
    }

    // Always bootstrap existing segments from REST on page load.
    // WS only delivers new segments — without REST bootstrap, existing
    // transcripts are invisible after page reload during active meetings.
    if (meetingPlatform && meetingNativeId) {
      fetchTranscripts(meetingPlatform, meetingNativeId, meetingNumericId);
      fetchChatMessages(meetingPlatform, meetingNativeId);
    }
  }, [meetingStatus, shouldUseWebSocket, isBrowserSession, meetingPlatform, meetingNativeId, meetingNumericId, fetchTranscripts, fetchChatMessages]);

  // Also fetch chat messages for active meetings (WS handles real-time, REST bootstraps)
  useEffect(() => {
    if (shouldUseWebSocket && meetingPlatform && meetingNativeId) {
      fetchChatMessages(meetingPlatform, meetingNativeId);
    }
  }, [shouldUseWebSocket, meetingPlatform, meetingNativeId, fetchChatMessages]);

  // Recording masters are finalized asynchronously after the bot stops. The
  // status WebSocket can report "completed" before playback_url/audio is ready,
  // so keep refreshing post-meeting artifacts until the player can render.
  useEffect(() => {
    if (!meetingId || !meetingPlatform || !meetingNativeId) return;
    if (!shouldPollPostMeetingArtifacts) return;

    let cancelled = false;
    const refreshPostMeetingArtifacts = () => {
      if (cancelled) return;
      refreshMeeting(meetingId);
      fetchTranscripts(meetingPlatform, meetingNativeId, meetingNumericId, { silent: true });
      fetchChatMessages(meetingPlatform, meetingNativeId);
    };

    refreshPostMeetingArtifacts();
    const interval = window.setInterval(refreshPostMeetingArtifacts, 2500);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [
    meetingId,
    meetingPlatform,
    meetingNativeId,
    meetingNumericId,
    shouldPollPostMeetingArtifacts,
    refreshMeeting,
    fetchTranscripts,
    fetchChatMessages,
  ]);

  // Handle saving notes on blur
  const handleNotesBlur = useCallback(async () => {
    if (!currentMeeting || isSavingNotes) return;

    const originalNotes = currentMeeting.data?.notes || "";
    const trimmedNotes = editedNotes.trim();

    // Only save if content has changed
    if (trimmedNotes === originalNotes) {
      setIsEditingNotes(false);
      return;
    }

    setIsSavingNotes(true);
    try {
      await updateMeetingData(currentMeeting.platform, currentMeeting.platform_specific_id, {
        notes: trimmedNotes,
      });
      setIsEditingNotes(false);
    } catch (err) {
      toast.error("メモの保存に失敗しました");
      // Keep in edit mode on error so user can retry
    } finally {
      setIsSavingNotes(false);
    }
  }, [currentMeeting, editedNotes, isSavingNotes, updateMeetingData]);

  // Handle setting cursor to end when textarea is focused
  const handleNotesFocus = useCallback((e: React.FocusEvent<HTMLTextAreaElement>) => {
    if (shouldSetCursorToEnd.current && editedNotes) {
      const textarea = e.currentTarget;
      const length = editedNotes.length;
      // Use setTimeout to ensure the textarea is fully rendered
      setTimeout(() => {
        textarea.setSelectionRange(length, length);
      }, 0);
      shouldSetCursorToEnd.current = false;
    }
  }, [editedNotes]);

  // Compute absolute playback time for transcript highlight matching.
  // Convert the playback position to an absolute (wall-clock) ISO timestamp
  // so the transcript viewer can match against segment absolute_start_time.
  //
  // Key insight: segment.start_time is relative to the session start (when
  // SegmentPublisher was constructed), and the audio file also starts recording
  // around the same time. So playbackTime (seconds from audio start) roughly
  // equals seconds from session start. We derive the session start wall-clock
  // time from the segments: sessionStart = absolute_start_time - start_time.
  //
  // Previously this used recording.created_at, which is the upload time — not
  // when the recording actually started — causing a large offset.
  // Convert playback position (seconds from session start) to absolute wall-clock
  // time so the transcript viewer can highlight the matching segment.
  //
  // NOTE: returns an ISO string WITH `Z` (UTC). This is intentional — the
  // value is consumed by formatAbsoluteTimestamp() in transcript-segment.tsx
  // which calls parseUTCTimestamp() then renders in browser-local tz. Do not
  // "fix" this to a local-tz string; that would cause a double-shift.
  const playbackAbsoluteTime = useMemo((): string | null => {
    if (playbackTime == null || !isPlaybackActive || recordingFragments.length === 0) return null;
    if (recordingFragments.length === 1) {
      const uid = recordingFragments[0].sessionUid;
      const sessionStart = sessionStartMsBySessionUid.get(uid);
      if (sessionStart == null) return null;
      return new Date(sessionStart + playbackTime * 1000).toISOString();
    }
    // Multi-fragment: find which fragment the virtual time falls in
    let remaining = playbackTime;
    for (let i = 0; i < recordingFragments.length; i++) {
      const fragDur = recordingFragments[i].duration || 0;
      if (remaining <= fragDur || i === recordingFragments.length - 1) {
        const uid = recordingFragments[i].sessionUid;
        const sessionStart = sessionStartMsBySessionUid.get(uid);
        if (sessionStart == null) return null;
        return new Date(sessionStart + remaining * 1000).toISOString();
      }
      remaining -= fragDur;
    }
    return null;
  }, [playbackTime, isPlaybackActive, recordingFragments, sessionStartMsBySessionUid]);

  // Browser session check runs first — transcript errors must not block the VNC view.
  // The transcript fetch is skipped for active browser sessions, but if a stale error
  // exists in the store (e.g. from a prior page visit), we still want to show the VNC.
  if (currentMeeting && currentMeeting.data?.mode === "browser_session") {
    return <BrowserSessionView meeting={currentMeeting} />;
  }

  if (error) {
    return (
      <div className="space-y-6">
        <Button variant="ghost" onClick={() => router.back()}>
          <ArrowLeft className="mr-2 h-4 w-4" />
          戻る
        </Button>
        <ErrorState
          error={error}
          onRetry={() => fetchMeeting(meetingId)}
        />
      </div>
    );
  }

  if (isLoadingMeeting || !currentMeeting) {
    return <MeetingDetailSkeleton />;
  }

  const platformConfig = PLATFORM_CONFIG[currentMeeting.platform];
  const statusConfig = getDetailedStatus(currentMeeting.status, currentMeeting.data);

  // Safety check: ensure statusConfig is always defined
  if (!statusConfig) {
    console.error("statusConfig is undefined for status:", currentMeeting.status);
    return <MeetingDetailSkeleton />;
  }

  // v0.10.5.3 Pack D-1: parseUTCTimestamp on both ends so duration is correct
  // when API returns unsuffixed-ISO timestamps. Pre-fix: new Date() interpreted
  // both as local-tz → numerical delta is correct (same offset cancels) but
  // unifying the parse path here matches the rest of the file.
  const duration =
    currentMeeting.start_time && currentMeeting.end_time
      ? Math.round(
          (parseUTCTimestamp(currentMeeting.end_time).getTime() -
            parseUTCTimestamp(currentMeeting.start_time).getTime()) /
            60000
        )
      : null;
  const isPostMeetingFlow =
    forcePostMeetingMode ||
    currentMeeting.status === "stopping" || currentMeeting.status === "completed";
  const meetingRecordings = Array.isArray(currentMeeting.data?.recordings)
    ? (currentMeeting.data.recordings as RecordingData[])
    : [];
  const effectiveRecordings = recordings.length > 0 ? recordings : meetingRecordings;
  const hasRecordingEntries = effectiveRecordings.length > 0;
  const hasActiveRecording = effectiveRecordings.some((recording) =>
    recording.status === "in_progress" || recording.status === "uploading"
  );
  const recordingWasRequested = currentMeeting.data?.recording_enabled !== false;
  const noAudioRecordingForMeeting =
    currentMeeting.data?.recording_enabled === false && !hasRecordingAudio;
  const missingRequestedRecording =
    isPostMeetingFlow && recordingWasRequested && currentMeeting.status === "completed" && !hasRecordingEntries;
  const canUseSegmentPlayback = isPostMeetingFlow && !noAudioRecordingForMeeting && !missingRequestedRecording;
  const recordingTopBar = (isPostMeetingFlow || hasActiveRecording) ? (
    hasActiveRecording && !isPostMeetingFlow ? (
      <div className="flex items-center gap-2 px-4 py-2 bg-muted/50 rounded-lg border text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        録画中...
      </div>
    ) : playbackConnectionError ? (
      <div className="flex items-center gap-2 px-4 py-2 bg-destructive/10 rounded-lg border border-destructive/30 text-sm text-destructive">
        録画の読み込みで接続エラーが発生しました: {playbackConnectionError}
      </div>
    ) : hasRecordingAudio ? (
      <div className="flex flex-col gap-2">
        {videoSrc && (
          <VideoPlayer ref={videoPlayerRef} src={videoSrc} className="max-h-[360px]" />
        )}
        <AudioPlayer
          ref={audioPlayerRef}
          fragments={recordingFragments}
          onTimeUpdate={handlePlaybackTimeUpdate}
          onFragmentChange={handleFragmentChange}
          compact
        />
      </div>
    ) : noAudioRecordingForMeeting ? (
      <div className="flex items-center gap-2 px-4 py-2 bg-muted/50 rounded-lg border text-sm text-muted-foreground">
        この会議には音声録音がありません。
      </div>
    ) : missingRequestedRecording ? (
      <div className="flex items-center gap-2 px-4 py-2 bg-muted/50 rounded-lg border text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        録画を最終処理中...
      </div>
    ) : (
      <div className="flex items-center gap-2 px-4 py-2 bg-muted/50 rounded-lg border text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        録画を処理中...
      </div>
    )
  ) : null;

  const formatDuration = (minutes: number) => {
    if (minutes < 1) return "1分未満";
    if (minutes < 60) return `${minutes}分`;
    const hours = Math.floor(minutes / 60);
    const mins = minutes % 60;
    return mins > 0 ? `${hours}時間${mins}分` : `${hours}時間`;
  };

  // Browser view available for any active meeting bot (VNC runs in all bot containers)
  const hasBrowserView = !!(['requested', 'joining', 'awaiting_admission', 'active'].includes(currentMeeting?.status));
  const browserSessionEscalation = currentMeeting.data?.escalation as Record<string, unknown> | undefined;
  const browserSessionToken =
    (browserSessionEscalation?.session_token as string | undefined) ||
    (currentMeeting.data?.session_token as string | undefined) ||
    String(currentMeeting.id);
  const browserVncUrl = browserSessionToken
    ? browserRouteUrl(`/b/${browserSessionToken}/vnc/vnc.html?autoconnect=true&resize=scale&reconnect=true&view_only=false&path=b/${browserSessionToken}/vnc/websockify`)
    : "";

  const browserViewIframe = hasBrowserView && viewMode === 'browser' ? (() => {
    return (
      <div className="flex-1 overflow-hidden">
        {browserVncUrl ? (
          <iframe
            src={browserVncUrl}
            className="w-full h-full border-0"
            allow="clipboard-read; clipboard-write"
          />
        ) : (
          <div className="h-full flex items-center justify-center">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          </div>
        )}
      </div>
    );
  })() : null;

  // When browser view is active, render full-screen layout (like BrowserSessionView)
  if (browserViewIframe) {
    return (
      <div className="flex flex-col h-[calc(100vh-64px)] -m-4 md:-m-6 relative z-10">
        {/* Minimal toolbar */}
        <div className="flex items-center gap-2 px-3 py-1.5 border-b bg-background">
          <Button variant="ghost" size="sm" asChild className="h-8 px-2 text-muted-foreground hover:text-foreground">
            <Link href="/meetings">
              <ArrowLeft className="h-4 w-4" />
            </Link>
          </Button>
          <span className="text-sm font-medium truncate">{currentMeeting.data?.name || currentMeeting.platform_specific_id}</span>
          <Badge className={cn("shrink-0", statusConfig.bgColor, statusConfig.color)}>
            {statusConfig.label}
          </Badge>
          <div className="flex-1" />
          <div className="flex items-center border rounded-md overflow-hidden bg-background shadow-sm h-8">
            <Button variant="ghost" size="sm" className={cn("rounded-r-none h-full gap-1.5 text-xs", viewMode === 'transcript' && "bg-muted")} onClick={() => setViewMode('transcript')}>
              <FileText className="h-3.5 w-3.5" />
              文字起こし
            </Button>
            <Button variant="ghost" size="sm" className={cn("rounded-l-none h-full gap-1.5 text-xs", viewMode === 'browser' && "bg-muted")} onClick={() => setViewMode('browser')}>
              <Monitor className="h-3.5 w-3.5" />
              ブラウザ
            </Button>
          </div>
          <Button variant="outline" size="sm" className="h-8" disabled={!browserVncUrl} onClick={() => { if (browserVncUrl) window.open(browserVncUrl, "_blank"); }}>
            <ExternalLink className="h-3.5 w-3.5 mr-1" />
            全画面
          </Button>
        </div>
        {browserViewIframe}
      </div>
    );
  }

  return (
    <div className="space-y-2 lg:space-y-6 h-full flex flex-col">
      {/* Desktop Header */}
      <div className="hidden lg:flex items-center justify-between gap-4 mb-6">
        <div className="flex items-center gap-4 flex-1 min-w-0">
          <Button variant="ghost" size="sm" asChild className="-ml-2 h-8 px-2 text-muted-foreground hover:text-foreground">
            <Link href="/meetings">
              <ArrowLeft className="h-4 w-4" />
            </Link>
          </Button>
          
          {isEditingTitle ? (
            <div className="flex items-center gap-2 flex-1 max-w-md">
              <div className="flex items-center gap-2 flex-1">
                <Input
                  value={editedTitle}
                  onChange={(e) => setEditedTitle(e.target.value)}
                  className="text-xl font-bold h-9"
                  placeholder="会議タイトル..."
                  autoFocus
                  disabled={isSavingTitle}
                onKeyDown={async (e) => {
                  if (e.key === "Enter" && editedTitle.trim()) {
                    setIsSavingTitle(true);
                    try {
                      await updateMeetingData(currentMeeting.platform, currentMeeting.platform_specific_id, {
                        name: editedTitle.trim(),
                      });
                      setIsEditingTitle(false);
                      toast.success("タイトルを更新しました");
                    } catch (err) {
                      toast.error("タイトルの更新に失敗しました");
                    } finally {
                      setIsSavingTitle(false);
                    }
                  } else if (e.key === "Escape") {
                    setIsEditingTitle(false);
                  }
                }}
              />
              <div className="flex items-center gap-1">
                <Button
                  size="icon"
                  variant="ghost"
                  className="h-8 w-8 text-green-600"
                  disabled={isSavingTitle || !editedTitle.trim()}
                  onClick={async () => {
                    if (!editedTitle.trim()) return;
                    setIsSavingTitle(true);
                    try {
                      await updateMeetingData(currentMeeting.platform, currentMeeting.platform_specific_id, {
                        name: editedTitle.trim(),
                      });
                      setIsEditingTitle(false);
                      toast.success("タイトルを更新しました");
                    } catch (err) {
                      toast.error("タイトルの更新に失敗しました");
                    } finally {
                      setIsSavingTitle(false);
                    }
                  }}
                >
                  {isSavingTitle ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                </Button>
                <Button
                  size="icon"
                  variant="ghost"
                  className="h-8 w-8 text-muted-foreground"
                  disabled={isSavingTitle}
                  onClick={() => setIsEditingTitle(false)}
                >
                  <X className="h-4 w-4" />
                </Button>
                <DocsLink href="/docs/cookbook/rename-meeting" />
              </div>
              </div>
            </div>
          ) : (
            <div className="flex items-center gap-3 min-w-0">
              <div className="flex items-center gap-2 group min-w-0">
                <h1 className="text-xl font-bold tracking-tight truncate">
                  {currentMeeting.data?.name || currentMeeting.data?.title || currentMeeting.platform_specific_id}
                </h1>
                <Button
                  size="icon"
                  variant="ghost"
                  className="h-7 w-7 opacity-0 group-hover:opacity-100 transition-opacity shrink-0"
                  onClick={() => {
                    setEditedTitle(currentMeeting.data?.name || currentMeeting.data?.title || "");
                    setIsEditingTitle(true);
                  }}
                >
                  <Pencil className="h-3.5 w-3.5" />
                </Button>
              </div>
              <Badge className={cn("shrink-0", statusConfig.bgColor, statusConfig.color)}>
                {statusConfig.label}
              </Badge>
            </div>
          )}
        </div>

        <div className="flex items-center gap-2 shrink-0">
          {hasBrowserView && (
            <div className="flex items-center border rounded-md overflow-hidden bg-background shadow-sm h-9">
              <Button
                variant="ghost"
                size="sm"
                className={cn("rounded-r-none h-full gap-1.5", viewMode === 'transcript' && "bg-muted")}
                onClick={() => setViewMode('transcript')}
              >
                <FileText className="h-4 w-4" />
                文字起こし
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className={cn("rounded-l-none h-full gap-1.5", viewMode === 'browser' && "bg-muted")}
                onClick={() => setViewMode('browser')}
              >
                <Monitor className="h-4 w-4" />
                ブラウザ
              </Button>
            </div>
          )}
          {(currentMeeting.status === "active" || currentMeeting.status === "completed" || currentMeeting.status === "failed") && transcripts.length > 0 && (
            <div className="flex items-center gap-2">
              <AIChatPanel
                meeting={currentMeeting}
                transcripts={transcripts}
                trigger={
                  <Button className="gap-2 h-9">
                    <Sparkles className="h-4 w-4" />
                    AIに質問
                  </Button>
                }
              />
              
              <div className="flex items-center gap-2">
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      variant="outline"
                      className="gap-2 h-9"
                      title="エクスポート"
                    >
                      <Share className="h-4 w-4" />
                      <span>エクスポート</span>
                      <ChevronDown className="h-4 w-4" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem onClick={() => handleOpenInProvider("chatgpt")}>
                      <Image src="/icons/icons8-chatgpt-100.png" alt="ChatGPT" width={16} height={16} className="object-contain mr-2 invert dark:invert-0" />
                      ChatGPTで開く
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => handleOpenInProvider("perplexity")}>
                      <Image src="/icons/icons8-perplexity-ai-100.png" alt="Perplexity" width={16} height={16} className="object-contain mr-2" />
                      Perplexityで開く
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem onClick={() => handleExport("txt")}>
                      <FileText className="h-4 w-4 mr-2" />
                      .txtをダウンロード
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => handleExport("json")}>
                      <FileJson className="h-4 w-4 mr-2" />
                      .jsonをダウンロード
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem
                      onClick={() => {
                        if (!currentMeeting || transcripts.length === 0) return;
                        const text = exportToTxt(currentMeeting, transcripts);
                        navigator.clipboard.writeText(text).then(() => {
                          toast.success("文字起こしをクリップボードにコピーしました");
                        });
                      }}
                      disabled={transcripts.length === 0}
                    >
                      <ClipboardCopy className="h-4 w-4 mr-2" />
                      クリップボードにコピー
                    </DropdownMenuItem>
		                    {hasRecordingAudio && (
		                      <>
		                        <DropdownMenuItem
		                          onClick={() => handleDownloadRecordingAudio("webm")}
		                          disabled={isDownloadingRecording || !recordingDownloadTarget}
		                        >
		                          <Download className="h-4 w-4 mr-2" />
		                          {isDownloadingRecording ? "音声を準備中" : "WebMをダウンロード"}
		                        </DropdownMenuItem>
		                        <DropdownMenuItem
		                          onClick={() => handleDownloadRecordingAudio("mp3")}
		                          disabled={isDownloadingRecording || !recordingDownloadTarget}
		                        >
		                          <Download className="h-4 w-4 mr-2" />
		                          MP3をダウンロード
		                        </DropdownMenuItem>
		                      </>
		                    )}
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            </div>
          )}
          {currentMeeting.status === "active" && (
            <div className="flex items-center">
              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button
                    variant="outline"
                    className="gap-2 text-destructive hover:text-destructive hover:bg-destructive/10 h-9"
                    disabled={isStoppingBot}
                  >
                    {isStoppingBot ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <StopCircle className="h-4 w-4" />
                    )}
                    停止
                  </Button>
                </AlertDialogTrigger>
              <AlertDialogContent className={apiViewOpen ? "sm:max-w-lg" : undefined}>
                <AlertDialogHeader>
                  <AlertDialogTitle>文字起こしを停止しますか？</AlertDialogTitle>
                  <AlertDialogDescription>
                    ボットを会議から退出させ、ライブ文字起こしを停止します。停止後も文字起こしは確認できます。
                  </AlertDialogDescription>
                </AlertDialogHeader>
                {apiViewOpen && currentMeeting && (
                  <div className="rounded-lg overflow-hidden border border-border bg-[#111111] font-mono text-[11px]">
                    <div className="px-3 py-2 bg-[#1a1a1a] flex items-center justify-between">
                      <div className="flex items-center gap-[5px]">
                        <span className="w-2 h-2 rounded-full bg-[#ff5f57]" />
                        <span className="w-2 h-2 rounded-full bg-[#febc2e]" />
                        <span className="w-2 h-2 rounded-full bg-[#28c840]" />
                      </div>
                      <span className="text-[10px] text-gray-500">DELETE /bots</span>
                    </div>
                    <div className="p-3 leading-relaxed">
                      <div className="text-gray-500 mb-2"># ボットを停止</div>
                      <div>
                        <span className="text-gray-300">curl -X </span>
                        <span className="text-[#fca5a5]">DELETE</span>
                        <span className="text-gray-300"> \</span>
                      </div>
                      <div className="pl-4">
                        <span className="text-[#6ee7b7]">{apiBaseUrl}/bots/{currentMeeting.platform}/{currentMeeting.platform_specific_id}</span>
                        <span className="text-gray-300"> \</span>
                      </div>
                      <div className="pl-4">
                        <span className="text-gray-300">-H </span>
                        <span className="text-[#7dd3fc]">&apos;X-API-Key: {authToken ? `${authToken.slice(0, 8)}...` : "vx_sk_..."}&apos;</span>
                      </div>
                    </div>
                  </div>
                )}
                <AlertDialogFooter>
                  <AlertDialogCancel>キャンセル</AlertDialogCancel>
                  <AlertDialogAction
                    onClick={handleStopBot}
                    className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                  >
                    文字起こしを停止
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
            <DocsLink href="/docs/rest/bots#stop-bot" />
            </div>
          )}

          {/* Agent and API buttons hidden for now */}

        </div>
      </div>

      {/* API Tutorial Mode Banner */}
      {apiViewOpen && (
        <div className="hidden lg:flex items-center justify-between gap-3 mb-4 px-5 py-3 rounded-xl bg-gray-950 dark:bg-white">
          <div className="flex items-center gap-3">
            <span className="w-[7px] h-[7px] rounded-full bg-emerald-400 animate-pulse shrink-0" />
            <span className="text-[13px] font-medium text-white dark:text-gray-950">
              APIチュートリアルモード
            </span>
            <span className="text-[13px] text-gray-400 dark:text-gray-500">
              ライブAPI呼び出しとWebSocketイベントを表示中
            </span>
          </div>
          <button
            className="text-gray-400 hover:text-white dark:hover:text-gray-950 transition-colors p-1"
            onClick={() => {
              setApiViewOpen(false);
              setApiButtonHighlight(true);
              setTimeout(() => setApiButtonHighlight(false), 3000);
            }}
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      {/* Participants List - Desktop Only */}
      {currentMeeting.data?.participants && currentMeeting.data.participants.length > 0 && (
        <div className="hidden lg:block mb-6">
          <p className="text-sm text-muted-foreground">
            参加者: {currentMeeting.data.participants.slice(0, 4).join(", ")}
            {currentMeeting.data.participants.length > 4 && ` ほか${currentMeeting.data.participants.length - 4}名`}
          </p>
        </div>
      )}

      {/* Mobile: Single consolidated block with everything */}
      <div className="lg:hidden sticky top-[-16px] z-40 bg-background/80 backdrop-blur-sm -mx-4 px-4 py-2 mb-2">
        <div
          className={cn(
            "bg-card text-card-foreground rounded-lg border shadow-sm px-2 py-1.5",
            "backdrop-blur supports-[backdrop-filter]:bg-card/95"
          )}
        >
          {/* Single Highly Compact Row for Mobile */}
          <div className="flex items-center gap-1">
            <Button variant="ghost" size="icon" className="h-7 w-7 -ml-0.5 shrink-0" asChild>
              <Link href="/meetings">
                <ArrowLeft className="h-3.5 w-3.5" />
              </Link>
            </Button>

            {/* Title & Platform Icon */}
            <div className="flex-1 min-w-0 flex items-center gap-1">
              {isEditingTitle ? (
                <div className="flex items-center gap-1 flex-1 min-w-0">
                  <Input
                    value={editedTitle}
                    onChange={(e) => setEditedTitle(e.target.value)}
                    className="text-[11px] font-medium h-6 flex-1 min-w-0 py-0 px-1.5"
                    placeholder="タイトル..."
                    autoFocus
                    disabled={isSavingTitle}
                    onBlur={() => {
                      if (!isSavingTitle) setIsEditingTitle(false);
                    }}
                    onKeyDown={async (e) => {
                      if (e.key === "Enter" && editedTitle.trim()) {
                        setIsSavingTitle(true);
                        try {
                          await updateMeetingData(currentMeeting.platform, currentMeeting.platform_specific_id, {
                            name: editedTitle.trim(),
                          });
                          setIsEditingTitle(false);
                          toast.success("タイトルを更新しました");
                        } catch (err) {
                          toast.error("タイトルの更新に失敗しました");
                        } finally {
                          setIsSavingTitle(false);
                        }
                      } else if (e.key === "Escape") {
                        setIsEditingTitle(false);
                      }
                    }}
                  />
                  <Button
                    size="icon"
                    variant="ghost"
                    className="h-6 w-6 text-green-600 shrink-0"
                    disabled={isSavingTitle || !editedTitle.trim()}
                    onClick={async () => {
                      if (!editedTitle.trim()) return;
                      setIsSavingTitle(true);
                      try {
                        await updateMeetingData(currentMeeting.platform, currentMeeting.platform_specific_id, {
                          name: editedTitle.trim(),
                        });
                        setIsEditingTitle(false);
                        toast.success("タイトルを更新しました");
                      } catch (err) {
                        toast.error("タイトルの更新に失敗しました");
                      } finally {
                        setIsSavingTitle(false);
                      }
                    }}
                  >
                    {isSavingTitle ? <Loader2 className="h-3 w-3 animate-spin" /> : <Check className="h-3 w-3" />}
                  </Button>
                  <DocsLink href="/docs/cookbook/rename-meeting" />
                </div>
              ) : (
                <div 
                  className="flex items-center gap-1 group cursor-pointer min-w-0"
                  onClick={() => {
                    setEditedTitle(currentMeeting.data?.name || currentMeeting.data?.title || "");
                    setIsEditingTitle(true);
                  }}
                >
                  <span className="text-xs font-semibold truncate">
                    {currentMeeting.data?.name || currentMeeting.data?.title || currentMeeting.platform_specific_id}
                  </span>
                  <Pencil className="h-3 w-3 opacity-0 group-hover:opacity-100 transition-opacity shrink-0 text-muted-foreground" />
                </div>
              )}
            </div>

            {/* Status & Actions */}
            <div className="flex items-center gap-1 shrink-0">
              <Badge className={cn("text-[9px] h-4 px-1 shrink-0", statusConfig.bgColor, statusConfig.color)}>
                {statusConfig.label}
              </Badge>

              {/* Browser view toggle - Mobile */}
              {hasBrowserView && (
                <Button
                  variant="ghost"
                  size="icon"
                  className={cn("h-7 w-7", viewMode === 'browser' && "bg-muted")}
                  onClick={() => setViewMode(viewMode === 'browser' ? 'transcript' : 'browser')}
                  title={viewMode === 'browser' ? '文字起こしを表示' : 'ブラウザ画面を表示'}
                >
                  <Monitor className="h-3.5 w-3.5" />
                </Button>
              )}

              {/* Language Selector - Mobile (only when active) */}
              {currentMeeting.status === "active" && (
                <div className="flex items-center gap-0.5 shrink-0 ml-0.5">
                  <LanguagePicker
                    value={currentLanguage ?? "auto"}
                    onValueChange={handleLanguageChange}
                    disabled={isUpdatingConfig}
                    compact
                  />
                  {isUpdatingConfig && (
                    <Loader2 className="h-2.5 w-2.5 animate-spin" />
                  )}
                </div>
              )}

              <div className="flex items-center border-l ml-0.5 pl-0.5 gap-0">
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7 text-muted-foreground"
                  onClick={() => {
                    setEditedNotes(currentMeeting.data?.notes || "");
                    setIsEditingNotes(true);
                    setIsNotesExpanded(true);
                  }}
                  title="メモ"
                >
                  <FileText className="h-3.5 w-3.5" />
                </Button>

                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="outline" size="icon" className="h-7 w-7 ml-0.5" title="エクスポート">
                      <Share className="h-3.5 w-3.5" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem onClick={() => handleOpenInProvider("chatgpt")} disabled={transcripts.length === 0}>
                      <Image src="/icons/icons8-chatgpt-100.png" alt="ChatGPT" width={16} height={16} className="object-contain mr-2 invert dark:invert-0" />
                      ChatGPTで開く
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => handleOpenInProvider("perplexity")} disabled={transcripts.length === 0}>
                      <Image src="/icons/icons8-perplexity-ai-100.png" alt="Perplexity" width={16} height={16} className="object-contain mr-2" />
                      Perplexityで開く
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem onClick={() => handleExport("txt")} disabled={transcripts.length === 0}>
                      <FileText className="h-4 w-4 mr-2" />
                      .txtをダウンロード
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => handleExport("json")} disabled={transcripts.length === 0}>
                      <FileJson className="h-4 w-4 mr-2" />
                      .jsonをダウンロード
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem
                      onClick={() => {
                        if (!currentMeeting || transcripts.length === 0) return;
                        const text = exportToTxt(currentMeeting, transcripts);
                        navigator.clipboard.writeText(text).then(() => {
                          toast.success("文字起こしをクリップボードにコピーしました");
                        });
                      }}
                      disabled={transcripts.length === 0}
                    >
                      <ClipboardCopy className="h-4 w-4 mr-2" />
                      クリップボードにコピー
                    </DropdownMenuItem>
		                    {hasRecordingAudio && (
		                      <>
		                        <DropdownMenuItem
		                          onClick={() => handleDownloadRecordingAudio("webm")}
		                          disabled={isDownloadingRecording || !recordingDownloadTarget}
		                        >
		                          <Download className="h-4 w-4 mr-2" />
		                          {isDownloadingRecording ? "音声を準備中" : "WebMをダウンロード"}
		                        </DropdownMenuItem>
		                        <DropdownMenuItem
		                          onClick={() => handleDownloadRecordingAudio("mp3")}
		                          disabled={isDownloadingRecording || !recordingDownloadTarget}
		                        >
		                          <Download className="h-4 w-4 mr-2" />
		                          MP3をダウンロード
		                        </DropdownMenuItem>
		                      </>
		                    )}
                </DropdownMenuContent>
              </DropdownMenu>

                {currentMeeting.status === "active" && (
                  <AlertDialog>
                    <AlertDialogTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7 text-destructive ml-0.5"
                        disabled={isStoppingBot}
                        title="停止"
                      >
                        {isStoppingBot ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <StopCircle className="h-4 w-4" />
                        )}
                      </Button>
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>文字起こしを停止しますか？</AlertDialogTitle>
                        <AlertDialogDescription>
                          ボットを退出させ、文字起こしを停止します。
                        </AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>キャンセル</AlertDialogCancel>
                        <AlertDialogAction
                          onClick={handleStopBot}
                          className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                        >
                          停止
                        </AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>


      {/* Collapsible Notes Section - Mobile Only */}
      {isNotesExpanded && (
        <div className="lg:hidden sticky top-0 z-50 bg-card text-card-foreground rounded-lg border shadow-sm overflow-hidden animate-in slide-in-from-top-2 duration-200">
          <div className="p-3 space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium">メモ</span>
              <div className="flex items-center gap-2">
                {isSavingNotes && (
                  <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                    <Loader2 className="h-3 w-3 animate-spin" />
                    保存中...
                  </div>
                )}
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 w-6 p-0"
                  onClick={() => {
                    setIsNotesExpanded(false);
                    setIsEditingNotes(false);
                  }}
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>
            </div>
            <Textarea
              ref={notesTextareaRef}
              value={editedNotes}
              onChange={(e) => setEditedNotes(e.target.value)}
              onFocus={handleNotesFocus}
              onBlur={handleNotesBlur}
              placeholder="この会議のメモを追加..."
              className="min-h-[120px] resize-none text-sm"
              disabled={isSavingNotes}
              autoFocus
            />
          </div>
        </div>
      )}

      {/* Collapsible AI Prompt Section - Mobile Only */}
      {isChatgptPromptExpanded && (
        <div className="lg:hidden sticky top-0 z-50 bg-card text-card-foreground rounded-lg border shadow-sm overflow-hidden animate-in slide-in-from-top-2 duration-200">
          <div className="p-3 space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium">AIプロンプト</span>
              <Button
                variant="ghost"
                size="sm"
                className="h-6 w-6 p-0"
                onClick={() => {
                  setIsChatgptPromptExpanded(false);
                }}
              >
                <X className="h-4 w-4" />
              </Button>
            </div>
            <div className="space-y-2">
              <Textarea
                ref={chatgptPromptTextareaRef}
                value={editedChatgptPrompt}
                onChange={(e) => setEditedChatgptPrompt(e.target.value)}
                onBlur={handleChatgptPromptBlur}
                onKeyDown={(e) => {
                  if (e.key === "Escape") {
                    setEditedChatgptPrompt(chatgptPrompt);
                    setIsChatgptPromptExpanded(false);
                  }
                }}
                placeholder="AIプロンプト（文字起こしURLには {url} を使います）"
                className="min-h-[120px] resize-none text-sm"
                autoFocus
              />
              <p className="text-xs text-muted-foreground">
                文字起こしURLの差し込み位置として <code className="px-1 py-0.5 bg-muted rounded">{"{url}"}</code> を使います。
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Main content */}
      <div className={cn("grid grid-cols-1 gap-6 flex-1 min-h-0", browserViewIframe ? "" : "lg:grid-cols-3")}>
        {/* Transcript or Browser View */}
        <div className={cn("order-2 lg:order-1 flex flex-col min-h-0 flex-1", browserViewIframe ? "col-span-full" : "lg:col-span-2")}>
          {browserViewIframe ? browserViewIframe : (<>
          {/* Show bot status for early states */}
          {(currentMeeting.status === "requested" ||
            currentMeeting.status === "joining" ||
            currentMeeting.status === "awaiting_admission") && (
            <BotStatusIndicator
              status={currentMeeting.status}
              platform={currentMeeting.platform}
              meetingId={currentMeeting.platform_specific_id}
              createdAt={currentMeeting.created_at}
              updatedAt={currentMeeting.updated_at}
              transcribeEnabled={currentMeeting.data?.transcribe_enabled !== false}
              onStopped={() => {
                fetchMeeting(meetingId);
              }}
            />
          )}

          {/* Show escalation banner when bot needs human help */}
          {currentMeeting.status === "needs_human_help" && (
            <Card className="border-orange-500/50 bg-orange-500/5">
              <CardContent className="pt-6 pb-6">
                <div className="flex flex-col items-center text-center">
                  <div className="h-16 w-16 rounded-full bg-orange-500/10 flex items-center justify-center mb-4">
                    <AlertTriangle className="h-8 w-8 text-orange-500 animate-pulse" />
                  </div>
                  <h2 className="text-xl font-semibold mb-2 text-orange-600 dark:text-orange-400">
                    ボットの確認が必要です
                  </h2>
                  <p className="text-sm text-muted-foreground max-w-sm mb-4">
                    {(currentMeeting.data?.escalation as Record<string, unknown>)?.reason as string
                      || currentMeeting.data?.escalation_reason as string
                      || "ボットが停止しているため、人の確認が必要です。"}
                  </p>
                  <div className="flex gap-2 flex-wrap justify-center">
                    {(() => {
                      const escalation = currentMeeting.data?.escalation as Record<string, unknown> | undefined;
                      const sessionToken = escalation?.session_token as string
                        || currentMeeting.data?.session_token as string;
                      if (!sessionToken) return null;
                      const vncUrl = browserRouteUrl(`/b/${sessionToken}/vnc/vnc.html?autoconnect=true&resize=scale&reconnect=true&view_only=false&path=b/${sessionToken}/vnc/websockify`);
                      return (
                        <Button
                          variant="default"
                          size="sm"
                          className="gap-2 bg-orange-600 hover:bg-orange-700"
                          disabled={!vncUrl}
                          onClick={() => {
                            if (vncUrl) window.open(vncUrl, "_blank");
                          }}
                        >
                          <Monitor className="h-4 w-4" />
                          リモートブラウザを開く
                        </Button>
                      );
                    })()}
                    {(() => {
                      const escalation = currentMeeting.data?.escalation as Record<string, unknown> | undefined;
                      const sessionToken = escalation?.session_token as string
                        || currentMeeting.data?.session_token as string;
                      if (!sessionToken) return null;
                      return (
                        <Button
                          variant="outline"
                          size="sm"
                          className="gap-2"
                          onClick={async () => {
                            try {
                              const saveUrl = browserRouteUrl(`/b/${sessionToken}/save`);
                              if (!saveUrl) throw new Error("実行時設定を読み込み中です");
                              const response = await fetch(saveUrl, {
                                method: "POST",
                              });
                              if (!response.ok) throw new Error(await response.text());
                              toast.success("ブラウザ状態を保存しました");
                            } catch (error) {
                              toast.error("保存に失敗しました: " + (error as Error).message);
                            }
                          }}
                        >
                          <Save className="h-4 w-4" />
                          ブラウザ状態を保存
                        </Button>
                      );
                    })()}
                    <Button
                      variant="destructive"
                      size="sm"
                      onClick={handleStopBot}
                      disabled={isStoppingBot}
                      className="gap-2"
                    >
                      {isStoppingBot ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <StopCircle className="h-4 w-4" />
                      )}
                      ボットを停止
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>
          )}

          {/* Show failed indicator only when no transcripts exist */}
          {currentMeeting.status === "failed" && transcripts.length === 0 && (
            <BotFailedIndicator
              status={currentMeeting.status}
              errorMessage={(() => {
                // v0.10.5: bots emit a structured `error_details` payload
                // (stringified Python repr, single-quoted) on join failures.
                // Surface the human-readable error_message field so a user
                // staring at /meetings/<id> can see WHAT failed without
                // having to curl the API. Falls through to the legacy fields
                // for older bots / non-Node code paths.
                const ed = currentMeeting.data?.error_details;
                if (typeof ed === "string" && ed.length > 0) {
                  // Tolerate both JSON and Python repr (single-quoted) shapes.
                  const m = ed.match(/['"]error_message['"]\s*:\s*['"]([^'"]+)['"]/);
                  if (m) return m[1];
                  // Fall back to the raw string if pattern didn't match —
                  // ugly is better than silent.
                  return ed.length > 240 ? ed.slice(0, 240) + "…" : ed;
                }
                return currentMeeting.data?.error
                  || currentMeeting.data?.failure_reason
                  || currentMeeting.data?.status_message;
              })()}
              errorCode={
                currentMeeting.data?.error_code
                || (typeof currentMeeting.data?.failure_stage === "string"
                  ? currentMeeting.data.failure_stage
                  : undefined)
              }
            />
          )}

          {/* Keep transcript visible through stopping -> completed transition, and for failed meetings with data */}
          {(currentMeeting.status === "active" ||
            currentMeeting.status === "stopping" ||
            currentMeeting.status === "completed" ||
            (currentMeeting.status === "failed" && transcripts.length > 0)) && (
            <TranscriptViewer
              meeting={currentMeeting}
              segments={transcripts}
              chatMessages={chatMessages}
              isLoading={isLoadingTranscripts}
              isLive={currentMeeting.status === "active"}
              wsConnecting={wsConnecting}
              wsConnected={wsConnected}
              wsError={wsError}
              wsReconnectAttempts={reconnectAttempts}
              headerActions={<DocsLink href="/docs/cookbook/get-transcripts" />}
              topBarContent={recordingTopBar}
              playbackTime={playbackTime}
              playbackAbsoluteTime={playbackAbsoluteTime}
              isPlaybackActive={isPlaybackActive}
              onSegmentClick={canUseSegmentPlayback ? handleSegmentClick : undefined}
              onTranscribeComplete={() => {
                fetchMeeting(meetingId);
                if (currentMeeting?.platform && currentMeeting?.platform_specific_id) {
                  fetchTranscripts(currentMeeting.platform, currentMeeting.platform_specific_id, String(currentMeeting.id));
                }
              }}
            />
          )}
          </>)}

        </div>

        {/* Sidebar - sticky on desktop, hidden on mobile */}
        <div className="hidden lg:block order-1 lg:order-2">
          <div className="lg:sticky lg:top-6 space-y-6">
          {agentPanelOpen && (currentMeeting.status === "active" || currentMeeting.status === "completed") ? (
            <div className="rounded-lg border bg-card shadow-sm overflow-hidden" style={{ height: "calc(100vh - 10rem)" }}>
              <MeetingAgentPanel
                meetingId={currentMeeting.platform_specific_id}
                platform={currentMeeting.platform}
              />
            </div>
          ) : apiViewOpen ? (
            <>
            <WsEventLog
              status={currentMeeting.status}
              platform={currentMeeting.platform}
              nativeId={currentMeeting.platform_specific_id}
              wsConnected={wsConnected}
              wsConnecting={wsConnecting}
              segmentCount={transcripts.length}
            />
            <RestTranscriptsPreview
              platform={currentMeeting.platform}
              nativeId={currentMeeting.platform_specific_id}
              segmentCount={transcripts.length}
              token={authToken}
            />
            <RestRecordingsPreview
              platform={currentMeeting.platform}
              nativeId={currentMeeting.platform_specific_id}
              token={authToken}
            />
            </>
          ) : (
          <>
          {/* Meeting Info */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Video className="h-4 w-4" />
                会議情報
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Platform & Meeting ID */}
              <div className="flex items-center gap-3">
                <div className="h-8 w-8 rounded-lg flex items-center justify-center overflow-hidden bg-background">
                  <Image
                    src={currentMeeting.platform === "google_meet"
                      ? "/icons/icons8-google-meet-96.png"
                      : currentMeeting.platform === "teams"
                      ? "/icons/icons8-teams-96.png"
                      : "/icons/icons8-zoom-96.png"}
                    alt={platformConfig.name}
                    width={32}
                    height={32}
                    className="object-contain"
                  />
                </div>
                <div>
                  <p className="text-sm font-medium">{platformConfig.name}</p>
                  <p className="text-sm text-muted-foreground font-mono">
                    {currentMeeting.platform_specific_id}
                  </p>
                </div>
              </div>

              {/* Date */}
              {currentMeeting.start_time && (
                <div className="flex items-center gap-3">
                  <Calendar className="h-4 w-4 text-muted-foreground" />
                  <div>
                    <p className="text-sm font-medium">日時</p>
                    {/* v0.10.5.3 Pack D-1 (#265): parseUTCTimestamp interprets the
                        unsuffixed-ISO API timestamp as UTC; date-fns format()
                        renders in browser-local tz. Pre-fix: new Date() treated
                        unsuffixed ISO as LOCAL-tz, producing tz-shifted display. */}
                    <p className="text-sm text-muted-foreground" title={`UTC: ${currentMeeting.start_time}`}>
                      {format(parseUTCTimestamp(currentMeeting.start_time), "yyyy年M月d日 HH:mm", { locale: ja })}
                    </p>
                  </div>
                </div>
              )}

              {/* Duration */}
              {duration && (
                <div className="flex items-center gap-3">
                  <Clock className="h-4 w-4 text-muted-foreground" />
                  <div>
                    <p className="text-sm font-medium">時間</p>
                    <p className="text-sm text-muted-foreground">
                      {formatDuration(duration)}
                    </p>
                  </div>
                </div>
              )}

              {/* Bot Settings - hidden for now, available via API */}

              {/* Languages (read-only when not active) */}
              {currentMeeting.status !== "active" &&
                currentMeeting.data?.languages &&
                currentMeeting.data.languages.length > 0 && (
                  <div className="flex items-center gap-3">
                    <Globe className="h-4 w-4 text-muted-foreground" />
                    <div>
                      <p className="text-sm font-medium">言語</p>
                      <p className="text-sm text-muted-foreground">
                        {currentMeeting.data.languages.map(getLanguageDisplayName).join(", ")}
                      </p>
                    </div>
                  </div>
                )}
            </CardContent>
          </Card>

          {/* Participants */}
          {currentMeeting.data?.participants &&
            currentMeeting.data.participants.length > 0 && (
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Users className="h-4 w-4" />
                    参加者 ({currentMeeting.data.participants.length})
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="space-y-2">
                    {currentMeeting.data.participants.map((participant, index) => (
                      <div
                        key={index}
                        className="flex items-center gap-2 text-sm group"
                      >
                        <div className="h-2 w-2 rounded-full bg-primary transition-transform group-hover:scale-125" />
                        <span className="group-hover:text-primary transition-colors">{participant}</span>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            )}

          {/* Details */}
          <Card>
            <CardHeader>
              <CardTitle>詳細</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {/* Status with description */}
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">状態</span>
                <div className="text-right">
                  <span className={cn("font-medium", statusConfig.color)}>
                    {statusConfig.label}
                  </span>
                  {statusConfig.description && (
                    <p className="text-xs text-muted-foreground mt-0.5">
                      {statusConfig.description}
                    </p>
                  )}
                </div>
              </div>
              <Separator />
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">話者</span>
                <span className="font-medium">
                  {new Set(transcripts.map((t) => t.speaker)).size}
                </span>
              </div>
              <Separator />
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">単語数</span>
                <span className="font-medium">
                  {transcripts.reduce(
                    (acc, t) => acc + t.text.split(/\s+/).length,
                    0
                  )}
                </span>
              </div>

              {/* Status History */}
              {currentMeeting.data?.status_transition && currentMeeting.data.status_transition.length > 0 && (
                <>
                  <Separator />
                  <StatusHistory transitions={currentMeeting.data.status_transition} />
                </>
              )}
            </CardContent>
          </Card>

          {/* Notes */}
          <Card>
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <CardTitle className="flex items-center gap-2">
                    <FileText className="h-4 w-4" />
                    メモ
                  </CardTitle>
                  <DocsLink href="/docs/rest/meetings#update-meeting-data" />
                </div>
                {isSavingNotes && (
                  <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                    <Loader2 className="h-3 w-3 animate-spin" />
                    保存中...
                  </div>
                )}
              </div>
            </CardHeader>
            <CardContent>
              {isEditingNotes ? (
                <Textarea
                  ref={notesTextareaRef}
                  value={editedNotes}
                  onChange={(e) => setEditedNotes(e.target.value)}
                  onFocus={handleNotesFocus}
                  onBlur={handleNotesBlur}
                  placeholder="この会議のメモを追加..."
                  className="min-h-[120px] resize-none"
                  disabled={isSavingNotes}
                  autoFocus
                />
              ) : currentMeeting.data?.notes ? (
                <p
                  className="text-sm text-muted-foreground whitespace-pre-wrap cursor-text hover:bg-muted/50 rounded-md p-2 -m-2 transition-colors"
                  onClick={() => {
                    setEditedNotes(currentMeeting.data?.notes || "");
                    shouldSetCursorToEnd.current = true;
                    setIsEditingNotes(true);
                  }}
                >
                  {currentMeeting.data.notes}
                </p>
              ) : (
                <div
                  className="text-sm text-muted-foreground italic cursor-text hover:bg-muted/50 rounded-md p-2 -m-2 transition-colors min-h-[120px] flex items-center"
                  onClick={() => {
                    setEditedNotes("");
                    shouldSetCursorToEnd.current = false;
                    setIsEditingNotes(true);
                  }}
                >
                  ここをクリックしてメモを追加...
                </div>
              )}
            </CardContent>
          </Card>

          {/* TTS - Speak in Meeting */}
          {(currentMeeting.status === "active" || currentMeeting.status === "joining") && (
            <TtsSpeakCard platform={currentMeeting.platform} nativeId={currentMeeting.platform_specific_id} />
          )}

          {(currentMeeting.status === "completed" || currentMeeting.status === "failed") && (
            <Card className="border-destructive/30">
              <CardContent className="pt-6">
                <AlertDialog>
                  <AlertDialogTrigger asChild>
                    <Button
                      variant="destructive"
                      className="w-full gap-2"
                      disabled={isDeletingMeeting}
                      onClick={() => setDeleteConfirmText("")}
                    >
                      {isDeletingMeeting ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <Trash2 className="h-4 w-4" />
                      )}
                      会議を削除
                    </Button>
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogHeader>
                      <AlertDialogTitle>会議を削除しますか？</AlertDialogTitle>
                      <AlertDialogDescription>
                        文字起こしデータを削除し、会議データを匿名化します。確認のため <strong>削除</strong> と入力してください。
                      </AlertDialogDescription>
                    </AlertDialogHeader>
                    <div className="py-2">
                      <Input
                        placeholder="確認のため「削除」と入力"
                        value={deleteConfirmText}
                        onChange={(e) => setDeleteConfirmText(e.target.value)}
                        autoFocus
                      />
                    </div>
                    <AlertDialogFooter>
                      <AlertDialogCancel>キャンセル</AlertDialogCancel>
                      <AlertDialogAction
                        onClick={handleDeleteMeeting}
                        disabled={deleteConfirmText.trim() !== "削除" || isDeletingMeeting}
                        className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                      >
                        会議を削除
                      </AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              </CardContent>
            </Card>
          )}
          </>
          )}
          </div>
        </div>
      </div>

      {/* Webhook Delivery Section */}
      {currentMeeting.status === "completed" && (
        <div className="mt-6">
          <WebhookDeliverySection meetingId={meetingId} />
        </div>
      )}

    </div>
  );
}

function MeetingDetailSkeleton() {
  return (
    <div className="space-y-6">
      <Skeleton className="h-10 w-40" />
      <div className="space-y-2">
        <Skeleton className="h-8 w-64" />
        <div className="flex gap-2">
          <Skeleton className="h-6 w-24" />
          <Skeleton className="h-6 w-20" />
        </div>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2">
          <Skeleton className="h-[600px]" />
        </div>
        <div className="space-y-6">
          <Skeleton className="h-48" />
          <Skeleton className="h-40" />
        </div>
      </div>
    </div>
  );
}

function TtsSpeakCard({ platform, nativeId }: { platform: string; nativeId: string }) {
  const [text, setText] = useState("");
  const [isSpeaking, setIsSpeaking] = useState(false);
  const speakTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  async function handleSpeak() {
    if (!text.trim()) return;
    setIsSpeaking(true);
    // Keep stop button visible — estimate ~100ms per character for TTS playback
    const estimatedMs = Math.max(3000, text.trim().length * 100);
    if (speakTimeoutRef.current) clearTimeout(speakTimeoutRef.current);
    speakTimeoutRef.current = setTimeout(() => setIsSpeaking(false), estimatedMs);
    try {
      const response = await fetch(`/api/vexa/bots/${platform}/${nativeId}/speak`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text.trim(), voice: "alloy" }),
      });
      if (!response.ok) throw new Error(await response.text());
      setText("");
    } catch (error) {
      toast.error("読み上げに失敗しました: " + (error as Error).message);
      setIsSpeaking(false);
      if (speakTimeoutRef.current) clearTimeout(speakTimeoutRef.current);
    }
  }

  async function handleStop() {
    try {
      await fetch(`/api/vexa/bots/${platform}/${nativeId}/speak`, { method: "DELETE" });
    } catch {}
    setIsSpeaking(false);
    if (speakTimeoutRef.current) clearTimeout(speakTimeoutRef.current);
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm flex items-center gap-2">
          <Volume2 className="h-4 w-4" />
          会議で読み上げ
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex gap-2">
          <Input
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="読み上げる内容を入力..."
            className="text-sm"
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSpeak(); } }}
            disabled={isSpeaking}
          />
          {isSpeaking ? (
            <Button size="sm" variant="destructive" onClick={handleStop}>
              <StopCircle className="h-4 w-4" />
            </Button>
          ) : (
            <Button size="sm" onClick={handleSpeak} disabled={!text.trim()}>
              <Send className="h-4 w-4" />
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
