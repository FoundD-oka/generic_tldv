// Vexa API Types

export type Platform = "google_meet" | "teams" | "zoom" | "browser_session";

export type MeetingStatus =
  | "requested"
  | "joining"
  | "awaiting_admission"
  | "active"
  | "needs_human_help"
  | "stopping"
  | "completed"
  | "failed";

export interface Meeting {
  id: string;
  platform: Platform;
  platform_specific_id: string;
  status: MeetingStatus;
  start_time: string | null;
  end_time: string | null;
  bot_container_id: string | null;
  data: MeetingData;
  created_at: string;
  updated_at?: string;
}

// Status transition record from Vexa API
export interface StatusTransition {
  from: MeetingStatus | string;
  to: MeetingStatus | string;
  timestamp: string;
  source?: string;
  reason?: string;
  completion_reason?: string;
  container_id?: string;
  finalized_by?: string;
}

export interface MeetingData {
  name?: string;
  title?: string;
  calendar_title?: string;
  calendar_event?: {
    title?: string;
    [key: string]: unknown;
  };
  notes?: string;
  participants?: string[];
  languages?: string[];
  // Bot status details (may be populated by Vexa API)
  error?: string;
  error_code?: string;
  status_message?: string;
  failure_reason?: string;
  // Completion details
  completion_reason?: string;
  // Status history
  status_transition?: StatusTransition[];
  [key: string]: unknown;
}

/**
 * 声紋照合による命名候補（issue #27 Phase4）。`profile_id`は意図的に含めない
 * — サーバ側もtranscript応答へは最小payloadのみ返す（露出制御）。
 */
export interface SpeakerSuggestion {
  candidate_display_name: string;
  similarity: number;
  status: "suggested";
}

export interface TranscriptSegment {
  id: string;
  meeting_id: string;
  start_time: number;
  end_time: number;
  absolute_start_time: string;
  absolute_end_time: string;
  text: string;
  speaker: string;
  language: string;
  completed?: boolean;
  session_uid: string;
  speaker_mapping_status?: string;
  track_id?: string;
  speaker_track_id?: string;
  created_at: string;
  updated_at?: string;
  /** Stable segment identity from bot: {session_uid}:{speakerId}:{seq} */
  segment_id?: string;
  /** Acoustic cluster id from STT diarization (anonymous, per-meeting) */
  speaker_cluster?: string;
  /** Original auto-assigned speaker label (undo baseline) */
  speaker_auto?: string;
  /** 声紋照合による命名候補（issue #27 Phase4）。承認まで`speaker`は書き換えない。 */
  speaker_suggestion?: SpeakerSuggestion;
}

/** Payload for PATCH /meetings/{id}/transcripts/speakers (issue #23) */
export interface SpeakerUpdatePayload {
  rename?: Array<{ from_cluster?: string; from_name?: string; to_name: string }>;
  merge?: Array<{ clusters: string[]; to_name: string; to_cluster?: string }>;
  reassign?: Array<{ segment_ids: string[]; to_name: string; to_cluster?: string }>;
}

/**
 * PATCH応答が反映したクラスタ（issue #27 Phase4, ARC-6）。rename/merge/reassignを
 * 同時に受けられるPATCHのため、単数`cluster_id`では対象を一意特定できない
 * ——複数クラスタを配列で返す。暗黙登録オファーは`operation==="rename"`限定。
 */
export interface AffectedCluster {
  cluster_id: string;
  display_name: string;
  operation: "rename" | "merge" | "reassign";
}

export interface SpeakerUpdateResult {
  meeting_id: number;
  updated: Record<string, number>;
  speakers: string[];
  redis_cache_cleared: boolean;
  drive_export_requeued: boolean;
  affected_clusters?: AffectedCluster[];
}

export interface CreateBotRequest {
  platform: Platform;
  native_meeting_id: string;
  passcode?: string;
  meeting_url?: string;
  bot_name?: string;
  language?: string;
  transcribe_enabled?: boolean;
  authenticated?: boolean;
  voice_agent_enabled?: boolean;
  video?: boolean;
  video_receive_enabled?: boolean;
}

export interface BotConfigUpdate {
  language?: string;
  task?: "transcribe" | "translate";
  bot_name?: string;
}

// WebSocket Types
export type WebSocketMessageType =
  | "transcript"
  | "transcript.finalized"
  | "chat.new_message"
  | "meeting.status"
  | "subscribed"
  | "pong"
  | "error"
  // Deprecated inbound compatibility only. Producers emit "transcript".
  | "transcript.mutable";

