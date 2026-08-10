"use client";

import type { FocusEvent, RefObject } from "react";
import Image from "next/image";
import { Calendar, Clock, FileText, Globe, Loader2, Trash2, Users, Video } from "lucide-react";
import { format } from "date-fns";
import { ja } from "date-fns/locale";
import { MeetingAgentPanel } from "@/components/agent/meeting-agent-panel";
import { DocsLink } from "@/components/docs/docs-link";
import { StatusHistory } from "@/components/meetings/status-history";
import { RestRecordingsPreview, RestTranscriptsPreview, WsEventLog } from "@/components/meetings/ws-event-log";
import { TtsSpeakCard } from "@/components/meetings/meeting-detail-auxiliary";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { getLanguageDisplayName } from "@/lib/languages";
import { withBasePath } from "@/lib/base-path";
import { cn, parseUTCTimestamp } from "@/lib/utils";
import type { Meeting, TranscriptSegment } from "@/types/vexa";

type StatusConfig = { label: string; color: string; bgColor: string; description?: string };
export type MeetingDetailSidebarProps = {
  currentMeeting: Meeting; transcripts: TranscriptSegment[]; agentPanelOpen: boolean; apiViewOpen: boolean;
  wsConnected: boolean; wsConnecting: boolean; authToken: string | null; platformConfig: { name: string };
  duration: number | null; statusConfig: StatusConfig; isSavingNotes: boolean; isEditingNotes: boolean; editedNotes: string;
  notesTextareaRef: RefObject<HTMLTextAreaElement | null>; handleNotesFocus: (event: FocusEvent<HTMLTextAreaElement>) => void;
  handleNotesBlur: () => void; setEditedNotes: (value: string) => void; setIsEditingNotes: (value: boolean) => void;
  shouldSetCursorToEndRef: RefObject<boolean>; isDeletingMeeting: boolean; deleteConfirmText: string; setDeleteConfirmText: (value: string) => void;
  handleDeleteMeeting: () => void; formatDuration: (minutes: number) => string;
};

export function MeetingDetailSidebar(props: MeetingDetailSidebarProps) {
  const { currentMeeting, transcripts, agentPanelOpen, apiViewOpen, wsConnected, wsConnecting, authToken, platformConfig, duration, statusConfig, isSavingNotes, isEditingNotes, editedNotes, notesTextareaRef, handleNotesFocus, handleNotesBlur, setEditedNotes, setIsEditingNotes, shouldSetCursorToEndRef, isDeletingMeeting, deleteConfirmText, setDeleteConfirmText, handleDeleteMeeting, formatDuration } = props;
  return (
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
            src={withBasePath(currentMeeting.platform === "google_meet"
              ? "/icons/icons8-google-meet-96.png"
              : currentMeeting.platform === "teams"
              ? "/icons/icons8-teams-96.png"
              : "/icons/icons8-zoom-96.png")}
            alt={platformConfig.name}
            width={32}
            height={32}
            unoptimized
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
            shouldSetCursorToEndRef.current = true;
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
            shouldSetCursorToEndRef.current = false;
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
  );
}
