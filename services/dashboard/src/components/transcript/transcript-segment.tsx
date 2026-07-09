"use client";

import { useState } from "react";
import { cn, parseUTCTimestamp } from "@/lib/utils";
import { Play, Pencil } from "lucide-react";
import type { TranscriptSegment as TranscriptSegmentType, SpeakerColor } from "@/types/vexa";
import { Badge } from "@/components/ui/badge";

interface TranscriptSegmentProps {
  segment: TranscriptSegmentType;
  speakerColor: SpeakerColor;
  isHighlighted?: boolean;
  searchQuery?: string;

  isActivePlayback?: boolean;
  onClickSegment?: () => void;
  /** When false, hide the avatar and speaker name (consecutive segments from same speaker). Defaults to true. */
  showSpeakerHeader?: boolean;
  /** 会議後の話者編集（issue #24）: 有効時は話者名クリックでインライン編集 */
  canEdit?: boolean;
  /** scope: "speaker" = クラスタ/同名一括、"segment" = この発話のみ */
  onSpeakerEdit?: (toName: string, scope: "speaker" | "segment") => void;
  /** 範囲選択（まとめて話者変更）用 */
  isSelected?: boolean;
  onToggleSelect?: () => void;
}

function formatTimestamp(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${minutes.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
}

function formatAbsoluteTimestamp(utcAbsoluteTime: string): string {
  // v0.10.5.3 Pack D-1 follow-up: parseUTCTimestamp handles unsuffixed-ISO
  // (no `Z`) so we land on the right instant; getHours/Minutes/Seconds (NOT
  // getUTC*) then renders in the viewer's browser-local timezone — matching
  // the rest of the dashboard sweep. Pre-fix this rendered UTC, which is why
  // the meeting detail page kept showing UTC even after meeting-card and
  // duration math were patched.
  try {
    const date = parseUTCTimestamp(utcAbsoluteTime);
    const hh = date.getHours().toString().padStart(2, "0");
    const mm = date.getMinutes().toString().padStart(2, "0");
    const ss = date.getSeconds().toString().padStart(2, "0");
    return `${hh}:${mm}:${ss}`;
  } catch (error) {
    console.error("Error parsing absolute timestamp:", error);
    return "00:00:00";
  }
}

function highlightText(text: string, query: string): React.ReactNode {
  if (!query) return text;

  const parts = text.split(new RegExp(`(${query})`, "gi"));
  return parts.map((part, i) =>
    part.toLowerCase() === query.toLowerCase() ? (
      <mark key={i} className="bg-yellow-200 dark:bg-yellow-800 rounded px-0.5">
        {part}
      </mark>
    ) : (
      part
    )
  );
}

function renderText(
  text: string,
  searchQuery?: string
): React.ReactNode {
  return searchQuery ? highlightText(text, searchQuery) : text;
}

export function TranscriptSegment({
  segment,
  speakerColor,
  isHighlighted,
  searchQuery,
  isActivePlayback,
  onClickSegment,
  showSpeakerHeader = true,
  canEdit,
  onSpeakerEdit,
  isSelected,
  onToggleSelect,
}: TranscriptSegmentProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [editValue, setEditValue] = useState("");

  // Always display absolute time from the feed when available (device-independent).
  // For grouped segments, callers should pass the FIRST segment's `absolute_start_time` as `segment.absolute_start_time`.
  const displayTimestamp = segment.absolute_start_time
    ? formatAbsoluteTimestamp(segment.absolute_start_time)
    : formatTimestamp(segment.start_time);

  const startEditing = (e: React.MouseEvent) => {
    e.stopPropagation();
    setEditValue(segment.speaker || "");
    setIsEditing(true);
  };

  const submitEdit = (scope: "speaker" | "segment") => {
    const name = editValue.trim();
    setIsEditing(false);
    if (!name || name === segment.speaker) return;
    onSpeakerEdit?.(name, scope);
  };

  return (
    <div
      onClick={onClickSegment}
      className={cn(
        "group flex gap-2 rounded-lg transition-colors",
        showSpeakerHeader ? "px-3 pt-2 pb-0.5" : "px-3 py-0",
        isHighlighted && "bg-yellow-50 dark:bg-yellow-900/20",
        isActivePlayback && "bg-primary/10 border-l-2 border-primary",
        isSelected && "bg-sky-50 dark:bg-sky-950/30 ring-1 ring-sky-300 dark:ring-sky-800",
        !isHighlighted && !isActivePlayback && !isSelected && "hover:bg-muted/50",
        onClickSegment && "cursor-pointer"
      )}
    >
      {/* 範囲選択チェックボックス（編集可能時のみ、ホバー/選択中に表示） */}
      {onToggleSelect && (
        <div
          className={cn(
            "flex items-start pt-1 transition-opacity",
            isSelected ? "opacity-100" : "opacity-0 group-hover:opacity-100"
          )}
        >
          <input
            type="checkbox"
            checked={!!isSelected}
            onChange={() => onToggleSelect()}
            onClick={(e) => e.stopPropagation()}
            aria-label="この発話を選択"
            className="h-3.5 w-3.5 accent-sky-600 cursor-pointer"
          />
        </div>
      )}

      {/* Content */}
      <div className="flex-1 min-w-0">
        {showSpeakerHeader && (
          <div className="flex items-center gap-2 mb-0.5">
            {isEditing ? (
              <span
                className="flex flex-wrap items-center gap-1.5"
                onClick={(e) => e.stopPropagation()}
              >
                <input
                  autoFocus
                  value={editValue}
                  onChange={(e) => setEditValue(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") submitEdit("speaker");
                    if (e.key === "Escape") setIsEditing(false);
                  }}
                  placeholder="話者名"
                  className="h-6 w-36 rounded border bg-background px-2 text-sm"
                  aria-label="話者名を編集"
                />
                <button
                  type="button"
                  onClick={() => submitEdit("speaker")}
                  className="rounded border border-primary/40 bg-primary/10 px-2 py-0.5 text-[11px] text-primary hover:bg-primary/20"
                  title="同じ声（クラスタ）の発話すべてを変更します"
                >
                  この話者を一括変更
                </button>
                <button
                  type="button"
                  onClick={() => submitEdit("segment")}
                  className="rounded border px-2 py-0.5 text-[11px] text-muted-foreground hover:bg-muted"
                  title="この発話だけを変更します"
                >
                  この発話のみ
                </button>
                <button
                  type="button"
                  onClick={() => setIsEditing(false)}
                  className="px-1 py-0.5 text-[11px] text-muted-foreground hover:text-foreground"
                >
                  キャンセル
                </button>
              </span>
            ) : (
              <span
                className={cn(
                  "font-medium text-sm inline-flex items-center gap-1",
                  speakerColor.text,
                  canEdit && "cursor-pointer hover:underline decoration-dotted underline-offset-2"
                )}
                onClick={canEdit ? startEditing : undefined}
                title={canEdit ? "クリックで話者名を変更" : undefined}
                role={canEdit ? "button" : undefined}
              >
                {segment.speaker || ""}
                {canEdit && (
                  <Pencil className="h-3 w-3 opacity-0 group-hover:opacity-60 transition-opacity" />
                )}
              </span>
            )}
            {/* 同室共有マイクの未命名サブ話者（issue #26 Phase 3）:
                クリックして話者名を確定すると消える。 */}
            {!isEditing && segment.speaker_mapping_status === "needs_review" && (
              <Badge
                variant="outline"
                className="text-[10px] h-4 px-1.5 border-amber-300 dark:border-amber-700 text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-950/30"
                title="同じマイクに複数人の声が入っています。クリックして話者名を確定してください。"
              >
                要確認
              </Badge>
            )}
            <span className="text-xs text-muted-foreground">
              {displayTimestamp}
            </span>
            {onClickSegment && !isEditing && (
              <span
                className={cn(
                  "ml-auto inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] text-muted-foreground transition-opacity",
                  isActivePlayback
                    ? "opacity-100 border-primary/40 bg-primary/10 text-primary"
                    : "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus-visible:opacity-100 group-hover:border-primary/40 group-hover:bg-primary/10 group-hover:text-primary"
                )}
                aria-label="この時刻から再生"
                title="この発話から再生"
              >
                <Play className="h-3 w-3" />
                再生
              </span>
            )}
          </div>
        )}
        {!showSpeakerHeader && (
          <div className="flex items-center gap-2">
            <p className={cn("text-sm leading-snug flex-1", !segment.completed && "text-muted-foreground/70 italic")}>
              {renderText(segment.text, searchQuery)}
            </p>
            <span className="text-[10px] text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0">
              {displayTimestamp}
            </span>
          </div>
        )}
        {showSpeakerHeader && (
          <p className={cn("text-sm leading-snug", !segment.completed && "text-muted-foreground/70 italic")}>
            {renderText(segment.text, searchQuery)}
          </p>
        )}
      </div>
    </div>
  );
}
