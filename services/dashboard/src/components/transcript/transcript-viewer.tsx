"use client";

import { useMemo, useState, useRef, useEffect, useLayoutEffect, useCallback } from "react";
import { Search, Download, FileText, FileJson, FileVideo, X, Users, MessageSquare, Wifi, WifiOff, Loader2, AlertCircle, Sparkles, Settings, ChevronDown, Mic, RefreshCw } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import Image from "next/image";
import { AIChatPanel } from "@/components/ai";
import { getCookie, setCookie } from "@/lib/cookies";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  DropdownMenuSeparator,
  DropdownMenuCheckboxItem,
} from "@/components/ui/dropdown-menu";
import { TranscriptSegment } from "./transcript-segment";
import type { Meeting, TranscriptSegment as TranscriptSegmentType, ChatMessage, SpeakerSuggestion, SpeakerUpdatePayload } from "@/types/vexa";
import { getSpeakerColor } from "@/types/vexa";
import {
  buildSegmentReassign,
  buildSpeakerMerge,
  buildSpeakerRename,
  describeSpeakerUpdate,
  reconcileRejectedSuggestions,
  type RejectedSuggestionEntry,
} from "@/lib/speaker-edit";
import {
  exportToTxt,
  exportToJson,
  exportToSrt,
  exportToVtt,
  downloadFile,
  generateFilename,
} from "@/lib/export";
import { cn, parseUTCTimestamp } from "@/lib/utils";
import { buildSpeakerDisplayLabels, getSpeakerDisplayLabel, getSpeakerIdentityKey, resolveSpeakerLabelByKey } from "@/lib/speaker-label";
import { vexaAPI, type VoiceprintSegmentsPreview } from "@/lib/api";
import { toast } from "sonner";
import { LanguagePicker } from "@/components/language-picker";
import { SelectedAudioVoiceprintDialog } from "./selected-audio-voiceprint-dialog";
import { type SegmentGroup, deduplicateByIdentity, sortByStartTime } from "@vexaai/transcript-rendering";
import { format } from "date-fns";
import { ja } from "date-fns/locale";
import { normalizeVoiceprintSelectionTiming } from "@/lib/voiceprint-selection";