export interface WebSocketSubscribeMessage {
  action: "subscribe";
  meetings: Array<{
    platform: Platform;
    native_id: string;
  }>;
}

export interface WebSocketPingMessage {
  action: "ping";
}

// Raw segment from WebSocket (different from stored TranscriptSegment)
export interface WebSocketSegment {
  text: string;
  speaker: string | null;
  language?: string;
  session_uid?: string;
  speaker_mapping_status?: string;
  track_id?: string;
  speaker_track_id?: string;
  segment_id?: string;
  completed?: boolean;
  start?: number;
  end_time?: number;
  absolute_start_time: string;
  absolute_end_time: string;
  updated_at?: string;
}

// Deprecated inbound compatibility only. Producers emit WebSocketTranscriptBundleMessage.
export interface WebSocketTranscriptLegacyMutableMessage {
  type: "transcript.mutable";
  meeting: { id: number };
  payload: {
    segments: WebSocketSegment[];
  };
  ts: string;
}

export interface WebSocketTranscriptFinalizedMessage {
  type: "transcript.finalized";
  meeting: { id: number };
  payload?: {
    segment_count?: number;
    triggered_by?: string;
  };
  ts: string;
}

export interface WebSocketTranscriptBundleMessage {
  type: "transcript";
  meeting: { id: number };
  speaker: string;
  confirmed: WebSocketSegment[];
  pending: WebSocketSegment[];
  ts: string;
}

export interface WebSocketStatusMessage {
  type: "meeting.status";
  meeting: { platform: Platform; native_id: string };
  payload: {
    status: MeetingStatus;
  };
  ts: string;
}

export interface WebSocketSubscribedMessage {
  type: "subscribed";
  meetings: number[];  // Array of meeting IDs
}

export interface WebSocketPongMessage {
  type: "pong";
}

export interface WebSocketErrorMessage {
  type: "error";
  error: string;
  details?: string;
}

// Chat message from the meeting chat (read by the bot)
export interface ChatMessage {
  sender: string;
  text: string;
  timestamp: number;    // Unix ms
  is_from_bot: boolean;
}

export interface WebSocketChatMessage {
  type: "chat.new_message";
  meeting: { id: number };
  payload: ChatMessage;
  ts: string;
}

export type WebSocketIncomingMessage =
  | WebSocketTranscriptLegacyMutableMessage
  | WebSocketTranscriptFinalizedMessage
  | WebSocketTranscriptBundleMessage
  | WebSocketStatusMessage
  | WebSocketChatMessage
  | WebSocketSubscribedMessage
  | WebSocketPongMessage
  | WebSocketErrorMessage;

// API Response Types
export interface MeetingsResponse {
  meetings: Meeting[];
}

export interface TranscriptsResponse {
  segments: TranscriptSegment[];
}

// UI Types
export interface SpeakerColor {
  bg: string;
  text: string;
  border: string;
  avatar: string;
}

export const SPEAKER_COLORS: SpeakerColor[] = [
  { bg: "bg-blue-50", text: "text-blue-700", border: "border-blue-200", avatar: "bg-blue-500" },
  { bg: "bg-emerald-50", text: "text-emerald-700", border: "border-emerald-200", avatar: "bg-emerald-500" },
  { bg: "bg-purple-50", text: "text-purple-700", border: "border-purple-200", avatar: "bg-purple-500" },
  { bg: "bg-amber-50", text: "text-amber-700", border: "border-amber-200", avatar: "bg-amber-500" },
  { bg: "bg-rose-50", text: "text-rose-700", border: "border-rose-200", avatar: "bg-rose-500" },
  { bg: "bg-cyan-50", text: "text-cyan-700", border: "border-cyan-200", avatar: "bg-cyan-500" },
  { bg: "bg-indigo-50", text: "text-indigo-700", border: "border-indigo-200", avatar: "bg-indigo-500" },
  { bg: "bg-teal-50", text: "text-teal-700", border: "border-teal-200", avatar: "bg-teal-500" },
];

export function getSpeakerColor(speaker: string, speakerList: string[]): SpeakerColor {
  const index = speakerList.indexOf(speaker);
  if (index === -1) {
    return SPEAKER_COLORS[0];
  }
  return SPEAKER_COLORS[index % SPEAKER_COLORS.length];
}

