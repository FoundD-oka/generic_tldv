"use client";

import { useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { formatDistanceToNow, format } from "date-fns";
import { ja } from "date-fns/locale";
import { ChevronRight, Calendar, Clock, FileText, MessageSquare, Pencil, Check, X, Monitor, Trash2, Loader2 } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import type { Meeting } from "@/types/vexa";
import { getDetailedStatus } from "@/types/vexa";
import { cn, parseUTCTimestamp } from "@/lib/utils";
import { getCustomMeetingTitle, resolveMeetingTitle } from "@/lib/meeting-title";
import { useMeetingsStore } from "@/stores/meetings-store";
import { toast } from "sonner";
import { withBasePath } from "@/lib/base-path";

interface MeetingCardProps {
  meeting: Meeting;
}

// Platform icons using actual icon files from public folder
function GoogleMeetIcon({ className }: { className?: string }) {
  return (
    <Image
      src={withBasePath("/icons/icons8-google-meet-96.png")}
      alt="Google Meet"
      width={40}
      height={40}
      className={className}
      unoptimized
    />
  );
}

function TeamsIcon({ className }: { className?: string }) {
  return (
    <Image
      src={withBasePath("/icons/icons8-teams-96.png")}
      alt="Microsoft Teams"
      width={40}
      height={40}
      className={className}
      unoptimized
    />
  );
}

function ZoomIcon({ className }: { className?: string }) {
  return (
    <Image
      src={withBasePath("/icons/icons8-zoom-96.png")}
      alt="Zoom"
      width={40}
      height={40}
      className={className}
      unoptimized
    />
  );
}

function PlatformIcon({ platform, className }: { platform: string; className?: string }) {
  if (platform === "google_meet") return <GoogleMeetIcon className={className} />;
  if (platform === "teams") return <TeamsIcon className={className} />;
  if (platform === "browser_session") {
    return (
      <div className={cn("flex items-center justify-center bg-muted text-muted-foreground", className)}>
        <Monitor className="h-3.5 w-3.5" />
      </div>
    );
  }
  return <ZoomIcon className={className} />;
}

export function MeetingCard({ meeting }: MeetingCardProps) {
  const statusConfig = getDetailedStatus(meeting.status, meeting.data);
  const updateMeetingData = useMeetingsStore((state) => state.updateMeetingData);
  const deleteMeeting = useMeetingsStore((state) => state.deleteMeeting);
  const customTitle = getCustomMeetingTitle(meeting.data);
  const hasCustomTitle = customTitle !== "";
  const displayTitle = resolveMeetingTitle(meeting.data, meeting.platform_specific_id);
  const participants = meeting.data?.participants ?? [];
  const timeSource = meeting.start_time || meeting.created_at;
  const isActive = meeting.status === "active";
  
  // Title editing state
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [editedTitle, setEditedTitle] = useState("");
  const [isSavingTitle, setIsSavingTitle] = useState(false);

  // Delete state. The backend only accepts deletion for finalized meetings
  // (collector/endpoints.py:856) — anything else returns 409, so no button.
  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const canDelete = meeting.status === "completed" || meeting.status === "failed";

  // v0.10.5.3 Pack D-1 (#265): use parseUTCTimestamp consistently so the
  // unsuffixed-ISO timestamps the API returns are interpreted as UTC. Then
  // date-fns format() / toLocaleString() render in the browser's local
  // timezone (resolved via Intl.DateTimeFormat().resolvedOptions().timeZone).
  // Pre-fix: new Date(...) was interpreting unsuffixed ISO as local-time,
  // producing displayed times shifted by the user's UTC offset.
  const browserTz = typeof Intl !== "undefined"
    ? Intl.DateTimeFormat().resolvedOptions().timeZone
    : "UTC";
  const duration = meeting.start_time && meeting.end_time
    ? Math.round(
        (parseUTCTimestamp(meeting.end_time).getTime()
          - parseUTCTimestamp(meeting.start_time).getTime()) / 60000
      )
    : null;

  const formatDuration = (minutes: number) => {
    if (minutes < 1) return "1分未満";
    if (minutes < 60) return `${minutes}分`;
    const hours = Math.floor(minutes / 60);
    const mins = minutes % 60;
    return mins > 0 ? `${hours}時間${mins}分` : `${hours}時間`;
  };

  // Build detailed status info for tooltip
  const getStatusTooltipContent = () => {
    const lines: string[] = [];
    
    // Status description
    if (statusConfig.description) {
      lines.push(statusConfig.description);
    }
    
    // Completion reason details
    if (meeting.data?.completion_reason) {
      const reason = meeting.data.completion_reason;
      if (reason !== "stopped" && reason !== "meeting_ended") {
        const formattedReason = reason
          .split("_")
          .map(word => word.charAt(0).toUpperCase() + word.slice(1))
          .join(" ");
        lines.push(`理由: ${formattedReason}`);
      }
    }
    
    // Status transitions summary
    if (meeting.data?.status_transition && meeting.data.status_transition.length > 0) {
      const transitions = meeting.data.status_transition;
      const lastTransition = transitions[transitions.length - 1];
      
      if (lastTransition.timestamp) {
        try {
          const timestamp = parseUTCTimestamp(lastTransition.timestamp);
          lines.push(`最終更新: ${formatDistanceToNow(timestamp, { addSuffix: true, locale: ja })}`);
        } catch {
          // Ignore parsing errors
        }
      }
      
      // Show transition count if more than 1
      if (transitions.length > 1) {
        lines.push(`状態変更 ${transitions.length}回`);
      }
    }
    
    // Start/end times if available
    if (meeting.start_time) {
      try {
        const startTime = parseUTCTimestamp(meeting.start_time);
        lines.push(`開始: ${format(startTime, "M月d日 HH:mm", { locale: ja })}`);
      } catch {
        // Ignore parsing errors
      }
    }
    
    if (meeting.end_time) {
      try {
        const endTime = parseUTCTimestamp(meeting.end_time);
        lines.push(`終了: ${format(endTime, "M月d日 HH:mm", { locale: ja })}`);
      } catch {
        // Ignore parsing errors
      }
    }
    
    return lines;
  };

  const tooltipContent = getStatusTooltipContent();

  // Handle title editing
  const handleStartEdit = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setEditedTitle(customTitle);
    setIsEditingTitle(true);
  };

  const handleSaveTitle = async (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!editedTitle.trim()) {
      setIsEditingTitle(false);
      return;
    }
    setIsSavingTitle(true);
    try {
      await updateMeetingData(meeting.platform, meeting.platform_specific_id, {
        name: editedTitle.trim(),
      });
      setIsEditingTitle(false);
      toast.success("タイトルを更新しました");
    } catch {
      toast.error("タイトルの更新に失敗しました");
    } finally {
      setIsSavingTitle(false);
    }
  };

  const handleDelete = async () => {
    setIsDeleting(true);
    try {
      await deleteMeeting(meeting.platform, meeting.platform_specific_id, meeting.id);
      toast.success("会議を削除しました");
      // 成功時は store が一覧から除くのでこのカードごとアンマウントされる
    } catch (error) {
      toast.error("会議の削除に失敗しました", { description: (error as Error).message });
      setIsDeleting(false);
      setIsDeleteDialogOpen(false);
    }
  };

  const handleCancelEdit = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsEditingTitle(false);
    setEditedTitle("");
  };

  const handleKeyDown = async (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && editedTitle.trim() && !isSavingTitle) {
      e.preventDefault();
      e.stopPropagation();
      setIsSavingTitle(true);
      try {
        await updateMeetingData(meeting.platform, meeting.platform_specific_id, {
          name: editedTitle.trim(),
        });
        setIsEditingTitle(false);
        toast.success("タイトルを更新しました");
      } catch {
        toast.error("タイトルの更新に失敗しました");
      } finally {
        setIsSavingTitle(false);
      }
    } else if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      setIsEditingTitle(false);
      setEditedTitle("");
    }
  };

  const handleOpenDeleteDialog = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDeleteDialogOpen(true);
  };

  return (
    <>
    <Link
      href={`/meetings/${meeting.id}`}
      className="group block h-full rounded-2xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
      onClick={(event) => (isEditingTitle || isDeleteDialogOpen) && event.preventDefault()}
    >
      <Card
        className={cn(
          "relative flex h-full min-h-32 flex-col gap-0 overflow-hidden border-border/70 bg-card p-3 shadow-sm",
          "transition-all duration-300 ease-out hover:-translate-y-1 hover:border-primary/25 hover:shadow-lg",
          isActive && "border-green-500/40 shadow-green-500/10"
        )}
      >
        <div
          className={cn(
            "absolute inset-x-0 top-0 h-0.5",
            meeting.platform === "google_meet"
              ? "bg-green-500"
              : meeting.platform === "teams"
                ? "bg-[#5059C9]"
                : meeting.platform === "browser_session"
                  ? "bg-violet-500"
                  : "bg-blue-500"
          )}
        />

        {isActive && (
          <div className="pointer-events-none absolute inset-0 bg-gradient-to-br from-green-500/8 via-transparent to-transparent" />
        )}

        <div className="relative flex items-start justify-between gap-3">
          <div className="relative">
            <PlatformIcon platform={meeting.platform} className="h-6 w-6 rounded-md" />
            {isActive && (
              <span className="absolute -right-1 -top-1 flex h-3 w-3">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-green-400 opacity-75" />
                <span className="relative inline-flex h-3 w-3 rounded-full border-2 border-card bg-green-500" />
              </span>
            )}
          </div>

          <Tooltip>
            <TooltipTrigger asChild>
              <div>
                <Badge
                  variant="secondary"
                  className={cn(
                    "cursor-help px-2 py-0.5 text-[11px] font-medium",
                    statusConfig.bgColor,
                    statusConfig.color,
                    isActive && "animate-pulse"
                  )}
                >
                  {statusConfig.label}
                </Badge>
              </div>
            </TooltipTrigger>
            {tooltipContent.length > 0 && (
              <TooltipContent side="top" className="max-w-xs">
                <div className="space-y-1">
                  {tooltipContent.map((line, index) => (
                    <div key={index} className="text-xs">
                      {line}
                    </div>
                  ))}
                </div>
              </TooltipContent>
            )}
          </Tooltip>
        </div>

        <div className="relative mt-2 min-w-0 flex-1">
          {isEditingTitle ? (
            <div onClick={(event) => event.stopPropagation()}>
              <div className="flex items-center gap-1.5">
                <Input
                  value={editedTitle}
                  onChange={(event) => setEditedTitle(event.target.value)}
                  className="h-9 min-w-0 flex-1 text-sm font-semibold"
                  placeholder="会議タイトル..."
                  aria-label="会議タイトル"
                  autoFocus
                  disabled={isSavingTitle}
                  onFocus={(event) => event.currentTarget.select()}
                  onKeyDown={handleKeyDown}
                  onClick={(event) => event.stopPropagation()}
                />
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  className="h-8 w-8 shrink-0"
                  aria-label="タイトルを保存"
                  onClick={handleSaveTitle}
                  disabled={isSavingTitle || !editedTitle.trim()}
                >
                  <Check className="h-4 w-4" />
                </Button>
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  className="h-8 w-8 shrink-0"
                  aria-label="タイトル編集を取り消す"
                  onClick={handleCancelEdit}
                  disabled={isSavingTitle}
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>
            </div>
          ) : (
            <>
              <div className="flex items-start gap-2">
                <h3 className="line-clamp-2 flex-1 text-sm font-semibold leading-snug tracking-tight transition-colors group-hover:text-primary">
                  {displayTitle}
                </h3>
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  className="h-6 w-6 shrink-0 opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
                  aria-label="タイトルを編集"
                  onClick={handleStartEdit}
                >
                  <Pencil className="h-3.5 w-3.5" />
                </Button>
                {canDelete && (
                  <Button
                    type="button"
                    size="icon"
                    variant="ghost"
                    className="h-6 w-6 shrink-0 text-muted-foreground opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100 focus-visible:opacity-100"
                    aria-label="会議を削除"
                    onClick={handleOpenDeleteDialog}
                    disabled={isDeleting}
                  >
                    {isDeleting ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Trash2 className="h-3.5 w-3.5" />
                    )}
                  </Button>
                )}
              </div>

              {hasCustomTitle && meeting.platform_specific_id && (
                <p className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">
                  {meeting.platform_specific_id}
                </p>
              )}

              {participants.length > 0 && (
                <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
                  参加者: {participants.slice(0, 3).join(", ")}
                  {participants.length > 3 && ` ほか${participants.length - 3}名`}
                </p>
              )}
            </>
          )}

        </div>

        <div className="relative mt-2 border-t border-border/60 pt-2">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 pr-8 text-[11px]">
            {timeSource && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <div className="flex cursor-help items-center gap-1.5 text-muted-foreground">
                    <Calendar className="h-3 w-3" />
                    <span>{format(parseUTCTimestamp(timeSource), "yyyy年M月d日", { locale: ja })}</span>
                  </div>
                </TooltipTrigger>
                <TooltipContent side="top">
                  <p className="text-xs">
                    {parseUTCTimestamp(timeSource).toLocaleString(undefined, {
                      dateStyle: "medium",
                      timeStyle: "long",
                      timeZone: browserTz,
                    })}
                  </p>
                  <p className="text-[11px] text-muted-foreground/80">
                    UTC: {parseUTCTimestamp(timeSource).toISOString().replace("T", " ").slice(0, 19)} UTC
                  </p>
                </TooltipContent>
              </Tooltip>
            )}

            {timeSource && (
              <div className="flex items-center gap-1.5 text-muted-foreground">
                <Clock className="h-3 w-3" />
                <span>{formatDistanceToNow(parseUTCTimestamp(timeSource), { addSuffix: true, locale: ja })}</span>
              </div>
            )}

            {duration !== null && (
              <div className="flex items-center gap-1.5 text-muted-foreground">
                <MessageSquare className="h-3 w-3" />
                <span>{formatDuration(duration)}</span>
              </div>
            )}

            {typeof meeting.data?.notes === "string" && meeting.data.notes.trim() && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <div className="flex cursor-help items-center gap-1.5 text-muted-foreground">
                    <FileText className="h-3 w-3" />
                    <span>メモ</span>
                  </div>
                </TooltipTrigger>
                <TooltipContent side="top" className="max-w-xs">
                  <div className="text-xs text-muted-foreground">
                    {meeting.data.notes.length > 100
                      ? `${meeting.data.notes.substring(0, 100)}...`
                      : meeting.data.notes}
                  </div>
                </TooltipContent>
              </Tooltip>
            )}
          </div>

          <div className="absolute bottom-0 right-0 rounded-full p-1 transition-all duration-300 group-hover:translate-x-0.5 group-hover:bg-primary/10">
            <ChevronRight className="h-4 w-4 text-muted-foreground transition-colors group-hover:text-primary" />
          </div>
        </div>
      </Card>
    </Link>

    <AlertDialog open={isDeleteDialogOpen} onOpenChange={setIsDeleteDialogOpen}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>会議を削除しますか？</AlertDialogTitle>
          <AlertDialogDescription>
            「{displayTitle}」の文字起こしを削除し、会議データを匿名化します。この操作は取り消せません。
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={isDeleting}>キャンセル</AlertDialogCancel>
          <AlertDialogAction
            onClick={(event) => {
              event.preventDefault();
              handleDelete();
            }}
            disabled={isDeleting}
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
          >
            {isDeleting ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                削除中...
              </>
            ) : (
              "削除"
            )}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
    </>
  );
}