// Linkify URLs in chat message text — splits text into plain strings and clickable <a> elements
const URL_REGEX = /(https?:\/\/[^\s<>"')\]]+)/gi;

// When two consecutive segments from the same speaker are separated by a
// silence gap at least this long, treat them as separate blocks (re-show the
// speaker header and add extra spacing) instead of merging them visually.
const SPEAKER_BLOCK_GAP_MS = 3 * 60 * 1000;

function linkifyText(text: string, searchQuery?: string): React.ReactNode[] {
  const parts = text.split(URL_REGEX);
  return parts.map((part, i) => {
    if (URL_REGEX.test(part)) {
      // Reset lastIndex since we're using 'g' flag
      URL_REGEX.lastIndex = 0;
      return (
        <a
          key={i}
          href={part}
          target="_blank"
          rel="noopener noreferrer"
          className="text-sky-600 dark:text-sky-400 underline underline-offset-2 hover:text-sky-800 dark:hover:text-sky-300 break-all"
          onClick={(e) => e.stopPropagation()}
        >
          {searchQuery ? highlightChatText(part, searchQuery) : part}
        </a>
      );
    }
    return searchQuery ? highlightChatText(part, searchQuery) : part;
  });
}

function highlightChatText(text: string, query: string): React.ReactNode {
  if (!query) return text;
  const segments = text.split(new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, "gi"));
  return segments.map((seg, i) =>
    seg.toLowerCase() === query.toLowerCase() ? (
      <mark key={i} className="bg-yellow-200 dark:bg-yellow-800 rounded px-0.5">
        {seg}
      </mark>
    ) : (
      seg
    )
  );
}

interface TranscriptViewerProps {
  meeting: Meeting;
  segments: TranscriptSegmentType[];
  chatMessages?: ChatMessage[];
  isLoading?: boolean;
  isLive?: boolean;
  // WebSocket connection state (only relevant when isLive=true)
  wsConnecting?: boolean;
  wsConnected?: boolean;
  wsError?: string | null;
  wsReconnectAttempts?: number;
  headerActions?: React.ReactNode;
  topBarContent?: React.ReactNode;
  // Playback sync props
  playbackTime?: number | null;
  /** ISO absolute timestamp of current playback position (for multi-fragment matching) */
  playbackAbsoluteTime?: string | null;
  isPlaybackActive?: boolean;
  onSegmentClick?: (startTimeSeconds: number, absoluteStartTime?: string) => void;
  onTranscribeComplete?: () => void;
}

export function TranscriptViewer({
  meeting,
  segments,
  chatMessages = [],
  isLoading,
  isLive,
  wsConnecting,
  wsConnected,
  wsError,
  wsReconnectAttempts,
  headerActions,
  topBarContent,
  playbackTime,
  playbackAbsoluteTime,
  isPlaybackActive,
  onSegmentClick,
  onTranscribeComplete,
}: TranscriptViewerProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedSpeakers, setSelectedSpeakers] = useState<string[]>([]);
  const [transcribeLanguage, setTranscribeLanguage] = useState("auto");
  const [isTranscribing, setIsTranscribing] = useState(false);
  // 話者編集（issue #24）: 会議後の文字起こしのみ編集可能
  const [selectedSegmentIds, setSelectedSegmentIds] = useState<Set<string>>(new Set());
  const [isMergeInputOpen, setIsMergeInputOpen] = useState(false);
  const [mergeTargetName, setMergeTargetName] = useState("");
  const [reassignTargetName, setReassignTargetName] = useState("");
  const [isUpdatingSpeakers, setIsUpdatingSpeakers] = useState(false);
  const [selectedAudioDialogOpen, setSelectedAudioDialogOpen] = useState(false);
  const [selectedAudioPreview, setSelectedAudioPreview] = useState<VoiceprintSegmentsPreview | null>(null);
  const [selectedAudioPreviewError, setSelectedAudioPreviewError] = useState<string | null>(null);
  const [isPreviewingSelectedAudio, setIsPreviewingSelectedAudio] = useState(false);
  const [isEnrollingSelectedAudio, setIsEnrollingSelectedAudio] = useState(false);
  const selectedAudioPreviewRunRef = useRef(0);
  // 声紋照合による命名候補（issue #27 Phase4）: 却下した候補はクライアント側で隠す。
  // BUG-010: クラスタIDのみでなく却下時の候補内容も保持し、文字起こし再取得時に
  // reconcileRejectedSuggestionsで再検証する（新しい照合実行の候補は再表示する）。
  const [rejectedSuggestionClusters, setRejectedSuggestionClusters] = useState<
    Map<string, RejectedSuggestionEntry>
  >(new Map());
  const searchInputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const userScrolledUpRef = useRef(false); // Track if user has manually scrolled away from bottom
  const lastScrollTopRef = useRef(0);
  const previousSegmentsLengthRef = useRef(0);

  // ChatGPT prompt state
  const [chatgptPrompt, setChatgptPrompt] = useState(() => {
    if (typeof window !== "undefined") {
      return getCookie("vexa-chatgpt-prompt") || "{url} を読んで、この会議内容について質問できるようにしてください。";
    }
    return "{url} を読んで、この会議内容について質問できるようにしてください。";
  });
  const [isChatgptPromptExpanded, setIsChatgptPromptExpanded] = useState(false);
  const [editedChatgptPrompt, setEditedChatgptPrompt] = useState(chatgptPrompt);
  const chatgptPromptTextareaRef = useRef<HTMLInputElement>(null);

  // Measure scroll container for auto-follow
  const isNearBottom = useCallback((el: HTMLElement) => {
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    return distanceFromBottom <= 50; // Allow some tolerance
  }, []);
  

  // Keyboard shortcut for search (Cmd/Ctrl + F)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "f") {
        e.preventDefault();
        searchInputRef.current?.focus();
      }
      if (e.key === "Escape" && document.activeElement === searchInputRef.current) {
        setSearchQuery("");
        searchInputRef.current?.blur();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  // Split text into sentence chunks for readability (max chars per chunk)
  const splitTextIntoSentenceChunks = useCallback((text: string, maxLen: number): string[] => {
    const normalized = (text || "").trim().replace(/\s+/g, " ");
    if (normalized.length <= maxLen) return [normalized];

    // Split into sentences on punctuation boundaries. Keep punctuation.
    const sentences = normalized.split(/(?<=[.!?])\s+/);
    if (sentences.length === 1) {
      // Single long sentence: return as one chunk to avoid breaking the sentence
      return [normalized];
    }

    const chunks: string[] = [];
    let current = "";
    for (const sentence of sentences) {
      if (current.length === 0) {
        if (sentence.length > maxLen) {
          // Sentence itself exceeds limit: do not split it
          chunks.push(sentence);
        } else {
          current = sentence;
        }
      } else if (current.length + 1 + sentence.length <= maxLen) {
        current = current + " " + sentence;
      } else {
        chunks.push(current);
        if (sentence.length > maxLen) {
          // Sentence exceeds limit: push as is to avoid breaking
          chunks.push(sentence);
          current = "";
        } else {
          current = sentence;
        }
      }
    }
    if (current.length > 0) chunks.push(current);
    return chunks;
  }, []);

  // ---- Timeline item types for unified transcript + chat rendering ----
  type TimelineItem =
    | { type: "transcript"; group: SegmentGroup<TranscriptSegmentType>; index: number }
    | { type: "chat"; message: ChatMessage };

  // Cleaned + time-sorted segments shared by grouping, speaker order, and
  // display-label assignment so all three agree on the same identity keys
  // and first-appearance order (excluding legacy [Chat] injected segments).
  // Dedup by segment_id when available, otherwise by absolute_start_time.
  const cleanedSortedSegments = useMemo(() => {
    const cleaned = segments.filter((seg) => !seg.text?.trimStart().startsWith("[Chat]") && seg.text?.trim());
    return sortByStartTime(deduplicateByIdentity(cleaned));
  }, [segments, segments.length]);

  // Identity key per unique speaker in order of appearance. Falls back to
  // speaker_cluster when speaker is unset so distinct unnamed lane
  // sub-clusters never collapse into the same identity (issue #26 B-4/FC-9).
  const speakerOrder = useMemo(() => {
    const speakers: string[] = [];
    for (const segment of cleanedSortedSegments) {
      const key = getSpeakerIdentityKey(segment);
      if (!speakers.includes(key)) {
        speakers.push(key);
      }
    }
    return speakers;
  }, [cleanedSortedSegments]);

  // identityキー → 表示ラベル（「要確認の話者A」等）。生のcluster idは
  // ここで一度だけ解決し、以降の描画はこのラベルのみを見る（B-4）。
  const speakerDisplayLabels = useMemo(
    () => buildSpeakerDisplayLabels(cleanedSortedSegments),
    [cleanedSortedSegments]
  );

  // Raw segments — no grouping, no merging. Each segment rendered individually.
  const groupedSegments = useMemo(() => {
    // Wrap each segment as its own group (1 segment per group). The group
    // key is the identity key (never a display label) — grouping, filter,
    // color, and consecutive-header logic all key off this value.
    return cleanedSortedSegments.map((seg): SegmentGroup<typeof seg> => ({
      key: getSpeakerIdentityKey(seg),
      startTime: seg.absolute_start_time,
      endTime: seg.absolute_end_time || seg.absolute_start_time,
      startTimeSeconds: seg.start_time ?? 0,
      endTimeSeconds: seg.end_time ?? 0,
      combinedText: (seg.text || "").trim(),
      segments: [seg],
    }));
  }, [cleanedSortedSegments]);

  const selectedVoiceprintSegments = useMemo(
    () => cleanedSortedSegments.filter(
      (segment) => !!segment.segment_id && selectedSegmentIds.has(segment.segment_id)
    ),
    [cleanedSortedSegments, selectedSegmentIds]
  );
  const selectedVoiceprintSegmentIds = useMemo(
    () => selectedVoiceprintSegments
      .map((segment) => segment.segment_id)
      .filter((segmentId): segmentId is string => !!segmentId),
    [selectedVoiceprintSegments]
  );
  const selectedVoiceprintTiming = useMemo(
    () => normalizeVoiceprintSelectionTiming(selectedVoiceprintSegments),
    [selectedVoiceprintSegments]
  );
  const selectedVoiceprintDurationSeconds = selectedVoiceprintTiming.durationSeconds;
  const selectedVoiceprintValidationMessage = useMemo(() => {
    if (selectedVoiceprintSegmentIds.length === 0) return "発話を1件以上選択してください";
    if (selectedVoiceprintSegmentIds.length > 20) return "選択できる発話は20件までです";
    const sessionUids = new Set(selectedVoiceprintSegments.map((segment) => segment.session_uid || ""));
    if (sessionUids.size !== 1 || sessionUids.has("")) {
      return "同じ録音セッションの発話だけを選択してください";
    }
    if (selectedVoiceprintTiming.hasInvalidTiming) return "発話の時間情報が不正です";
    if (selectedVoiceprintTiming.hasOverlap) return "時間が重なる発話は同時に選択できません";
    if (selectedVoiceprintDurationSeconds < 5) return "合計5秒以上の音声を選択してください";
    if (selectedVoiceprintDurationSeconds > 30) return "合計30秒以内の音声を選択してください";
    return null;
  }, [
    selectedVoiceprintDurationSeconds,
    selectedVoiceprintSegmentIds.length,
    selectedVoiceprintSegments,
    selectedVoiceprintTiming,
  ]);
  const selectedAudioSelectionKey = useMemo(
    () => `${meeting?.id || "none"}:${selectedVoiceprintSegmentIds.join("|")}`,
    [meeting?.id, selectedVoiceprintSegmentIds]
  );

  // Filter grouped segments by search query and selected speakers
  const filteredSegments = useMemo(() => {
    let result = groupedSegments;

    // Filter by selected speakers
    if (selectedSpeakers.length > 0) {
      result = result.filter((g) => selectedSpeakers.includes(g.key));
    }

    // Filter by search query
    if (searchQuery.trim()) {
      const query = searchQuery.toLowerCase();
      result = result.filter(
        (g) =>
          g.combinedText.toLowerCase().includes(query) ||
          resolveSpeakerLabelByKey(g.key, speakerDisplayLabels).toLowerCase().includes(query)
      );
    }

    return result;
  }, [groupedSegments, searchQuery, selectedSpeakers, speakerDisplayLabels]);

  // Build unified timeline: merge transcript groups + chat messages, sorted by time
  const timelineItems: TimelineItem[] = useMemo(() => {
    // Start with transcript groups
    const items: TimelineItem[] = filteredSegments.map((group, index) => ({
      type: "transcript" as const,
      group,
      index,
    }));

    // Add chat messages (optionally filtered by search query)
    if (chatMessages.length > 0) {
      const query = searchQuery.trim().toLowerCase();
      for (const msg of chatMessages) {
        if (query && !msg.text.toLowerCase().includes(query) && !msg.sender.toLowerCase().includes(query)) {
          continue;
        }
        items.push({ type: "chat" as const, message: msg });
      }
    }

    // Sort by timestamp: transcript groups use ISO startTime, chat messages use Unix ms
    items.sort((a, b) => {
      const timeA = a.type === "transcript"
        ? new Date(a.group.startTime).getTime()
        : a.message.timestamp;
      const timeB = b.type === "transcript"
        ? new Date(b.group.startTime).getTime()
        : b.message.timestamp;
      return timeA - timeB;
    });

    return items;
  }, [filteredSegments, chatMessages, searchQuery]);

  // Toggle speaker selection
  const toggleSpeaker = useCallback((speaker: string) => {
    setSelectedSpeakers((prev) =>
      prev.includes(speaker)
        ? prev.filter((s) => s !== speaker)
        : [...prev, speaker]
    );
  }, []);

  // Clear all filters
  const clearFilters = useCallback(() => {
    setSearchQuery("");
    setSelectedSpeakers([]);
  }, []);

  const handleTranscribe = useCallback(async () => {
    if (!meeting?.id) return;
    if (filteredSegments.length > 0 && !window.confirm("現在の文字起こしを、最新の辞書を使った結果で置き換えますか？")) return;
    setIsTranscribing(true);
    try {
      await vexaAPI.transcribeMeeting(
        meeting.id,
        transcribeLanguage === "auto" ? undefined : transcribeLanguage,
        "replace"
      );
      toast.info("再文字起こしを開始しました", { description: "画面を閉じても処理は継続します" });
      let completed = false;
      for (let attempt = 0; attempt < 1050; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 2000));
        const status = await vexaAPI.getTranscriptionStatus(meeting.id);
        if (status.status === "completed") {
          toast.success("文字起こしが完了しました", {
            description: `${status.segment_count ?? 0}件のセグメントを保存しました`,
          });
          completed = true;
          break;
        }
        if (["failed", "unknown_manual_reconcile"].includes(status.status)) {
          throw new Error(status.message || "再文字起こしの確認が必要です");
        }
      }
      if (!completed) throw new Error("処理状態の確認がタイムアウトしました");
      onTranscribeComplete?.();
    } catch (error) {
      toast.error("文字起こしに失敗しました", {
        description: (error as Error).message,
      });
    } finally {
      setIsTranscribing(false);
    }
  }, [meeting?.id, transcribeLanguage, onTranscribeComplete, filteredSegments.length]);

  const hasActiveFilters = searchQuery.trim() || selectedSpeakers.length > 0;

  // ---- 話者編集（issue #24 Phase 1c） ----
  const canEditSpeakers = !isLive && !isLoading && !!meeting?.id;

  const discardSelectedAudioPreview = useCallback(() => {
    selectedAudioPreviewRunRef.current += 1;
    setSelectedAudioPreview(null);
    setSelectedAudioPreviewError(null);
    setIsPreviewingSelectedAudio(false);
  }, []);

  const requestSelectedAudioPreview = useCallback(async () => {
    if (!meeting?.id || selectedVoiceprintSegmentIds.length === 0) return;
    const runId = selectedAudioPreviewRunRef.current + 1;
    selectedAudioPreviewRunRef.current = runId;
    setSelectedAudioPreview(null);
    setSelectedAudioPreviewError(null);
    setIsPreviewingSelectedAudio(true);
    try {
      const preview = await vexaAPI.previewVoiceprintFromSegments(
        meeting.id,
        selectedVoiceprintSegmentIds
      );
      if (selectedAudioPreviewRunRef.current !== runId) return;
      setSelectedAudioPreview(preview);
    } catch (error) {
      if (selectedAudioPreviewRunRef.current !== runId) return;
      setSelectedAudioPreviewError((error as Error).message);
    } finally {
      if (selectedAudioPreviewRunRef.current === runId) {
        setIsPreviewingSelectedAudio(false);
      }
    }
  }, [meeting?.id, selectedVoiceprintSegmentIds]);

  const openSelectedAudioVoiceprintDialog = useCallback(() => {
    if (selectedVoiceprintValidationMessage) {
      toast.error(selectedVoiceprintValidationMessage);
      return;
    }
    setSelectedAudioDialogOpen(true);
    void requestSelectedAudioPreview();
  }, [requestSelectedAudioPreview, selectedVoiceprintValidationMessage]);

  const handleSelectedAudioDialogOpenChange = useCallback((open: boolean) => {
    setSelectedAudioDialogOpen(open);
    if (!open) discardSelectedAudioPreview();
  }, [discardSelectedAudioPreview]);

  const handleSelectedAudioEnrollmentSubmit = useCallback(async (displayName: string) => {
    if (!meeting?.id || !selectedAudioPreview || selectedVoiceprintSegmentIds.length === 0) return;
    setIsEnrollingSelectedAudio(true);
    try {
      await vexaAPI.enrollVoiceprintFromSegments(
        meeting.id,
        selectedVoiceprintSegmentIds,
        displayName,
        selectedAudioPreview.clip_sha256,
        selectedAudioPreview.source_fingerprint
      );
      toast.success("声紋を登録しました", {
        description: `選択した音声を「${displayName}」として今後の話者候補に使います`,
      });
      setSelectedAudioDialogOpen(false);
      discardSelectedAudioPreview();
      setSelectedSegmentIds(new Set());
    } catch (error) {
      toast.error("声紋の登録に失敗しました", {
        description: (error as Error).message,
      });
    } finally {
      setIsEnrollingSelectedAudio(false);
    }
  }, [
    discardSelectedAudioPreview,
    meeting?.id,
    selectedAudioPreview,
    selectedVoiceprintSegmentIds,
  ]);

  // 選択が変わった時点で、以前のpreview hashと確認状態は再利用できない。
  useEffect(() => {
    setSelectedAudioDialogOpen(false);
    discardSelectedAudioPreview();
  }, [discardSelectedAudioPreview, selectedAudioSelectionKey]);

  const applySpeakerUpdate = useCallback(
    async (payload: SpeakerUpdatePayload | null) => {
      if (!payload || !meeting?.id) return false;
      setIsUpdatingSpeakers(true);
      try {
        const result = await vexaAPI.updateSpeakers(meeting.id, payload);
        toast.success("話者を更新しました", {
          description: describeSpeakerUpdate(result.updated),
        });
        setSelectedSegmentIds(new Set());
        setIsMergeInputOpen(false);
        setMergeTargetName("");
        setReassignTargetName("");
        // 旧話者名のフィルタが残ると改名後のセグメントが隠れるため解除する
        setSelectedSpeakers([]);
        onTranscribeComplete?.();
        return true;
      } catch (error) {
        toast.error("話者の更新に失敗しました", {
          description: (error as Error).message,
        });
        return false;
      } finally {
        setIsUpdatingSpeakers(false);
      }
    },
    [meeting?.id, onTranscribeComplete]
  );

  // 声紋照合による候補の承認/却下（issue #27 Phase4）
  const handleAcceptSuggestion = useCallback(
    (clusterId: string, candidateName: string) => {
      void applySpeakerUpdate(
        buildSpeakerRename({ speaker: "", speaker_cluster: clusterId }, candidateName)
      );
    },
    [applySpeakerUpdate]
  );

  const handleRejectSuggestion = useCallback(
    (clusterId: string, suggestion: SpeakerSuggestion) => {
      if (!meeting?.id) return;
      // 楽観的にチップを消す（失敗時は元に戻す）。BUG-010: どの候補を却下
      // したかも保持し、再取得時に新しい候補と区別できるようにする。
      setRejectedSuggestionClusters((prev) => {
        const next = new Map(prev);
        next.set(clusterId, {
          candidateDisplayName: suggestion.candidate_display_name,
          similarity: suggestion.similarity,
        });
        return next;
      });
      void vexaAPI.rejectSpeakerSuggestion(meeting.id, clusterId).catch((error) => {
        setRejectedSuggestionClusters((prev) => {
          const next = new Map(prev);
          next.delete(clusterId);
          return next;
        });
        toast.error("候補の却下に失敗しました", {
          description: (error as Error).message,
        });
      });
    },
    [meeting?.id]
  );

  // BUG-010: 文字起こしが再取得されるたびに却下フラグを最新のsuggestionと
  // 突き合わせ、サーバ側で反映済み/新しい照合実行の候補は追跡を外す。
  useEffect(() => {
    setRejectedSuggestionClusters((prev) => reconcileRejectedSuggestions(prev, segments));
  }, [segments]);

  const toggleGroupSelection = useCallback((segmentIds: string[]) => {
    setSelectedSegmentIds((prev) => {
      const next = new Set(prev);
      const allSelected = segmentIds.every((id) => next.has(id));
      for (const id of segmentIds) {
        if (allSelected) next.delete(id);
        else next.add(id);
      }
      return next;
    });
  }, []);

  const handleReassignSelected = useCallback(() => {
    void applySpeakerUpdate(
      buildSegmentReassign([...selectedSegmentIds], reassignTargetName)
    );
  }, [applySpeakerUpdate, selectedSegmentIds, reassignTargetName]);

  const handleMergeSelectedSpeakers = useCallback(() => {
    void applySpeakerUpdate(
      buildSpeakerMerge(selectedSpeakers, segments, mergeTargetName)
    );
  }, [applySpeakerUpdate, selectedSpeakers, segments, mergeTargetName]);

  // Handle scroll events to detect when user scrolls up
  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;

    const currentScrollTop = el.scrollTop;
    const prevScrollTop = lastScrollTopRef.current;
    lastScrollTopRef.current = currentScrollTop;

    // If user scrolled up, mark them as having scrolled away from bottom
    if (currentScrollTop < prevScrollTop) {
      userScrolledUpRef.current = true;
    }

    // If user is back at bottom, resume auto-scrolling
    if (isNearBottom(el)) {
      userScrolledUpRef.current = false;
    }
  }, [isNearBottom]);



  // Auto-scroll to bottom when new segments arrive, unless user has scrolled up
  useLayoutEffect(() => {
    if (!isLive) return;

    const el = scrollRef.current;
    if (!el) return;

    // Only auto-scroll when new segments are actually added (not initial load)
    const hasNewSegments = segments.length > previousSegmentsLengthRef.current;
    previousSegmentsLengthRef.current = segments.length;

    if (!hasNewSegments) return;

    // Don't auto-scroll if user has manually scrolled up
    if (userScrolledUpRef.current) return;

    // Only scroll if we're actually near the bottom
    // This prevents scrolling when user is reading older content
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    const shouldScroll = distanceFromBottom <= 100; // Allow more tolerance

    if (!shouldScroll) return;

    // Small delay to ensure DOM has updated
    requestAnimationFrame(() => {
      // Double-check element still exists and we're still in live mode
      if (!el || !isLive) return;

      // Scroll to bottom by default (like a chat app)
      bottomRef.current?.scrollIntoView({ block: "end", behavior: "auto" });
    });
  }, [isLive, segments.length]);

  // Find the active segment index during playback.
  // When playbackAbsoluteTime is available (multi-fragment mode), use absolute
  // timestamp comparison. Otherwise fall back to relative time comparison.
  const activePlaybackIndex = useMemo(() => {
    if (!isPlaybackActive) return -1;

    // Absolute time matching (multi-fragment safe)
    if (playbackAbsoluteTime) {
      const pbTime = new Date(playbackAbsoluteTime).getTime();
      for (let i = filteredSegments.length - 1; i >= 0; i--) {
        const group = filteredSegments[i];
        const groupStart = new Date(group.startTime).getTime();
        const groupEnd = new Date(group.endTime).getTime();
        if (groupStart <= pbTime) {
          // Within this group's range (with 1s tolerance)
          if (pbTime <= groupEnd + 1000) return i;
          // Between this group and the next
          if (i < filteredSegments.length - 1) {
            const nextStart = new Date(filteredSegments[i + 1].startTime).getTime();
            if (pbTime < nextStart) return i;
          }
          // Past the last group
          if (i === filteredSegments.length - 1) return i;
          return -1;
        }
      }
      return -1;
    }

    // Fallback: relative time matching (single-fragment)
    if (playbackTime == null) return -1;
    for (let i = filteredSegments.length - 1; i >= 0; i--) {
      const group = filteredSegments[i];
      if (group.startTimeSeconds <= playbackTime) {
        if (playbackTime <= group.endTimeSeconds + 1) return i;
        if (i < filteredSegments.length - 1) {
          const nextGroup = filteredSegments[i + 1];
          if (playbackTime < nextGroup.startTimeSeconds) return i;
        }
        if (i === filteredSegments.length - 1) return i;
        return -1;
      }
    }
    return -1;
  }, [playbackTime, playbackAbsoluteTime, isPlaybackActive, filteredSegments]);

  // Auto-scroll to active playback segment
  const activeSegmentRef = useRef<HTMLDivElement>(null);
  const lastScrolledIndexRef = useRef(-1);

  useEffect(() => {
    if (activePlaybackIndex < 0 || !isPlaybackActive) return;
    // Only scroll when the active segment changes (not on every time update)
    if (activePlaybackIndex === lastScrolledIndexRef.current) return;
    lastScrolledIndexRef.current = activePlaybackIndex;

    // Use a small delay to let the DOM update
    requestAnimationFrame(() => {
      activeSegmentRef.current?.scrollIntoView({
        behavior: "smooth",
        block: "center",
      });
    });
  }, [activePlaybackIndex, isPlaybackActive]);

  // Export handlers
  const handleExport = (format: "txt" | "json" | "srt" | "vtt") => {
    let content: string;
    let mimeType: string;

    switch (format) {
      case "txt":
        content = exportToTxt(meeting, segments);
        mimeType = "text/plain";
        break;
      case "json":
        content = exportToJson(meeting, segments);
        mimeType = "application/json";
        break;
      case "srt":
        content = exportToSrt(segments);
        mimeType = "text/plain";
        break;
      case "vtt":
        content = exportToVtt(segments);
        mimeType = "text/vtt";
        break;
    }

    const filename = generateFilename(meeting, format);
    downloadFile(content, filename, mimeType);
  };

  // Format transcript for ChatGPT
  const formatTranscriptForChatGPT = useCallback(() => {
    let output = "会議文字起こし\n\n";
    
    if (meeting.data?.name || meeting.data?.title) {
      output += `タイトル: ${meeting.data?.name || meeting.data?.title}\n`;
    }
    
    if (meeting.start_time) {
      // v0.10.5.3 Pack D-1 (#265): parseUTCTimestamp for unsuffixed-ISO API timestamps.
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
          // sees on screen (e.g. "2026-05-01 14:32:11" not "11:32:11Z").
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
  }, [meeting, segments]);

  // Handle sending transcript to an AI provider (ChatGPT or Perplexity)
  const handleOpenInProvider = useCallback(async (provider: "chatgpt" | "perplexity") => {
    if (segments.length === 0) return;

    // Prefer link-based flow
    try {
      const response = await fetch(
        `/api/vexa/transcripts/${meeting.platform}/${meeting.platform_specific_id}/share?meeting_id=${encodeURIComponent(meeting.id)}`,
        { method: "POST" }
      );
      if (response.ok) {
        const share = (await response.json()) as { url: string; share_id?: string };
        if (share?.url) {
          const publicBase = process.env.NEXT_PUBLIC_TRANSCRIPT_SHARE_BASE_URL?.replace(/\/$/, "");
          const shareUrl =
            publicBase && share.share_id
              ? `${publicBase}/public/transcripts/${share.share_id}.txt`
              : share.url;

          // Use custom prompt, replacing {url} placeholder
          const prompt = chatgptPrompt.replace(/{url}/g, shareUrl);
          
          let providerUrl: string;
          if (provider === "chatgpt") {
            providerUrl = `https://chatgpt.com/?hints=search&q=${encodeURIComponent(prompt)}`;
          } else {
            providerUrl = `https://www.perplexity.ai/search?q=${encodeURIComponent(prompt)}`;
          }
          
          window.open(providerUrl, "_blank", "noopener,noreferrer");
          return;
        }
      }
    } catch (err) {
      console.error("Failed to create transcript share link:", err);
    }

    // Fallback: clipboard flow
    try {
      const transcriptText = formatTranscriptForChatGPT();
      await navigator.clipboard.writeText(transcriptText);
      const q = "会議の文字起こしをクリップボードにコピーしました。これから貼り付けるので、その内容について質問できるようにしてください。";
      let providerUrl: string;
      if (provider === "chatgpt") {
        providerUrl = `https://chatgpt.com/?hints=search&q=${encodeURIComponent(q)}`;
      } else {
        providerUrl = `https://www.perplexity.ai/search?q=${encodeURIComponent(q)}`;
      }
      setTimeout(() => window.open(providerUrl, "_blank", "noopener,noreferrer"), 100);
    } catch (error) {
      console.error("Failed to copy transcript to clipboard:", error);
    }
  }, [segments, formatTranscriptForChatGPT, meeting.id, meeting.platform, meeting.platform_specific_id, chatgptPrompt]);

  // Handle saving ChatGPT prompt to cookie
  const handleChatgptPromptBlur = useCallback(() => {
    const trimmed = editedChatgptPrompt.trim();
    if (trimmed && trimmed !== chatgptPrompt) {
      setChatgptPrompt(trimmed);
      setCookie("vexa-chatgpt-prompt", trimmed);
    }
  }, [editedChatgptPrompt, chatgptPrompt]);

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>文字起こし</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {[...Array(5)].map((_, i) => (
              <div key={i} className="flex gap-3">
                <Skeleton className="h-8 w-8 rounded-full" />
                <div className="flex-1 space-y-2">
                  <Skeleton className="h-4 w-24" />
                  <Skeleton className="h-4 w-full" />
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="flex flex-col h-full flex-1 min-h-0">
      <CardHeader className="flex-shrink-0 space-y-1.5 py-2">
        {/* Thin playback strip (separate row) */}
        {topBarContent && (
          <div className="mb-1">
            {topBarContent}
          </div>
        )}

        {/* Search and Filter Bar */}
        <div className="flex flex-wrap items-center gap-1.5 lg:gap-2">
          {/* Search */}
          <div className="relative flex-1 min-w-[150px] lg:min-w-[200px]">
            <Search className="absolute left-2 lg:left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 lg:h-4 lg:w-4 text-muted-foreground" />
            <Input
              ref={searchInputRef}
              placeholder="検索... (Cmd+F)"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className={cn(
                "h-7 lg:h-8 pl-7 lg:pl-9 pr-7 lg:pr-9 text-xs lg:text-sm transition-all",
                searchQuery && "ring-2 ring-primary/20"
              )}
            />
            {searchQuery && (
              <Button
                variant="ghost"
                size="icon"
                className="absolute right-0.5 lg:right-1 top-1/2 -translate-y-1/2 h-6 w-6 lg:h-7 lg:w-7"
                onClick={() => setSearchQuery("")}
              >
                <X className="h-3 w-3 lg:h-4 lg:w-4" />
              </Button>
            )}
          </div>

          {/* Speaker Filter */}
          {speakerOrder.length > 0 && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="outline"
                  size="sm"
                  className={cn(
                    "h-7 lg:h-8 px-2 lg:px-3 text-xs lg:text-sm gap-1 lg:gap-2",
                    selectedSpeakers.length > 0 && "border-primary text-primary"
                  )}
                >
                  <Users className="h-3.5 w-3.5 lg:h-4 lg:w-4" />
                  <span className="hidden sm:inline">話者</span>
                  {selectedSpeakers.length > 0 && (
                    <Badge variant="secondary" className="ml-0.5 lg:ml-1 h-4 lg:h-5 px-1 lg:px-1.5 text-[10px] lg:text-xs">
                      {selectedSpeakers.length}
                    </Badge>
                  )}
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-56">
                {speakerOrder.map((speaker) => {
                  const color = getSpeakerColor(speaker, speakerOrder);
                  const label = resolveSpeakerLabelByKey(speaker, speakerDisplayLabels);
                  return (
                    <DropdownMenuCheckboxItem
                      key={speaker}
                      checked={selectedSpeakers.includes(speaker)}
                      onCheckedChange={() => toggleSpeaker(speaker)}
                    >
                      <div className="flex items-center gap-2">
                        <div className={cn("w-2 h-2 rounded-full", color.avatar)} />
                        <span className="truncate">{label}</span>
                      </div>
                    </DropdownMenuCheckboxItem>
                  );
                })}
                {selectedSpeakers.length > 0 && (
                  <>
                    <DropdownMenuSeparator />
                    {canEditSpeakers && selectedSpeakers.length >= 2 && (
                      <DropdownMenuItem onClick={() => setIsMergeInputOpen(true)}>
                        選択した話者を統合...
                      </DropdownMenuItem>
                    )}
                    <DropdownMenuItem onClick={() => setSelectedSpeakers([])}>
                      選択を解除
                    </DropdownMenuItem>
                  </>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
          )}

          {/* Clear all filters */}
          {hasActiveFilters && (
            <Button
              variant="ghost"
              size="sm"
              onClick={clearFilters}
              className="text-muted-foreground hover:text-foreground"
            >
              <X className="h-4 w-4 mr-1" />
              フィルター解除
            </Button>
          )}

          {!isLive && filteredSegments.length > 0 && meeting?.id && (
            <Button
              variant="outline"
              size="sm"
              className="h-7 lg:h-8 gap-1.5"
              disabled={isTranscribing}
              onClick={handleTranscribe}
            >
              {isTranscribing ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
              <span className="hidden lg:inline">{isTranscribing ? "再文字起こし中" : "辞書を反映して再文字起こし"}</span>
            </Button>
          )}

          {/* Header actions (e.g., DocsLink) */}
          {headerActions && (
            <div className="ml-auto">
              {headerActions}
            </div>
          )}
        </div>

        {/* Filter results info */}
        {hasActiveFilters && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground animate-fade-in">
            <span>
              {groupedSegments.length + chatMessages.length}件中 {timelineItems.length}件を表示
              {chatMessages.length > 0 && `（チャット${chatMessages.length}件）`}
            </span>
            {searchQuery && (
              <Badge variant="outline" className="font-normal">
                &quot;{searchQuery}&quot;
              </Badge>
            )}
            {selectedSpeakers.map((speaker) => (
              <Badge
                key={speaker}
                variant="secondary"
                className="font-normal cursor-pointer hover:bg-destructive/20"
                onClick={() => toggleSpeaker(speaker)}
              >
                {resolveSpeakerLabelByKey(speaker, speakerDisplayLabels)}
                <X className="h-3 w-3 ml-1" />
              </Badge>
            ))}
          </div>
        )}

        {/* 話者統合の入力行（フィルタで選択した話者を1名にまとめる） */}
        {isMergeInputOpen && (
          <div className="flex flex-wrap items-center gap-2 text-sm animate-fade-in">
            <span className="text-muted-foreground">
              選択した{selectedSpeakers.length}名の話者を統合:
            </span>
            <Input
              autoFocus
              value={mergeTargetName}
              onChange={(e) => setMergeTargetName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && mergeTargetName.trim()) handleMergeSelectedSpeakers();
                if (e.key === "Escape") setIsMergeInputOpen(false);
              }}
              placeholder="統合後の話者名"
              className="h-7 w-44 text-sm"
            />
            <Button
              size="sm"
              className="h-7"
              disabled={isUpdatingSpeakers || !mergeTargetName.trim() || selectedSpeakers.length < 2}
              onClick={handleMergeSelectedSpeakers}
            >
              {isUpdatingSpeakers ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "統合"}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-7"
              onClick={() => setIsMergeInputOpen(false)}
            >
              キャンセル
            </Button>
          </div>
        )}

        {/* 選択発話の話者変更 / ユーザー確認済み音声からの声紋登録 */}
        {selectedSegmentIds.size > 0 && (
          <div className="flex flex-wrap items-center gap-2 text-sm animate-fade-in">
            <Badge variant="secondary" className="font-normal">
              {selectedVoiceprintSegmentIds.length}件・{selectedVoiceprintDurationSeconds.toFixed(1)}秒選択中
            </Badge>
            <Button
              type="button"
              size="sm"
              className="h-7"
              disabled={
                !!selectedVoiceprintValidationMessage
                || isPreviewingSelectedAudio
                || isEnrollingSelectedAudio
              }
              title={selectedVoiceprintValidationMessage || "選択した音声だけを確認して声紋登録します"}
              onClick={openSelectedAudioVoiceprintDialog}
            >
              {isPreviewingSelectedAudio
                ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                : "選択した音声で声紋登録"}
            </Button>
            {selectedVoiceprintValidationMessage && (
              <span className="text-xs text-amber-600 dark:text-amber-400">
                {selectedVoiceprintValidationMessage}
              </span>
            )}
            <Input
              value={reassignTargetName}
              onChange={(e) => setReassignTargetName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && reassignTargetName.trim()) handleReassignSelected();
                if (e.key === "Escape") setSelectedSegmentIds(new Set());
              }}
              placeholder="変更後の話者名"
              className="h-7 w-44 text-sm"
            />
            <Button
              size="sm"
              className="h-7"
              disabled={isUpdatingSpeakers || !reassignTargetName.trim()}
              onClick={handleReassignSelected}
            >
              {isUpdatingSpeakers ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "まとめて話者変更"}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-7"
              onClick={() => setSelectedSegmentIds(new Set())}
            >
              選択解除
            </Button>
          </div>
        )}
      </CardHeader>

      {/* Collapsible ChatGPT Prompt Section */}
      {isChatgptPromptExpanded && (
        <div className="px-6 pb-4 animate-in slide-in-from-top-2 duration-200">
          <div className="bg-muted/30 rounded-lg border p-3 space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium flex items-center gap-2">
                <Settings className="h-4 w-4" />
                AIプロンプト
              </span>
              <Button
                variant="ghost"
                size="sm"
                className="h-6 w-6 p-0"
                onClick={() => setIsChatgptPromptExpanded(false)}
              >
                <X className="h-4 w-4" />
              </Button>
            </div>
            <div className="space-y-2">
              <Input
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
                className="text-sm"
                autoFocus
              />
              <p className="text-[10px] text-muted-foreground">
                文字起こしリンクの差し込み位置として <code className="px-1 py-0.5 bg-muted rounded">{"{url}"}</code> を使います。
              </p>
            </div>
          </div>
        </div>
      )}

      <CardContent className="flex-1 min-h-0 flex flex-col overflow-hidden">
        <div
          ref={scrollRef}
          onScroll={handleScroll}
          className="flex-1 min-h-0 pr-4 overflow-y-auto"
        >
          {timelineItems.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-center animate-fade-in">
              {meeting.data?.transcribe_enabled === false && !isLive && !hasActiveFilters ? (
                <>
                  <div className="w-16 h-16 rounded-full bg-muted/50 flex items-center justify-center mb-4">
                    <Mic className="h-8 w-8 text-muted-foreground/50" />
                  </div>
                  <h3 className="font-medium mb-1">録音があります</h3>
                  <p className="text-sm text-muted-foreground mb-6">
                    この会議はリアルタイム文字起こしなしで録音されています。
                  </p>
                  {isTranscribing ? (
                    <div className="flex items-center gap-2 text-muted-foreground">
                      <Loader2 className="h-4 w-4 animate-spin" />
                      <span className="text-sm">録音を文字起こし中...</span>
                    </div>
                  ) : (
                    <div className="flex flex-col items-center gap-3">
                      <div className="w-48">
                        <LanguagePicker value={transcribeLanguage} onValueChange={setTranscribeLanguage} />
                      </div>
                      <Button onClick={handleTranscribe}>
                        録音を文字起こし
                      </Button>
                    </div>
                  )}
                </>
              ) : hasActiveFilters ? (
                <>
                  <div className="w-16 h-16 rounded-full bg-muted/50 flex items-center justify-center mb-4">
                    <Search className="h-8 w-8 text-muted-foreground/50" />
                  </div>
                  <h3 className="font-medium mb-1">結果が見つかりません</h3>
                  <p className="text-sm text-muted-foreground mb-4">
                    検索語やフィルターを変更してください
                  </p>
                  <Button variant="outline" size="sm" onClick={clearFilters}>
                    すべてのフィルターを解除
                  </Button>
                </>
              ) : isLive && meeting.data?.transcribe_enabled === false ? (
                <>
                  <div className="w-16 h-16 rounded-full bg-red-50 dark:bg-red-950/30 flex items-center justify-center mb-4 relative">
                    <Mic className="h-8 w-8 text-red-500" />
                    <span className="absolute top-1 right-1 h-3 w-3 rounded-full bg-red-500 animate-pulse" />
                  </div>
                  <h3 className="font-medium mb-1">録音中</h3>
                  <p className="text-sm text-muted-foreground">
                    音声を録音しています。会議終了後に文字起こしを実行します。
                  </p>
                </>
              ) : (
                <>
                  <div className="w-16 h-16 rounded-full bg-muted/50 flex items-center justify-center mb-4">
                    <MessageSquare className="h-8 w-8 text-muted-foreground/50" />
                  </div>
                  <h3 className="font-medium mb-1">文字起こしはまだありません</h3>
                  <p className="text-sm text-muted-foreground">
                    {isLive
                      ? "発話を待っています..."
                      : "この会議で利用できる文字起こしはありません"}
                  </p>
                </>
              )}
            </div>
          ) : (
            <div>
              {timelineItems.map((item, idx) => {
                // ---- Chat message item ----
                if (item.type === "chat") {
                  const msg = item.message;
                  // v0.10.5.3 Pack D-1 follow-up: msg.timestamp is a numeric
                  // epoch (Unix ms, see ChatMessage type) so new Date(ms)
                  // positions correctly with no tz interpretation needed —
                  // unlike absolute_start_time which is an unsuffixed-ISO string.
                  // Render with getHours/Minutes/Seconds (NOT getUTC*) so chat
                  // bubbles match transcript segment timestamps in browser-local tz.
                  const chatTime = new Date(msg.timestamp);
                  const hh = chatTime.getHours().toString().padStart(2, "0");
                  const mm = chatTime.getMinutes().toString().padStart(2, "0");
                  const ss = chatTime.getSeconds().toString().padStart(2, "0");
                  const displayTime = `${hh}:${mm}:${ss}`;

                  return (
                    <div
                      key={`chat-${msg.timestamp}-${idx}`}
                      className={cn(
                        "animate-fade-in flex gap-3 p-3 rounded-lg bg-sky-50/60 dark:bg-sky-950/20 border border-sky-200/50 dark:border-sky-800/30",
                        idx > 0 && "mt-4"
                      )}
                    >
                      {/* Chat icon instead of avatar */}
                      <div className="h-8 w-8 flex-shrink-0 rounded-full bg-sky-500 flex items-center justify-center">
                        <MessageSquare className="h-4 w-4 text-white" />
                      </div>

                      {/* Content */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1">
                          <span className="font-medium text-sm text-sky-700 dark:text-sky-400">
                            {msg.sender}
                          </span>
                          <Badge variant="outline" className="text-[10px] h-4 px-1.5 border-sky-300 dark:border-sky-700 text-sky-600 dark:text-sky-400">
                            チャット
                          </Badge>
                          <span className="text-xs text-muted-foreground">
                            {displayTime}
                          </span>
                        </div>
                        <p className="text-sm leading-relaxed">
                          {linkifyText(msg.text, searchQuery || undefined)}
                        </p>
                      </div>
                    </div>
                  );
                }

                // ---- Transcript group item ----
                const { group, index } = item;

                // Create a synthetic segment for the grouped segment.
                // `speaker` here is the resolved DISPLAY LABEL (never the raw
                // group.key/cluster id) — issue #26 B-4. Identity for
                // grouping/filter/color/header logic stays on group.key.
                const syntheticSegment: TranscriptSegmentType = {
                  id: `${group.startTime}-${index}`,
                  meeting_id: meeting.id,
                  start_time: group.startTimeSeconds,
                  end_time: group.endTimeSeconds,
                  absolute_start_time: group.startTime,
                  absolute_end_time: group.endTime,
                  text: group.combinedText,
                  speaker: getSpeakerDisplayLabel(group.segments[0], speakerDisplayLabels),
                  language: group.segments[0]?.language || "ja",
                  completed: group.segments.every(s => s.completed !== false),
                  session_uid: group.segments[0]?.session_uid || "",
                  created_at: group.startTime,
                  speaker_cluster: group.segments[0]?.speaker_cluster,
                  speaker_auto: group.segments[0]?.speaker_auto,
                  speaker_mapping_status: group.segments[0]?.speaker_mapping_status,
                  // 声紋照合による命名候補（issue #27 Phase4）: 却下済みクラスタは非表示にする
                  speaker_suggestion: rejectedSuggestionClusters.has(group.key)
                    ? undefined
                    : group.segments[0]?.speaker_suggestion,
                };

                const isActivePlayback = activePlaybackIndex === index;

                // 話者編集: PGに永続化された行（segment_idあり）のみ対象
                const groupSegmentIds = group.segments
                  .map((s) => s.segment_id)
                  .filter((id): id is string => !!id);
                const isGroupSelectable = canEditSpeakers && groupSegmentIds.length > 0;
                const isGroupSelected =
                  isGroupSelectable &&
                  groupSegmentIds.every((id) => selectedSegmentIds.has(id));

                // Determine if this is a continuation from the same speaker
                // by looking at the previous timeline item. A same-speaker run
                // is only merged into one tight visual block when the silence
                // gap between the two segments is small — a long pause (e.g.
                // the speaker went quiet for a while and picked back up) still
                // starts a new block so the timeline reads correctly.
                let showSpeakerHeader = true;
                if (idx > 0) {
                  const prevItem = timelineItems[idx - 1];
                  if (prevItem.type === "transcript") {
                    // Compare identity keys, not display labels (issue #26
                    // B-4): grouping/header logic must key off group.key so
                    // distinct unnamed sub-clusters never collapse together.
                    const sameSpeaker = prevItem.group.key === group.key;
                    const prevEndMs = new Date(prevItem.group.endTime).getTime();
                    const currStartMs = new Date(group.startTime).getTime();
                    const gapMs = Number.isFinite(prevEndMs) && Number.isFinite(currStartMs)
                      ? currStartMs - prevEndMs
                      : 0;
                    const isLargeGap = gapMs >= SPEAKER_BLOCK_GAP_MS;
                    if (sameSpeaker && !isLargeGap) {
                      showSpeakerHeader = false;
                    }
                  }
                }

                // New blocks (speaker change, or a long silence gap even for
                // the same speaker) get a larger top margin so they read as
                // separate turns; continuations within the same block stay tight.
                const isNewBlock = showSpeakerHeader;

                return (
                  <div
                    key={`${group.startTime}-${index}`}
                    ref={isActivePlayback ? activeSegmentRef : undefined}
                    className={cn("animate-fade-in", idx > 0 && (isNewBlock ? "mt-4" : "mt-0.5"))}
                    style={{
                      animationDelay: isLive ? "0ms" : `${Math.min(index * 20, 200)}ms`,
                      animationFillMode: "backwards",
                    }}
                  >
                    <TranscriptSegment
                      segment={syntheticSegment}
                      speakerColor={getSpeakerColor(group.key, speakerOrder)}
                      searchQuery={searchQuery}
                      isHighlighted={searchQuery.length > 0}
                      isActivePlayback={isActivePlayback}
                      onClickSegment={onSegmentClick ? () => onSegmentClick(group.startTimeSeconds, group.startTime) : undefined}
                      showSpeakerHeader={showSpeakerHeader}
                      canEdit={canEditSpeakers}
                      onSpeakerEdit={(toName, scope) => {
                        if (scope === "speaker") {
                          void applySpeakerUpdate(
                            buildSpeakerRename(
                              {
                                speaker: syntheticSegment.speaker,
                                speaker_cluster: syntheticSegment.speaker_cluster,
                              },
                              toName
                            )
                          );
                        } else {
                          void applySpeakerUpdate(
                            buildSegmentReassign(groupSegmentIds, toName)
                          );
                        }
                      }}
                      isSelected={isGroupSelected}
                      onToggleSelect={
                        isGroupSelectable
                          ? () => toggleGroupSelection(groupSegmentIds)
                          : undefined
                      }
                      onAcceptSuggestion={
                        syntheticSegment.speaker_suggestion
                          ? () =>
                              handleAcceptSuggestion(
                                syntheticSegment.speaker_cluster || group.key,
                                syntheticSegment.speaker_suggestion!.candidate_display_name
                              )
                          : undefined
                      }
                      onRejectSuggestion={
                        syntheticSegment.speaker_suggestion
                          ? () => handleRejectSuggestion(
                              syntheticSegment.speaker_cluster || group.key,
                              syntheticSegment.speaker_suggestion!
                            )
                          : undefined
                      }
                    />
                  </div>
                );
              })}
            </div>
          )}
          {/* Bottom sentinel for reliable auto-follow scrolling */}
          <div ref={bottomRef} />
        </div>
      </CardContent>
      <SelectedAudioVoiceprintDialog
        key={`${selectedAudioSelectionKey}:${selectedAudioDialogOpen ? "open" : "closed"}:${selectedAudioPreview?.source_fingerprint ?? "no-source"}:${selectedAudioPreview?.clip_sha256 ?? "no-clip"}`}
        open={selectedAudioDialogOpen}
        selectedCount={selectedVoiceprintSegmentIds.length}
        selectedDurationSeconds={selectedVoiceprintDurationSeconds}
        preview={selectedAudioPreview}
        previewing={isPreviewingSelectedAudio}
        previewError={selectedAudioPreviewError}
        submitting={isEnrollingSelectedAudio}
        onOpenChange={handleSelectedAudioDialogOpenChange}
        onRetryPreview={() => void requestSelectedAudioPreview()}
        onSubmit={handleSelectedAudioEnrollmentSubmit}
      />
    </Card>
  );
}
