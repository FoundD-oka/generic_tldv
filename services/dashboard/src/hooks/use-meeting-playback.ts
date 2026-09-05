"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { AudioFragment, AudioPlayerHandle } from "@/components/recording/audio-player";
import type { VideoPlayerHandle } from "@/components/recording/video-player";
import { vexaAPI } from "@/lib/api";
import type { RecordingData, RecordingMediaFile, TranscriptSegment } from "@/types/vexa";

const PLAYBACK_DEADLINE_MS = 10_000;
const PLAYBACK_RETRY_DELAYS_MS = [1_500, 3_000, 6_000] as const;
const MASTER_FINALIZER = "recording_finalizer.master";
const PREPARATION_ERROR = "録音の準備を確認できませんでした。再試行してください";

type Channel = "audio" | "video";
type MasterDescriptor = {
  id: number | null;
  storage_path: string | null;
  file_size_bytes: number | null;
  duration_seconds: number | null;
  finalized_by: string | null;
  is_final: boolean | null;
};
type PlaybackDescriptor = {
  id: number;
  status: string | null;
  session_uid: string | null;
  created_at: string | null;
  playback_url: string | null;
  master: MasterDescriptor | null;
};
type ChannelDescriptor = { meetingId: string; recordings: PlaybackDescriptor[] };

function nullable<T>(value: T | null | undefined): T | null {
  return value ?? null;
}

function masterDescriptor(mediaFiles: RecordingMediaFile[], channel: Channel): MasterDescriptor | null {
  const master = mediaFiles.find(
    (media) => media.type === channel && media.finalized_by === MASTER_FINALIZER
  );
  if (!master) return null;
  return {
    id: nullable(master.id),
    storage_path: nullable(master.storage_path),
    file_size_bytes: nullable(master.file_size_bytes),
    duration_seconds: nullable(master.duration_seconds),
    finalized_by: nullable(master.finalized_by),
    is_final: nullable(master.is_final),
  };
}

