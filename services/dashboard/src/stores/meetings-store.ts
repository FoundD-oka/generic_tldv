import { create } from "zustand";
import type { Meeting, TranscriptSegment, Platform, MeetingStatus, RecordingData, ChatMessage } from "@/types/vexa";
import { VexaAPIError, vexaAPI } from "@/lib/api";
import {
  type TranscriptManager,
  createTranscriptManager,
} from "@vexaai/transcript-rendering";

interface MeetingDataUpdate {
  name?: string;
  notes?: string;
  participants?: string[];
  languages?: string[];
}

function isHiddenDeletedMeeting(meeting: Meeting): boolean {
  const redacted = meeting.data?.redacted === true;
  // Backend delete/anonymize flow clears native meeting id.
  const missingNativeId = !meeting.platform_specific_id;
  return redacted || missingNativeId;
}

function recordingsFromMeeting(meeting: Meeting | null): RecordingData[] {
  const recordings = meeting?.data?.recordings;
  return Array.isArray(recordings) ? (recordings as RecordingData[]) : [];
}

function isTransientRefreshError(error: unknown): boolean {
  if (error instanceof VexaAPIError) {
    return (
      [502, 503, 504].includes(error.status) ||
      /server disconnected|request timeout|failed to connect/i.test(error.message)
    );
  }

  return (
    error instanceof TypeError &&
    /failed to fetch|load failed|networkerror/i.test(error.message)
  );
}

export function recordingsStateSignature(meeting: Meeting | null): string {
  return recordingsFromMeeting(meeting)
    .map((recording) => {
      const mediaFiles = Array.isArray(recording.media_files)
        ? recording.media_files
            .map((media) => [
              media.id,
              media.type,
              media.format,
              media.is_final === true ? "final" : "partial",
              media.finalized_by ?? "",
              media.storage_path ?? "",
              media.file_size_bytes ?? "",
              media.duration_seconds ?? "",
            ].join(":"))
            .join(",")
        : "";
      return [
        recording.id,
        recording.status,
        recording.completed_at ?? "",
        recording.playback_url?.audio ?? "",
        recording.playback_url?.video ?? "",
        mediaFiles,
      ].join(";");
    })
    .join("|");
}

interface MeetingsState {
  // Data
  meetings: Meeting[];
  currentMeeting: Meeting | null;
  transcripts: TranscriptSegment[];
  recordings: RecordingData[];
  chatMessages: ChatMessage[];

  // Internal state for best-known-transcript model
  _manager: TranscriptManager<TranscriptSegment>;

  // Pagination
  hasMore: boolean;
  isLoadingMore: boolean;

  // Loading states
  isLoadingMeetings: boolean;
  isLoadingMeeting: boolean;
  isLoadingTranscripts: boolean;
  isUpdatingMeeting: boolean;

  // Error states
  error: string | null;
  subscriptionRequired: boolean;

  // Filters (server-side)
  _filters: { search?: string; status?: string; platform?: string };

  // Pagination cursor (#304): explicit offset that advances by the
  // unfiltered API page size — NOT by `meetings.length` which is the
  // post-filter array. Mixing the two caused duplicate rows whenever a
  // page contained `data.redacted=true` shells (filtered client-side):
  // the next request asked for an offset N positions before where the
  // previous page actually ended → API returned N rows already shown.
  _offset: number;

  // Actions
  fetchMeetings: (
    filters?: { search?: string; status?: string; platform?: string },
    options?: { silent?: boolean }
  ) => Promise<void>;
  fetchMoreMeetings: () => Promise<void>;
  fetchMeeting: (id: string, options?: { silent?: boolean }) => Promise<void>;
  refreshMeeting: (id: string) => Promise<void>;
  fetchTranscripts: (platform: Platform, nativeId: string, meetingId?: string, options?: { silent?: boolean }) => Promise<void>;
  updateMeetingData: (platform: Platform, nativeId: string, data: MeetingDataUpdate) => Promise<void>;
  deleteMeeting: (platform: Platform, nativeId: string, meetingId?: string) => Promise<void>;
  setCurrentMeeting: (meeting: Meeting | null) => void;
  clearCurrentMeeting: () => void;

  // Real-time updates
  bootstrapTranscripts: (segments: TranscriptSegment[]) => void;
  upsertTranscriptSegments: (confirmed: TranscriptSegment[], pending?: TranscriptSegment[], speaker?: string) => void;
  addTranscriptSegment: (segment: TranscriptSegment) => void;
  updateTranscriptSegment: (segment: TranscriptSegment) => void;
  updateMeetingStatus: (meetingId: string, status: MeetingStatus) => void;

  // Chat
  fetchChatMessages: (platform: Platform, nativeId: string) => Promise<void>;
  addChatMessage: (message: ChatMessage) => void;

  // Utilities
  clearError: () => void;
}

