"use client";

import { useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { formatDistanceToNow, format } from "date-fns";
import { ja } from "date-fns/locale";
import { ChevronRight, Calendar, MessageSquare, Pencil, Check, X, Monitor } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { Meeting } from "@/types/vexa";
import { getDetailedStatus } from "@/types/vexa";
import { cn, parseUTCTimestamp } from "@/lib/utils";
import { useMeetingsStore } from "@/stores/meetings-store";
import { toast } from "sonner";
import { withBasePath } from "@/lib/base-path";

interface MeetingCardProps {
  meeting: Meeting;
  participantsTitleTemplate?: string;
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

export function MeetingCard({
  meeting,
  participantsTitleTemplate = "{names}との会議",
}: MeetingCardProps) {
  const statusConfig = getDetailedStatus(meeting.status, meeting.data);
  const updateMeetingData = useMeetingsStore((state) => state.updateMeetingData);
  const participants = meeting.data?.participants || [];
  const rawTitle = meeting.data?.name || meeting.data?.title;
  const participantsTitle = participants.length > 0
    ? participantsTitleTemplate.replace("{names}", participants.join(", "))
    : null;
  const displayTitle = rawTitle || participantsTitle || meeting.platform_specific_id || "無題の会議";
  const timeSource = meeting.start_time || meeting.created_at;
  const isActive = meeting.status === "active";
  
  // Title editing state
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [editedTitle, setEditedTitle] = useState("");
  const [isSavingTitle, setIsSavingTitle] = useState(false);

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
    setEditedTitle(rawTitle || participantsTitle || "");
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

  return (
    <Link
      href={`/meetings/${meeting.id}`}
      className="group block h-full rounded-2xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
      onClick={(event) => isEditingTitle && event.preventDefault()}
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
            <button
              type="button"
              className="group/title -m-1 flex w-[calc(100%+0.5rem)] items-start gap-2 rounded-lg p-1 text-left hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
              title="クリックしてタイトルを編集"
              aria-label={`「${displayTitle}」のタイトルを編集`}
              onClick={handleStartEdit}
            >
              <h3 className="line-clamp-2 flex-1 text-sm font-semibold leading-snug tracking-tight transition-colors group-hover/title:text-primary">
                {displayTitle}
              </h3>
              <Pencil className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground/70 transition-colors group-hover/title:text-primary" />
            </button>
          )}

        </div>

        <div className="relative mt-2 border-t border-border/60 pt-2">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 pr-8 text-[11px]">
            {timeSource && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <div className="flex cursor-help items-center gap-1.5 text-muted-foreground">
                    <Calendar className="h-3 w-3" />
                    <span>{format(parseUTCTimestamp(timeSource), "M月d日", { locale: ja })}</span>
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

            {duration !== null && (
              <div className="flex items-center gap-1.5 text-muted-foreground">
                <MessageSquare className="h-3 w-3" />
                <span>{formatDuration(duration)}</span>
              </div>
            )}
          </div>

          <div className="absolute bottom-0 right-0 rounded-full p-1 transition-all duration-300 group-hover:translate-x-0.5 group-hover:bg-primary/10">
            <ChevronRight className="h-4 w-4 text-muted-foreground transition-colors group-hover:text-primary" />
          </div>
        </div>
      </Card>
    </Link>
  );
}