function channelKey(meetingId: string, recordings: RecordingData[], channel: Channel): string {
  const selected = recordings
    .map((recording, originalIndex) => ({ recording, originalIndex }))
    .filter(({ recording }) =>
      String(recording.meeting_id) === meetingId &&
      (recording.status === "completed" || recording.status === "in_progress") &&
      Boolean(recording.playback_url?.[channel])
    );
  if (channel === "audio") {
    selected.sort((left, right) => {
      const compared = left.recording.created_at.localeCompare(right.recording.created_at);
      return compared || left.originalIndex - right.originalIndex;
    });
  }
  const descriptors: PlaybackDescriptor[] = selected.map(({ recording }) => ({
    id: recording.id,
    status: nullable(recording.status),
    session_uid: nullable(recording.session_uid),
    created_at: nullable(recording.created_at),
    playback_url: nullable(recording.playback_url?.[channel]),
    master: masterDescriptor(recording.media_files ?? [], channel),
  }));
  return JSON.stringify({ meetingId, recordings: descriptors } satisfies ChannelDescriptor);
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function isRetryableResolutionError(error: unknown): boolean {
  if (error instanceof DOMException && error.name === "AbortError") return true;
  if (error instanceof TypeError) return true;
  if (typeof error === "object" && error !== null && "status" in error) {
    return [502, 503, 504].includes(Number((error as { status?: unknown }).status));
  }
  return false;
}

export type MeetingPlayback = {
  audioPlayerRef: React.RefObject<AudioPlayerHandle | null>;
  videoPlayerRef: React.RefObject<VideoPlayerHandle | null>;
  recordingFragments: AudioFragment[];
  videoSrc: string | null;
  audioResolutionError: string | null;
  videoResolutionError: string | null;
  playbackConnectionError: string | null;
  playbackTime: number | null;
  playbackAbsoluteTime: string | null;
  isPlaybackActive: boolean;
  hasRecordingAudio: boolean;
  recordingDownloadTarget: { recordingId: number; webmUrl: string } | null;
  retryPlayback: () => void;
  handlePlaybackTimeUpdate: (time: number) => void;
  handleFragmentChange: (index: number) => void;
  handleSegmentClick: (startTimeSeconds: number, absoluteStartTime?: string) => void;
};

export function useMeetingPlayback(
  meetingId: string,
  recordings: RecordingData[],
  transcripts: TranscriptSegment[]
): MeetingPlayback {
  const audioPlayerRef = useRef<AudioPlayerHandle>(null);
  const videoPlayerRef = useRef<VideoPlayerHandle>(null);
  const [playbackTime, setPlaybackTime] = useState<number | null>(null);
  const [isPlaybackActive, setIsPlaybackActive] = useState(false);
  const [pendingSeekTime, setPendingSeekTime] = useState<number | null>(null);
  const [, setActiveFragmentIndex] = useState(0);
  const [recordingFragments, setRecordingFragments] = useState<AudioFragment[]>([]);
  const [videoSrc, setVideoSrc] = useState<string | null>(null);
  const [audioResolutionError, setAudioResolutionError] = useState<string | null>(null);
  const [videoResolutionError, setVideoResolutionError] = useState<string | null>(null);
  const [recordingDownloadTarget, setRecordingDownloadTarget] = useState<{ recordingId: number; webmUrl: string } | null>(null);
  const [retryGeneration, setRetryGeneration] = useState(0);
  const audioKey = useMemo(() => channelKey(meetingId, recordings, "audio"), [meetingId, recordings]);
  const videoKey = useMemo(() => channelKey(meetingId, recordings, "video"), [meetingId, recordings]);

  useEffect(() => {
    // A meeting owns every playback cursor and resolved artifact.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setRecordingFragments([]);
    setRecordingDownloadTarget(null);
    setVideoSrc(null);
    setAudioResolutionError(null);
    setVideoResolutionError(null);
    setPendingSeekTime(null);
    setPlaybackTime(null);
    setIsPlaybackActive(false);
    setActiveFragmentIndex(0);
  }, [meetingId]);

  useEffect(() => {
    const descriptor = JSON.parse(audioKey) as ChannelDescriptor;
    let owned = true;
    let controller: AbortController | null = null;
    let deadlineTimer: ReturnType<typeof setTimeout> | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    // Descriptor changes invalidate only this channel.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setRecordingFragments([]);
    setRecordingDownloadTarget(null);
    setAudioResolutionError(null);

    const clearAttempt = () => {
      if (deadlineTimer) clearTimeout(deadlineTimer);
      deadlineTimer = null;
      controller = null;
    };
    const run = async (attempt: number): Promise<void> => {
      controller = new AbortController();
      const signal = controller.signal;
      deadlineTimer = setTimeout(() => controller?.abort(), PLAYBACK_DEADLINE_MS);
      try {
        const results = await Promise.all(descriptor.recordings.map(async (recording) => {
          const result = await vexaAPI.getRecordingMasterStreamUrl(recording.id, "audio", signal);
          return result ? {
            recordingId: recording.id,
            fragment: {
              src: result.url,
              duration: result.duration_seconds ?? 0,
              sessionUid: recording.session_uid ?? "",
              createdAt: recording.created_at ?? "",
            } as AudioFragment,
          } : null;
        }));
        clearAttempt();
        if (!owned) return;
        const resolved = results.filter((entry): entry is { recordingId: number; fragment: AudioFragment } => entry !== null);
        if (descriptor.recordings.length > 0 && resolved.length === 0) {
          if (attempt < PLAYBACK_RETRY_DELAYS_MS.length) {
            retryTimer = setTimeout(() => void run(attempt + 1), PLAYBACK_RETRY_DELAYS_MS[attempt]);
          } else {
            setAudioResolutionError(PREPARATION_ERROR);
          }
          return;
        }
        setRecordingFragments(resolved.map((entry) => entry.fragment));
        setRecordingDownloadTarget(resolved[0] ? { recordingId: resolved[0].recordingId, webmUrl: resolved[0].fragment.src } : null);
        setAudioResolutionError(null);
      } catch (error) {
        clearAttempt();
        if (!owned) return;
        if (isRetryableResolutionError(error) && attempt < PLAYBACK_RETRY_DELAYS_MS.length) {
          retryTimer = setTimeout(() => void run(attempt + 1), PLAYBACK_RETRY_DELAYS_MS[attempt]);
          return;
        }
        setRecordingFragments([]);
        setRecordingDownloadTarget(null);
        setAudioResolutionError(errorMessage(error));
      }
    };
    if (descriptor.recordings.length > 0) void run(0);
    return () => {
      owned = false;
      if (deadlineTimer) clearTimeout(deadlineTimer);
      if (retryTimer) clearTimeout(retryTimer);
      controller?.abort();
    };
  }, [audioKey, retryGeneration]);

  useEffect(() => {
    const descriptor = JSON.parse(videoKey) as ChannelDescriptor;
    let owned = true;
    let controller: AbortController | null = null;
    let deadlineTimer: ReturnType<typeof setTimeout> | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setVideoSrc(null);
    setVideoResolutionError(null);

    const clearAttempt = () => {
      if (deadlineTimer) clearTimeout(deadlineTimer);
      deadlineTimer = null;
      controller = null;
    };
    const run = async (attempt: number): Promise<void> => {
      controller = new AbortController();
      const signal = controller.signal;
      deadlineTimer = setTimeout(() => controller?.abort(), PLAYBACK_DEADLINE_MS);
      try {
        let resolvedUrl: string | null = null;
        for (const recording of descriptor.recordings) {
          const result = await vexaAPI.getRecordingMasterStreamUrl(recording.id, "video", signal);
          if (result) { resolvedUrl = result.url; break; }
        }
        clearAttempt();
        if (!owned) return;
        if (descriptor.recordings.length > 0 && !resolvedUrl) {
          if (attempt < PLAYBACK_RETRY_DELAYS_MS.length) {
            retryTimer = setTimeout(() => void run(attempt + 1), PLAYBACK_RETRY_DELAYS_MS[attempt]);
          } else {
            setVideoResolutionError(PREPARATION_ERROR);
          }
          return;
        }
        setVideoSrc(resolvedUrl);
        setVideoResolutionError(null);
      } catch (error) {
        clearAttempt();
        if (!owned) return;
        if (isRetryableResolutionError(error) && attempt < PLAYBACK_RETRY_DELAYS_MS.length) {
          retryTimer = setTimeout(() => void run(attempt + 1), PLAYBACK_RETRY_DELAYS_MS[attempt]);
          return;
        }
        setVideoSrc(null);
        setVideoResolutionError(errorMessage(error));
      }
    };
    if (descriptor.recordings.length > 0) void run(0);
    return () => {
      owned = false;
      if (deadlineTimer) clearTimeout(deadlineTimer);
      if (retryTimer) clearTimeout(retryTimer);
      controller?.abort();
    };
  }, [videoKey, retryGeneration]);

  const retryPlayback = useCallback(() => {
    setAudioResolutionError(null);
    setVideoResolutionError(null);
    setRetryGeneration((generation) => generation + 1);
  }, []);

  const sessionStarts = useMemo(() => {
    const map = new Map<string, number>();
    for (const segment of transcripts) {
      if (!segment.absolute_start_time || segment.start_time == null) continue;
      const uid = segment.session_uid || "";
      if (!map.has(uid)) map.set(uid, new Date(segment.absolute_start_time).getTime() - segment.start_time * 1000);
    }
    return map;
  }, [transcripts]);
  const hasRecordingAudio = recordingFragments.length > 0;
  const handlePlaybackTimeUpdate = useCallback((time: number) => { setPlaybackTime(time); setIsPlaybackActive(true); }, []);
  const handleFragmentChange = useCallback((index: number) => setActiveFragmentIndex(index), []);
  const handleSegmentClick = useCallback((startTimeSeconds: number, absoluteStartTime?: string) => {
    if (!hasRecordingAudio) {
      setPendingSeekTime(startTimeSeconds);
      return;
    }
    if (recordingFragments.length <= 1) {
      audioPlayerRef.current?.seekTo(startTimeSeconds);
      videoPlayerRef.current?.seekTo(startTimeSeconds);
      setPlaybackTime(startTimeSeconds);
      setIsPlaybackActive(true);
      return;
    }
    let targetFragmentIndex = 0;
    if (absoluteStartTime) {
      const segTimeMs = new Date(absoluteStartTime).getTime();
      const matchingSegment = transcripts.find((segment) => segment.absolute_start_time === absoluteStartTime);
      if (matchingSegment?.session_uid) {
        const uidIndex = recordingFragments.findIndex((fragment) => fragment.sessionUid === matchingSegment.session_uid);
        if (uidIndex >= 0) targetFragmentIndex = uidIndex;
      } else {
        for (let index = recordingFragments.length - 1; index >= 0; index -= 1) {
          const sessionStart = sessionStarts.get(recordingFragments[index].sessionUid);
          if (sessionStart != null && sessionStart <= segTimeMs) { targetFragmentIndex = index; break; }
        }
      }
    }
    audioPlayerRef.current?.seekToFragment(targetFragmentIndex, startTimeSeconds);
    const virtualOffset = recordingFragments.slice(0, targetFragmentIndex).reduce((sum, fragment) => sum + (fragment.duration || 0), 0);
    videoPlayerRef.current?.seekTo(virtualOffset + startTimeSeconds);
    setPlaybackTime(virtualOffset + startTimeSeconds);
    setIsPlaybackActive(true);
  }, [hasRecordingAudio, recordingFragments, transcripts, sessionStarts]);

  useEffect(() => {
    if (!hasRecordingAudio || pendingSeekTime == null) return;
    const timer = setTimeout(() => {
      audioPlayerRef.current?.seekTo(pendingSeekTime);
      videoPlayerRef.current?.seekTo(pendingSeekTime);
      setPlaybackTime(pendingSeekTime);
      setIsPlaybackActive(true);
      setPendingSeekTime(null);
    }, 0);
    return () => clearTimeout(timer);
  }, [hasRecordingAudio, pendingSeekTime]);
  const playbackAbsoluteTime = useMemo(() => {
    if (playbackTime == null || !isPlaybackActive || !recordingFragments.length) return null;
    let remaining = playbackTime;
    for (let index = 0; index < recordingFragments.length; index += 1) {
      const fragment = recordingFragments[index];
      if (remaining <= (fragment.duration || 0) || index === recordingFragments.length - 1) {
        const start = sessionStarts.get(fragment.sessionUid);
        return start == null ? null : new Date(start + remaining * 1000).toISOString();
      }
      remaining -= fragment.duration || 0;
    }
    return null;
  }, [playbackTime, isPlaybackActive, recordingFragments, sessionStarts]);
  const playbackConnectionError = audioResolutionError ?? videoResolutionError;
  return {
    audioPlayerRef, videoPlayerRef, recordingFragments, videoSrc, audioResolutionError,
    videoResolutionError, playbackConnectionError, playbackTime, playbackAbsoluteTime,
    isPlaybackActive, hasRecordingAudio, recordingDownloadTarget, retryPlayback,
    handlePlaybackTimeUpdate, handleFragmentChange, handleSegmentClick,
  };
}