// Platform display helpers
export const PLATFORM_CONFIG = {
  google_meet: {
    name: "Google Meet",
    color: "bg-green-500",
    textColor: "text-green-700",
    bgColor: "bg-green-50",
    icon: "video",
    pattern: /^[a-z]{3}-[a-z]{4}-[a-z]{3}$/,
    placeholder: "abc-defg-hij",
  },
  teams: {
    name: "Microsoft Teams",
    color: "bg-blue-600",
    textColor: "text-blue-700",
    bgColor: "bg-blue-50",
    icon: "users",
    pattern: /^\d+$/,
    placeholder: "123456789",
  },
  zoom: {
    name: "Zoom",
    color: "bg-blue-500",
    textColor: "text-blue-600",
    bgColor: "bg-blue-50",
    icon: "video",
    pattern: /^\d{9,11}$/,
    placeholder: "85173157171",
  },
  browser_session: {
    name: "ブラウザ",
    color: "bg-gray-500",
    textColor: "text-gray-700",
    bgColor: "bg-gray-50",
    icon: "monitor",
    pattern: /^bs-/,
    placeholder: "",
  },
} as const;

export const MEETING_STATUS_CONFIG: Record<MeetingStatus, { label: string; color: string; bgColor: string }> = {
  requested: { label: "受付済み", color: "text-blue-600 dark:text-blue-400", bgColor: "bg-blue-100 dark:bg-blue-950/50" },
  joining: { label: "参加中", color: "text-blue-600 dark:text-blue-400", bgColor: "bg-blue-100 dark:bg-blue-950/50" },
  awaiting_admission: { label: "入室待ち", color: "text-amber-600 dark:text-amber-400", bgColor: "bg-amber-100 dark:bg-amber-950/50" },
  active: { label: "記録中", color: "text-green-600 dark:text-green-400", bgColor: "bg-green-100 dark:bg-green-950/50" },
  needs_human_help: { label: "要対応", color: "text-orange-600 dark:text-orange-400", bgColor: "bg-orange-100 dark:bg-orange-950/50" },
  stopping: { label: "停止中", color: "text-slate-600 dark:text-slate-400", bgColor: "bg-slate-100 dark:bg-slate-900/50" },
  completed: { label: "完了", color: "text-green-600 dark:text-green-400", bgColor: "bg-green-100 dark:bg-green-950/50" },
  failed: { label: "失敗", color: "text-red-600 dark:text-red-400", bgColor: "bg-red-100 dark:bg-red-950/50" },
};

// Get detailed status info based on meeting data
export interface DetailedStatusInfo {
  label: string;
  color: string;
  bgColor: string;
  description?: string;
}

export function getDetailedStatus(status: MeetingStatus, data?: MeetingData): DetailedStatusInfo {
  const baseConfig = MEETING_STATUS_CONFIG[status];

  // Fallback config in case status is invalid or config is missing
  const fallbackConfig: DetailedStatusInfo = {
    label: "不明",
    color: "text-gray-600 dark:text-gray-400",
    bgColor: "bg-gray-100 dark:bg-gray-800/50",
    description: "状態を確認できません"
  };

  // The status communicates processing state, not how the meeting ended.
  // Keep completion_reason in the data for audit/history, but render every
  // successfully finalized meeting consistently as completed.
  if (status === "completed") {
    return {
      ...(baseConfig || fallbackConfig),
      description: "文字起こしが完了しました",
    };
  }

  // For failed meetings, add description based on error
  if (status === "failed") {
    let description = "文字起こしに失敗しました";
    if (data?.error_code) {
      switch (data.error_code.toLowerCase()) {
        case "admission_timeout":
        case "not_admitted":
          description = "ボットの入室が許可されませんでした";
          break;
        case "meeting_ended":
          description = "ボット参加前に会議が終了しました";
          break;
        case "connection_failed":
          description = "会議への接続に失敗しました";
          break;
      }
    }
    return {
      label: "失敗",
      color: "text-red-600 dark:text-red-400",
      bgColor: "bg-red-100 dark:bg-red-950/50",
      description
    };
  }

  // For escalated meetings
  if (status === "needs_human_help") {
    return {
      label: "要対応",
      color: "text-orange-600 dark:text-orange-400",
      bgColor: "bg-orange-100 dark:bg-orange-950/50",
      description: data?.escalation_reason as string || "ボットが止まっているため、人の確認が必要です"
    };
  }

  // For active meetings
  if (status === "active") {
    return {
      label: "記録中",
      color: "text-green-600 dark:text-green-400",
      bgColor: "bg-green-100 dark:bg-green-950/50",
      description: "記録中です"
    };
  }

  // For joining states
  if (status === "joining") {
    return {
      label: "参加中",
      color: "text-blue-600 dark:text-blue-400",
      bgColor: "bg-blue-100 dark:bg-blue-950/50",
      description: "会議に接続しています"
    };
  }

  if (status === "awaiting_admission") {
    return {
      label: "入室待ち",
      color: "text-amber-600 dark:text-amber-400",
      bgColor: "bg-amber-100 dark:bg-amber-950/50",
      description: "ロビーで入室許可を待っています"
    };
  }

  if (status === "requested") {
    return {
      label: "受付済み",
      color: "text-blue-600 dark:text-blue-400",
      bgColor: "bg-blue-100 dark:bg-blue-950/50",
      description: "ボットを開始しています"
    };
  }

  // Return baseConfig if it exists, otherwise fallback
  return baseConfig || fallbackConfig;
}