let isChatRouteUnavailable = false;
let hasLoggedChatRouteUnavailable = false;
let meetingsRequestGeneration = 0;
let isSilentMeetingRefreshInFlight = false;
let meetingDetailRequestGeneration = 0;

export const useMeetingsStore = create<MeetingsState>((set, get) => ({
  // Initial state
  meetings: [],
  currentMeeting: null,
  transcripts: [],
  recordings: [],
  chatMessages: [],
  _manager: createTranscriptManager(),
  hasMore: false,
  isLoadingMore: false,
  _filters: {},
  _offset: 0,
  isLoadingMeetings: false,
  isLoadingMeeting: false,
  isLoadingTranscripts: false,
  isUpdatingMeeting: false,
  error: null,
  subscriptionRequired: false,

  // Fetch first page of meetings (with optional server-side filters)
  fetchMeetings: async (
    filters?: { search?: string; status?: string; platform?: string },
    options?: { silent?: boolean }
  ) => {
    const activeFilters = filters ?? get()._filters;
    const silent = options?.silent === true;
    if (silent && isSilentMeetingRefreshInFlight) return;
    if (silent) isSilentMeetingRefreshInFlight = true;
    const requestGeneration = ++meetingsRequestGeneration;
    if (silent) {
      set({ _filters: activeFilters });
    } else {
      set({ isLoadingMeetings: true, error: null, _filters: activeFilters, _offset: 0 });
    }
    try {
      const PAGE = 50;
      const pageLimit = silent ? Math.max(PAGE, get()._offset) : PAGE;
      const result = await vexaAPI.getMeetings({ limit: pageLimit, offset: 0, ...activeFilters });
      if (requestGeneration !== meetingsRequestGeneration) return;
      const meetings = result.meetings.filter((m) => !isHiddenDeletedMeeting(m));
      meetings.sort((a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      );
      // #304: advance _offset by the UNFILTERED page size (PAGE), NOT by
      // meetings.length. The API returns up to PAGE rows; client-side
      // filter may drop some (redacted shells); next request must ask
      // for offset PAGE regardless of how many survived the filter.
      set({
        meetings,
        hasMore: result.has_more,
        subscriptionRequired: false,
        _offset: pageLimit,
        ...(silent ? {} : { isLoadingMeetings: false }),
      });
    } catch (error) {
      if (error instanceof VexaAPIError && error.status === 402) {
        if (requestGeneration !== meetingsRequestGeneration) return;
        set({
          subscriptionRequired: true,
          error: null,
          ...(silent ? {} : { isLoadingMeetings: false }),
        });
        return;
      }
      if (silent) {
        console.error("Failed to silently refresh meetings:", error);
        return;
      }
      set({
        error: (error as Error).message,
        isLoadingMeetings: false
      });
    } finally {
      if (silent) isSilentMeetingRefreshInFlight = false;
    }
  },

  // Fetch next page and append
  fetchMoreMeetings: async () => {
    const { meetings, hasMore, isLoadingMore, _filters, _offset } = get();
    if (!hasMore || isLoadingMore) return;
    const requestGeneration = ++meetingsRequestGeneration;
    set({ isLoadingMore: true });
    try {
      const PAGE = 50;
      // #304: use the explicit _offset cursor, NOT meetings.length.
      const result = await vexaAPI.getMeetings({ limit: PAGE, offset: _offset, ..._filters });
      if (requestGeneration !== meetingsRequestGeneration) {
        set({ isLoadingMore: false });
        return;
      }
      const newMeetings = result.meetings.filter((m) => !isHiddenDeletedMeeting(m));
      // #304 belt-and-suspenders: dedupe by meeting.id. Defends against
      // any future filter / WebSocket-update race where the same meeting
      // could land in `meetings` twice.
      const seen = new Set(meetings.map((m) => m.id));
      const deduped = newMeetings.filter((m) => !seen.has(m.id));
      const merged = [...meetings, ...deduped];
      merged.sort((a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      );
      set({
        meetings: merged,
        hasMore: result.has_more,
        isLoadingMore: false,
        _offset: _offset + PAGE,
      });
    } catch (error) {
      set({ isLoadingMore: false });
      console.error("Failed to load more meetings:", error);
    }
  },

  // Fetch single meeting detail directly. List rows can be intentionally thin,
  // while detail rows carry canonical recording/transcription lifecycle data.
  // Use silent: true to avoid showing loading state (for polling/refresh)
  fetchMeeting: async (id: string, options?: { silent?: boolean }) => {
    const { silent = false } = options || {};
    const requestGeneration = ++meetingDetailRequestGeneration;

    if (!silent) {
      set({ isLoadingMeeting: true, error: null });
    }

    try {
      let meeting: Meeting;
      try {
        meeting = await vexaAPI.getMeeting(id);
        if (requestGeneration !== meetingDetailRequestGeneration) {
          if (!silent) set({ isLoadingMeeting: false });
          return;
        }
      } catch (e) {
        if (e instanceof VexaAPIError && e.status === 404) {
          set({ error: `Meeting with ID ${id} not found`, isLoadingMeeting: false });
          return;
        }
        throw e;
      }

      const { meetings } = get();
      const updatedMeetings = meetings.map((m) =>
        m.id.toString() === id ? meeting : m
      );
      set({
        currentMeeting: meeting,
        meetings: updatedMeetings,
        recordings: recordingsFromMeeting(meeting),
        isLoadingMeeting: false,
      });
    } catch (error) {
      if (error instanceof VexaAPIError && error.status === 402) {
        set({ subscriptionRequired: true, isLoadingMeeting: false, error: null });
        return;
      }
      set({
        error: (error as Error).message,
        isLoadingMeeting: false
      });
    }
  },

  // Silently refresh meeting data (for polling without UI flicker)
  refreshMeeting: async (id: string) => {
    const requestGeneration = ++meetingDetailRequestGeneration;
    try {
      const meeting = await vexaAPI.getMeeting(id);
      if (requestGeneration !== meetingDetailRequestGeneration) return;
      if (meeting) {
        const { currentMeeting, meetings } = get();
        const currentRecordingSignature = recordingsStateSignature(currentMeeting);
        const nextRecordingSignature = recordingsStateSignature(meeting);
        if (currentMeeting?.status !== meeting.status ||
            currentMeeting?.updated_at !== meeting.updated_at ||
            currentRecordingSignature !== nextRecordingSignature) {
          // Update in meetings list if present
          const updatedMeetings = meetings.map((m) =>
            m.id.toString() === id ? meeting : m
          );
          set({
            meetings: updatedMeetings,
            currentMeeting: meeting,
            recordings: recordingsFromMeeting(meeting),
          });
        }
      }
    } catch (error) {
      // Silent refresh - don't show errors for polling failures
      if (!isTransientRefreshError(error)) {
        console.debug("Failed to refresh meeting:", error);
      }
    }
  },

  // Fetch transcripts for a meeting
  fetchTranscripts: async (platform: Platform, nativeId: string, meetingId?: string, options?: { silent?: boolean }) => {
    const { silent = false } = options || {};
    if (!silent) {
      set({ isLoadingTranscripts: true, error: null });
    }
    try {
      const result = await vexaAPI.getMeetingWithTranscripts(platform, nativeId, meetingId);
      // Reuse the same canonical pipeline as WS/bootstraps:
      // - filter invalid
      // - sort by absolute_start_time
      // - collapse overlap (containment / expansion / tail-repeat)
      get().bootstrapTranscripts(result.segments);
      // Store the authoritative recording list, including empty responses so
      // navigating between meetings cannot leave stale playback controls behind.
      set({ recordings: result.recordings });
      if (!silent) {
        set({ isLoadingTranscripts: false });
      }
    } catch (error) {
      if (error instanceof VexaAPIError && error.status === 402) {
        set({ subscriptionRequired: true, ...(silent ? {} : { isLoadingTranscripts: false, error: null }) });
        return;
      }
      if (!silent) {
        set({
          error: (error as Error).message,
          isLoadingTranscripts: false
        });
      } else {
        console.error("Failed to silently refresh transcripts:", error);
      }
    }
  },

  // Update meeting data (title, notes, etc.)
  updateMeetingData: async (platform: Platform, nativeId: string, data: MeetingDataUpdate) => {
    set({ isUpdatingMeeting: true });
    try {
      const updatedMeeting = await vexaAPI.updateMeetingData(platform, nativeId, data);

      // Update current meeting if it matches
      const { currentMeeting, meetings } = get();
      if (currentMeeting?.platform_specific_id === nativeId) {
        set({
          currentMeeting: updatedMeeting,
          recordings: recordingsFromMeeting(updatedMeeting),
        });
      }

      // Update in meetings list
      const updatedMeetings = meetings.map((m) =>
        m.platform_specific_id === nativeId ? updatedMeeting : m
      );
      set({ meetings: updatedMeetings, isUpdatingMeeting: false });
    } catch (error) {
      set({ isUpdatingMeeting: false });
      throw error; // Re-throw so UI can handle it
    }
  },

  deleteMeeting: async (platform: Platform, nativeId: string, meetingId?: string) => {
    await vexaAPI.deleteMeeting(platform, nativeId, meetingId);

    const targetId = meetingId ? String(meetingId) : null;
    const { meetings, currentMeeting } = get();

    const updatedMeetings = meetings.filter((m) => {
      if (targetId) return String(m.id) !== targetId;
      return !(m.platform === platform && m.platform_specific_id === nativeId);
    });

    const shouldClearCurrent =
      currentMeeting &&
      (targetId
        ? String(currentMeeting.id) === targetId
        : currentMeeting.platform === platform && currentMeeting.platform_specific_id === nativeId);

    set({
      meetings: updatedMeetings,
      ...(shouldClearCurrent ? { currentMeeting: null, transcripts: [], recordings: [], chatMessages: [], _manager: createTranscriptManager() } : {}),
    });
  },

  setCurrentMeeting: (meeting: Meeting | null) => {
    set({ currentMeeting: meeting, recordings: recordingsFromMeeting(meeting) });
  },

  clearCurrentMeeting: () => {
    set({
      currentMeeting: null, transcripts: [], recordings: [], chatMessages: [],
      _manager: createTranscriptManager(),
    });
  },

  // Bootstrap from REST API: confirmed segments only, clears pending
  bootstrapTranscripts: (segments: TranscriptSegment[]) => {
    const { _manager } = get();
    const transcripts = _manager.bootstrap(segments);

    const firstTime = transcripts.length > 0 ? transcripts[0].absolute_start_time : null;
    const { currentMeeting } = get();
    const updatedMeeting = firstTime && currentMeeting && !currentMeeting.start_time
      ? { ...currentMeeting, start_time: firstTime }
      : currentMeeting;

    set({
      transcripts,
      ...(updatedMeeting !== currentMeeting ? { currentMeeting: updatedMeeting } : {}),
    });
  },

  // WS update: append confirmed (by segment_id), replace pending for speaker
  upsertTranscriptSegments: (confirmedSegs: TranscriptSegment[], pendingSegs?: TranscriptSegment[], speaker?: string) => {
    const { _manager } = get();
    const transcripts = _manager.handleMessage({
      type: 'transcript',
      speaker: speaker ?? undefined,
      confirmed: confirmedSegs,
      pending: pendingSegs,
    });

    if (!transcripts) return;

    const firstTime = transcripts.length > 0 ? transcripts[0].absolute_start_time : null;
    const { currentMeeting } = get();
    const updatedMeeting = firstTime && currentMeeting && !currentMeeting.start_time
      ? { ...currentMeeting, start_time: firstTime }
      : currentMeeting;

    set({
      transcripts,
      ...(updatedMeeting !== currentMeeting ? { currentMeeting: updatedMeeting } : {}),
    });
  },

  // Real-time: Add new transcript segment (legacy — treats as confirmed)
  addTranscriptSegment: (segment: TranscriptSegment) => {
    get().upsertTranscriptSegments([segment]);
  },

  // Real-time: Update existing transcript segment
  updateTranscriptSegment: (segment: TranscriptSegment) => {
    get().upsertTranscriptSegments([segment]);
  },

  // Update meeting status from WebSocket
  updateMeetingStatus: (meetingId: string, status: MeetingStatus) => {
    const { meetings, currentMeeting } = get();
    const targetId = String(meetingId);

    // Update in meetings list
    const updatedMeetings = meetings.map((m) =>
      String(m.id) === targetId ? { ...m, status } : m
    );
    set({ meetings: updatedMeetings });

    // Update current meeting if it matches
    if (currentMeeting && String(currentMeeting.id) === targetId) {
      set({ currentMeeting: { ...currentMeeting, status } });
    }
  },

  // Fetch chat messages via REST API (bootstrap)
  fetchChatMessages: async (platform: Platform, nativeId: string) => {
    if (isChatRouteUnavailable) {
      return;
    }

    try {
      const result = await vexaAPI.getChatMessages(platform, nativeId);
      set({ chatMessages: result.messages });
    } catch (error) {
      if (error instanceof VexaAPIError && error.status === 404) {
        // Backward compatibility: older backends do not expose this endpoint.
        const isMissingRoute = error.message === "Not Found";
        if (isMissingRoute) {
          isChatRouteUnavailable = true;
          if (!hasLoggedChatRouteUnavailable) {
            hasLoggedChatRouteUnavailable = true;
            console.info("[Chat] Chat endpoint is not available on this backend; disabling chat bootstrap fetches.");
          }
        }

        // Non-fatal: chat may not exist for this meeting.
        set({ chatMessages: [] });
        return;
      }

      // Non-fatal — chat may not be available (network/auth/transient failures).
      console.error("[Chat] Failed to fetch chat messages:", error);
    }
  },

  // Add a single chat message from WebSocket (real-time)
  addChatMessage: (message: ChatMessage) => {
    const { chatMessages } = get();
    // Deduplicate by timestamp + sender + text
    const exists = chatMessages.some(
      (m) => m.timestamp === message.timestamp && m.sender === message.sender && m.text === message.text
    );
    if (!exists) {
      set({ chatMessages: [...chatMessages, message] });
    }
  },

  clearError: () => {
    set({ error: null });
  },
}));
