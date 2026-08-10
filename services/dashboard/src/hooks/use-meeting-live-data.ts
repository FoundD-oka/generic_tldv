"use client";
import { useEffect, useMemo, type MutableRefObject } from "react";
import { useLiveTranscripts } from "@/hooks/use-live-transcripts";
import { useMeetingPolling } from "@/hooks/use-meeting-polling";
import { WHISPER_LANGUAGE_CODES } from "@/lib/languages";
import type { Meeting, MeetingStatus, TranscriptSegment } from "@/types/vexa";

type Props = { meetingId: string; currentMeeting: Meeting | null; transcripts: TranscriptSegment[]; forcePostMeetingMode: boolean; hasRecordingAudio: boolean; playbackConnectionError: string | null; hasLoadedRef: MutableRefObject<boolean>; handleStatusChange: (status: MeetingStatus) => void; setForcePostMeetingMode: (value: boolean) => void; setCurrentLanguage: (value: string) => void; fetchMeeting: (id: string) => Promise<void>; refreshMeeting: (id: string) => Promise<Meeting | null>; clearCurrentMeeting: () => void; fetchTranscripts: (platform: Meeting["platform"], nativeId: string, id?: string, options?: { silent?: boolean }) => Promise<void>; fetchChatMessages: (platform: Meeting["platform"], nativeId: string) => Promise<void>; };
export function useMeetingLiveData(props: Props) {
 const { currentMeeting, meetingId, hasLoadedRef } = props;
 const isEarlyState = currentMeeting?.status === "requested" || currentMeeting?.status === "joining" || currentMeeting?.status === "awaiting_admission";
 const isStoppingState = currentMeeting?.status === "stopping";
 const isBrowserSession = currentMeeting?.platform === "browser_session" || currentMeeting?.data?.mode === "browser_session";
 const shouldUseWebSocket = !isBrowserSession && (currentMeeting?.status === "active" || isEarlyState || isStoppingState);
 const ws = useLiveTranscripts({ platform: currentMeeting?.platform ?? "google_meet", nativeId: currentMeeting?.platform_specific_id ?? "", meetingId, isActive: shouldUseWebSocket, onStatusChange: props.handleStatusChange });
 useEffect(() => { if (meetingId) { props.setForcePostMeetingMode(false); void props.fetchMeeting(meetingId); } return () => { props.clearCurrentMeeting(); hasLoadedRef.current = false; }; }, [meetingId, props.fetchMeeting, props.clearCurrentMeeting]);
 useEffect(() => { if (currentMeeting && !hasLoadedRef.current) hasLoadedRef.current = true; }, [currentMeeting, hasLoadedRef]);
 const validLangCodes = useMemo(() => new Set(WHISPER_LANGUAGE_CODES), []);
 useEffect(() => { if (!currentMeeting) return; const fromData = currentMeeting.data?.languages?.[0]; if (fromData && fromData !== "auto") { props.setCurrentLanguage(fromData); return; } const detected = props.transcripts.find((item) => item.language && item.language !== "unknown" && validLangCodes.has(item.language))?.language; props.setCurrentLanguage(detected || "auto"); }, [currentMeeting, props.transcripts, validLangCodes]);
 const platform = currentMeeting?.platform; const nativeId = currentMeeting?.platform_specific_id; const numericId = currentMeeting?.id ? String(currentMeeting.id) : undefined; const status = currentMeeting?.status;
 const postMeeting = props.forcePostMeetingMode || status === "stopping" || status === "completed";
 const pollStatus = status === "requested" || status === "joining" || status === "awaiting_admission" || status === "active" || status === "needs_human_help" || status === "stopping";
 const pollArtifacts = postMeeting && currentMeeting?.data?.recording_enabled !== false && !props.hasRecordingAudio && !props.playbackConnectionError;
 useMeetingPolling({ meetingId, meetingPlatform: platform, meetingNativeId: nativeId, meetingNumericId: numericId, shouldPollMeetingStatus: pollStatus, shouldPollPostMeetingArtifacts: pollArtifacts, refreshMeeting: props.refreshMeeting, fetchTranscripts: props.fetchTranscripts, fetchChatMessages: props.fetchChatMessages });
 useEffect(() => { if (isBrowserSession && status !== "stopping" && status !== "completed") return; if (platform && nativeId) { void props.fetchTranscripts(platform, nativeId, numericId); void props.fetchChatMessages(platform, nativeId); } }, [status, shouldUseWebSocket, isBrowserSession, platform, nativeId, numericId, props.fetchTranscripts, props.fetchChatMessages]);
 useEffect(() => { if (shouldUseWebSocket && platform && nativeId) void props.fetchChatMessages(platform, nativeId); }, [shouldUseWebSocket, platform, nativeId, props.fetchChatMessages]);
 return { ...ws, shouldUseWebSocket, isBrowserSession, meetingPlatform: platform, meetingNativeId: nativeId, meetingNumericId: numericId, meetingStatus: status };
}
