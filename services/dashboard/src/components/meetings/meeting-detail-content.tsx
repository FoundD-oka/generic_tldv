"use client";

import { useEffect, type ReactNode } from "react";
import { AlertTriangle, Loader2, Monitor, Save, StopCircle } from "lucide-react";
import { toast } from "sonner";
import { BotStatusIndicator } from "@/components/meetings/bot-status-indicator";
import { DocsLink } from "@/components/docs/docs-link";
import { TranscriptViewer } from "@/components/transcript/transcript-viewer";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { saveBrowserState } from "@/lib/meeting-detail-api";
import { buildBrowserVncUrl } from "@/lib/browser-vnc-url";
import { isRetranscriptionInProgress, normalizeRetranscriptionStatus, startRetranscriptionStatusPolling } from "@/lib/retranscription-status";
import { cn } from "@/lib/utils";
import { useMeetingsStore } from "@/stores/meetings-store";
import { MeetingFailedActions } from "@/hooks/use-meeting-actions";
import type { ChatMessage, Meeting, TranscriptSegment } from "@/types/vexa";

export type MeetingDetailContentProps = {
  currentMeeting: Meeting; meetingId: string; transcripts: TranscriptSegment[]; chatMessages: ChatMessage[]; isLoadingTranscripts: boolean;
  browserViewIframe: ReactNode; browserRouteUrl: (path: string) => string; handleStopBot: () => void; isStoppingBot: boolean; handleRetryBot: () => void;
  fetchMeeting: (id: string) => Promise<void>; refreshMeeting: (id: string) => Promise<Meeting | null>; fetchTranscripts: (platform: Meeting["platform"], nativeId: string, id?: string) => Promise<void>;
  wsConnecting: boolean; wsConnected: boolean; wsError: string | null; reconnectAttempts: number; recordingTopBar: ReactNode;
  playbackTime: number | null; playbackAbsoluteTime: string | null; isPlaybackActive: boolean; canUseSegmentPlayback: boolean;
  handleSegmentClick: (seconds: number, absoluteStartTime?: string) => void; setCurrentMeeting: (meeting: Meeting) => void;
};

export function MeetingDetailContent(props: MeetingDetailContentProps) {
 const { currentMeeting, meetingId, transcripts, chatMessages, isLoadingTranscripts, browserViewIframe, browserRouteUrl, handleStopBot, isStoppingBot, handleRetryBot, fetchMeeting, refreshMeeting, fetchTranscripts, wsConnecting, wsConnected, wsError, reconnectAttempts, recordingTopBar, playbackTime, playbackAbsoluteTime, isPlaybackActive, canUseSegmentPlayback, handleSegmentClick, setCurrentMeeting } = props;
 const shouldPollRetranscription = isRetranscriptionInProgress(currentMeeting.data);
 useEffect(() => { if (!meetingId || !shouldPollRetranscription) return; return startRetranscriptionStatusPolling(() => refreshMeeting(meetingId), 2500); }, [meetingId, refreshMeeting, shouldPollRetranscription]);
 return (
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
              const vncUrl = buildBrowserVncUrl(browserRouteUrl, sessionToken);
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
                      await saveBrowserState(saveUrl);
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
  <MeetingFailedActions currentMeeting={currentMeeting} transcripts={transcripts} handleRetryBot={handleRetryBot} />

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
      onTranscribeStatusChange={(status) => {
        const normalizedStatus = normalizeRetranscriptionStatus(status);
        if (normalizedStatus === "idle") return;
        const latestMeeting = useMeetingsStore.getState().currentMeeting;
        if (!latestMeeting || latestMeeting.id !== currentMeeting.id) return;
        const finalTranscription = latestMeeting.data?.final_transcription || {};
        setCurrentMeeting({
          ...latestMeeting,
          data: {
            ...latestMeeting.data,
            final_transcription: {
              ...finalTranscription,
              status: normalizedStatus,
            },
          },
        });
      }}
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
 );
}