// Languages supported by Whisper
export const SUPPORTED_LANGUAGES = [
  { code: "auto", name: "自動判定" },
  { code: "en", name: "英語" },
  { code: "fr", name: "フランス語" },
  { code: "de", name: "ドイツ語" },
  { code: "es", name: "スペイン語" },
  { code: "it", name: "イタリア語" },
  { code: "pt", name: "ポルトガル語" },
  { code: "nl", name: "オランダ語" },
  { code: "pl", name: "ポーランド語" },
  { code: "ru", name: "ロシア語" },
  { code: "zh", name: "中国語" },
  { code: "ja", name: "日本語" },
  { code: "ko", name: "韓国語" },
  { code: "ar", name: "アラビア語" },
  { code: "hi", name: "ヒンディー語" },
  { code: "tr", name: "トルコ語" },
  { code: "vi", name: "ベトナム語" },
  { code: "th", name: "タイ語" },
  { code: "sv", name: "スウェーデン語" },
  { code: "da", name: "デンマーク語" },
  { code: "fi", name: "フィンランド語" },
  { code: "no", name: "ノルウェー語" },
] as const;

// ==========================================
// Recording Types (from meeting.data.recordings)
// ==========================================

export type RecordingStatus = "in_progress" | "uploading" | "completed" | "failed";
export type RecordingSource = "bot" | "upload" | "url";
export type MediaFileType = "audio" | "video" | "screenshot";

export interface RecordingMediaFile {
  id: number;
  type: MediaFileType;
  format: string; // wav, webm, opus, mp3, etc.
  storage_path: string;
  storage_backend: "minio" | "s3" | "local";
  file_size_bytes: number | null;
  duration_seconds: number | null;
  finalized_by?: string | null; // "recording_finalizer.master" when master
  is_final?: boolean;
  metadata?: Record<string, unknown>;
  created_at: string;
}

// v0.10.6.1 - canonical playback routes. Producer writes; consumer reads.
// Null sub-field means no master for that type yet.
export interface RecordingPlaybackUrl {
  audio: string | null; // stable route, e.g. /recordings/<id>/master?type=audio
  video: string | null;
}

export interface RecordingData {
  id: number;
  meeting_id: number;
  user_id: number;
  session_uid: string;
  source: RecordingSource;
  status: RecordingStatus;
  created_at: string;
  completed_at: string | null;
  media_files: RecordingMediaFile[]; // deprecated in v0.10.6.1; removed in v0.10.7
  playback_url?: RecordingPlaybackUrl | null; // v0.10.6.1 canonical
}

// ==========================================
// Admin API Types
// ==========================================

export interface VexaUser {
  id: string;
  email: string;
  name: string;
  image_url?: string;
  max_concurrent_bots: number;
  data?: Record<string, unknown>;
  created_at: string;
}

export interface VexaUserWithTokens extends VexaUser {
  api_tokens: APIToken[];
}

export interface APIToken {
  id: string;
  token: string; // Only visible once at creation
  user_id: string;
  created_at: string;
}

export interface CreateUserRequest {
  email: string;
  name?: string;
  max_concurrent_bots?: number;
}

export interface UpdateUserRequest {
  name?: string;
  max_concurrent_bots?: number;
  image_url?: string;
  data?: Record<string, unknown>;
}

export interface UsersListResponse {
  users: VexaUser[];
  total: number;
  skip: number;
  limit: number;
}

export interface CreateTokenResponse {
  id: string;
  token: string; // Save immediately - cannot be retrieved later!
  user_id: string;
  created_at: string;
}
