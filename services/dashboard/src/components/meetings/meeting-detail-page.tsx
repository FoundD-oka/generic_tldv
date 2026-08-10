"use client";

import { useState, useRef, useCallback } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, X, Loader2, FileText, Monitor, ExternalLink } from "lucide-react";
import { AudioPlayer } from "@/components/recording/audio-player";
import { VideoPlayer } from "@/components/recording/video-player";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ErrorState } from "@/components/ui/error-state";
import { useMeetingsStore } from "@/stores/meetings-store";
import { useAuthStore } from "@/stores/auth-store";
import { PLATFORM_CONFIG, getDetailedStatus } from "@/types/vexa";
import type { MeetingStatus, RecordingData } from "@/types/vexa";
import { cn, parseUTCTimestamp } from "@/lib/utils";
import { browserMeetingTitle } from "@/lib/meeting-detail-title";
import { withBasePath } from "@/lib/base-path";
import { toast } from "sonner";
import { getCookie, setCookie } from "@/lib/cookies";
import { WebhookDeliverySection } from "@/components/webhooks/webhook-delivery-section";
import { BrowserSessionView } from "@/components/meetings/browser-session-view";
import { useRuntimeConfig } from "@/hooks/use-runtime-config";
import { buildBrowserVncUrl } from "@/lib/browser-vnc-url";
import { saveMeetingTitle } from "@/lib/save-meeting-title";
import { MeetingDetailSkeleton } from "@/components/meetings/meeting-detail-auxiliary";
import { MeetingDetailSidebar } from "@/components/meetings/meeting-detail-sidebar";
import { MeetingDetailContent } from "@/components/meetings/meeting-detail-content";
import { MeetingDetailDesktopHeader } from "@/components/meetings/meeting-detail-desktop-header";
import { MeetingDetailMobileHeader } from "@/components/meetings/meeting-detail-mobile-header";
import { MeetingDetailMobileOverlays } from "@/components/meetings/meeting-detail-mobile-overlays";
import { useMeetingPlayback } from "@/hooks/use-meeting-playback";
import { useMeetingActions } from "@/hooks/use-meeting-actions";
import { useMeetingLiveData } from "@/hooks/use-meeting-live-data";

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
    error,
    fetchMeeting,
    refreshMeeting,
    fetchTranscripts,
    fetchChatMessages,
    updateMeetingStatus,
    updateMeetingData,
    deleteMeeting,
    setCurrentMeeting,
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

  const [agentPanelOpen] = useState(false);

  const [viewMode, setViewMode] = useState<'transcript' | 'browser'>('transcript');

  const [apiViewOpen, setApiViewOpen] = useState(() => searchParams?.get("apiView") === "1");
  const [, setApiButtonHighlight] = useState(false);

  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [editedTitle, setEditedTitle] = useState("");
  const [isSavingTitle, setIsSavingTitle] = useState(false);
  const handleSaveTitle = useCallback(async (): Promise<void> => {
    if (!currentMeeting || !editedTitle.trim()) return;
    await saveMeetingTitle({ platform: currentMeeting.platform, nativeId: currentMeeting.platform_specific_id, title: editedTitle, updateMeetingData, setSaving: setIsSavingTitle, onSaved: () => setIsEditingTitle(false), notifySuccess: toast.success, notifyError: toast.error });
  }, [currentMeeting, editedTitle, updateMeetingData]);

  const [isEditingNotes, setIsEditingNotes] = useState(false);
  const [editedNotes, setEditedNotes] = useState("");
  const [isSavingNotes, setIsSavingNotes] = useState(false);
  const [isNotesExpanded, setIsNotesExpanded] = useState(false);
  const notesTextareaRef = useRef<HTMLTextAreaElement>(null);
  const shouldSetCursorToEnd = useRef(false);

  const [chatgptPrompt, setChatgptPrompt] = useState(() => {
    if (typeof window !== "undefined") {
      return getCookie("vexa-chatgpt-prompt") || "{url} を読んで、この会議内容について質問できるようにしてください。";
    }
    return "{url} を読んで、この会議内容について質問できるようにしてください。";
  });
  const [isChatgptPromptExpanded, setIsChatgptPromptExpanded] = useState(false);
  const [editedChatgptPrompt, setEditedChatgptPrompt] = useState(chatgptPrompt);
  const chatgptPromptTextareaRef = useRef<HTMLTextAreaElement>(null);

  const [deleteConfirmText, setDeleteConfirmText] = useState("");
  
  const [currentLanguage, setCurrentLanguage] = useState<string | undefined>(
    currentMeeting?.data?.languages?.[0] || "auto"
  );

  const playback = useMeetingPlayback(recordings, transcripts);
  const {
    audioPlayerRef, videoPlayerRef, recordingFragments, videoSrc, playbackConnectionError,
    playbackTime, playbackAbsoluteTime, isPlaybackActive, hasRecordingAudio,
    recordingDownloadTarget, handlePlaybackTimeUpdate, handleFragmentChange, handleSegmentClick,
  } = playback;

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

  const actions = useMeetingActions({
    meeting: currentMeeting, meetingId, transcripts, chatgptPrompt, recordingDownloadTarget,
    refreshMeeting, fetchTranscripts, fetchChatMessages, updateMeetingStatus, updateMeetingData, setCurrentLanguage, deleteMeeting,
  });
  const {
    isStoppingBot, isDeletingMeeting, isUpdatingConfig, isDownloadingRecording, forcePostMeetingMode,
    setForcePostMeetingMode, handleStopBot, handleLanguageChange, handleRetryBot, handleDeleteMeeting,
    handleExport, handleOpenInProvider, handleDownloadRecordingAudio,
  } = actions;
  const handleChatgptPromptBlur = useCallback(() => {
    const trimmed = editedChatgptPrompt.trim();
    if (trimmed && trimmed !== chatgptPrompt) {
      setChatgptPrompt(trimmed);
      setCookie("vexa-chatgpt-prompt", trimmed);
    }
  }, [editedChatgptPrompt, chatgptPrompt]);

  const liveData = useMeetingLiveData({
    meetingId, currentMeeting, transcripts, forcePostMeetingMode, hasRecordingAudio, playbackConnectionError,
    hasLoadedRef, handleStatusChange, setForcePostMeetingMode, setCurrentLanguage, fetchMeeting, refreshMeeting,
    clearCurrentMeeting, fetchTranscripts, fetchChatMessages,
  });
  const {
    isConnecting: wsConnecting, isConnected: wsConnected, connectionError: wsError, reconnectAttempts,
  } = liveData;

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
    } catch {
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
        <Loader2 className="h-4 w-4 animate-spin shrink-0" />
        <div className="flex flex-col">
          <span>録画を最終処理中...</span>
          <span className="text-xs text-muted-foreground/80">録画の長さによって数分かかることがあります</span>
        </div>
      </div>
    ) : (
      <div className="flex items-center gap-2 px-4 py-2 bg-muted/50 rounded-lg border text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin shrink-0" />
        <div className="flex flex-col">
          <span>録画を処理中...</span>
          <span className="text-xs text-muted-foreground/80">録画の長さによって数分かかることがあります</span>
        </div>
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
    ? buildBrowserVncUrl(browserRouteUrl, browserSessionToken)
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
          <span className="text-sm font-medium truncate">{browserMeetingTitle(currentMeeting)}</span>
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
      <MeetingDetailDesktopHeader currentMeeting={currentMeeting} transcripts={transcripts} statusConfig={statusConfig}
        hasBrowserView={hasBrowserView} viewMode={viewMode} setViewMode={setViewMode} isEditingTitle={isEditingTitle}
        setIsEditingTitle={setIsEditingTitle} editedTitle={editedTitle} setEditedTitle={setEditedTitle} isSavingTitle={isSavingTitle}
        handleSaveTitle={handleSaveTitle} handleOpenInProvider={handleOpenInProvider}
        handleExport={handleExport} hasRecordingAudio={hasRecordingAudio} handleDownloadRecordingAudio={handleDownloadRecordingAudio}
        isDownloadingRecording={isDownloadingRecording} recordingDownloadTarget={recordingDownloadTarget} isStoppingBot={isStoppingBot}
        handleStopBot={handleStopBot} apiViewOpen={apiViewOpen} apiBaseUrl={apiBaseUrl} authToken={authToken} />

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

      <MeetingDetailMobileHeader currentMeeting={currentMeeting} transcripts={transcripts} statusConfig={statusConfig}
        hasBrowserView={hasBrowserView} viewMode={viewMode} setViewMode={setViewMode} currentLanguage={currentLanguage}
        handleLanguageChange={handleLanguageChange} isUpdatingConfig={isUpdatingConfig} isEditingTitle={isEditingTitle}
        setIsEditingTitle={setIsEditingTitle} editedTitle={editedTitle} setEditedTitle={setEditedTitle} isSavingTitle={isSavingTitle}
        handleSaveTitle={handleSaveTitle} setIsNotesExpanded={setIsNotesExpanded}
        setIsEditingNotes={setIsEditingNotes} setEditedNotes={setEditedNotes} handleOpenInProvider={handleOpenInProvider}
        handleExport={handleExport} hasRecordingAudio={hasRecordingAudio} handleDownloadRecordingAudio={handleDownloadRecordingAudio}
        isDownloadingRecording={isDownloadingRecording} recordingDownloadTarget={recordingDownloadTarget} isStoppingBot={isStoppingBot} handleStopBot={handleStopBot} />

      <MeetingDetailMobileOverlays isNotesExpanded={isNotesExpanded} setIsNotesExpanded={setIsNotesExpanded}
        isSavingNotes={isSavingNotes} setIsEditingNotes={setIsEditingNotes} notesTextareaRef={notesTextareaRef}
        editedNotes={editedNotes} setEditedNotes={setEditedNotes} handleNotesFocus={handleNotesFocus} handleNotesBlur={handleNotesBlur}
        isChatgptPromptExpanded={isChatgptPromptExpanded} setIsChatgptPromptExpanded={setIsChatgptPromptExpanded}
        chatgptPromptTextareaRef={chatgptPromptTextareaRef} editedChatgptPrompt={editedChatgptPrompt}
        setEditedChatgptPrompt={setEditedChatgptPrompt} handleChatgptPromptBlur={handleChatgptPromptBlur} chatgptPrompt={chatgptPrompt} />

      {/* Main content */}
      <div className={cn("grid grid-cols-1 gap-6 flex-1 min-h-0", browserViewIframe ? "" : "lg:grid-cols-3")}>
        <MeetingDetailContent
          currentMeeting={currentMeeting} meetingId={meetingId} transcripts={transcripts} chatMessages={chatMessages}
          isLoadingTranscripts={isLoadingTranscripts} browserViewIframe={browserViewIframe} browserRouteUrl={browserRouteUrl}
          handleStopBot={handleStopBot} isStoppingBot={isStoppingBot} handleRetryBot={handleRetryBot} fetchMeeting={fetchMeeting} refreshMeeting={refreshMeeting}
          fetchTranscripts={fetchTranscripts} wsConnecting={wsConnecting} wsConnected={wsConnected} wsError={wsError}
          reconnectAttempts={reconnectAttempts} recordingTopBar={recordingTopBar} playbackTime={playbackTime}
          playbackAbsoluteTime={playbackAbsoluteTime} isPlaybackActive={isPlaybackActive} canUseSegmentPlayback={canUseSegmentPlayback}
          handleSegmentClick={handleSegmentClick} setCurrentMeeting={setCurrentMeeting}
        />

        <MeetingDetailSidebar
          currentMeeting={currentMeeting} transcripts={transcripts} agentPanelOpen={agentPanelOpen} apiViewOpen={apiViewOpen}
          wsConnected={wsConnected} wsConnecting={wsConnecting} authToken={authToken} platformConfig={platformConfig}
          duration={duration} statusConfig={statusConfig} isSavingNotes={isSavingNotes} isEditingNotes={isEditingNotes}
          editedNotes={editedNotes} notesTextareaRef={notesTextareaRef} handleNotesFocus={handleNotesFocus} handleNotesBlur={handleNotesBlur}
          setEditedNotes={setEditedNotes} setIsEditingNotes={setIsEditingNotes} shouldSetCursorToEndRef={shouldSetCursorToEnd}
          isDeletingMeeting={isDeletingMeeting} deleteConfirmText={deleteConfirmText} setDeleteConfirmText={setDeleteConfirmText}
          handleDeleteMeeting={handleDeleteMeeting} formatDuration={formatDuration}
        />
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
