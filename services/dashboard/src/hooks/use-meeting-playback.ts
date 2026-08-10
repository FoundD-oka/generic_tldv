"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { AudioFragment, AudioPlayerHandle } from "@/components/recording/audio-player";
import type { VideoPlayerHandle } from "@/components/recording/video-player";
import { vexaAPI } from "@/lib/api";
import type { RecordingData, TranscriptSegment } from "@/types/vexa";

export type MeetingPlayback = {
  audioPlayerRef: React.RefObject<AudioPlayerHandle | null>;
  videoPlayerRef: React.RefObject<VideoPlayerHandle | null>;
  recordingFragments: AudioFragment[];
  videoSrc: string | null;
  playbackConnectionError: string | null;
  playbackTime: number | null;
  playbackAbsoluteTime: string | null;
  isPlaybackActive: boolean;
  hasRecordingAudio: boolean;
  recordingDownloadTarget: { recordingId: number; webmUrl: string } | null;
  handlePlaybackTimeUpdate: (time: number) => void;
  handleFragmentChange: (index: number) => void;
  handleSegmentClick: (startTimeSeconds: number, absoluteStartTime?: string) => void;
};

export function useMeetingPlayback(recordings: RecordingData[], transcripts: TranscriptSegment[]): MeetingPlayback {
  const audioPlayerRef = useRef<AudioPlayerHandle>(null);
  const videoPlayerRef = useRef<VideoPlayerHandle>(null);
  const [playbackTime, setPlaybackTime] = useState<number | null>(null);
  const [isPlaybackActive, setIsPlaybackActive] = useState(false);
  const [pendingSeekTime, setPendingSeekTime] = useState<number | null>(null);
  const [, setActiveFragmentIndex] = useState(0);
  const [recordingFragments, setRecordingFragments] = useState<AudioFragment[]>([]);
  const [videoSrc, setVideoSrc] = useState<string | null>(null);
  const [playbackConnectionError, setPlaybackConnectionError] = useState<string | null>(null);
  const [recordingDownloadTarget, setRecordingDownloadTarget] = useState<{ recordingId: number; webmUrl: string } | null>(null);
  const audioMediaSignature = useMemo(() => recordings
    .filter((r) => (r.status === "completed" || r.status === "in_progress") && r.playback_url?.audio)
    .sort((a, b) => a.created_at.localeCompare(b.created_at))
    .map((r) => `${r.id}:${r.playback_url?.audio ?? ""}`).join("|"), [recordings]);

  useEffect(() => {
    if (!audioMediaSignature) {
      // Preserve the original immediate reset when no canonical master is available.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setRecordingFragments([]); setRecordingDownloadTarget(null); setPlaybackConnectionError(null); return; }
    let cancelled = false;
    void (async () => {
      try {
        const available = recordings.filter((r) => (r.status === "completed" || r.status === "in_progress") && r.playback_url?.audio)
          .sort((a, b) => a.created_at.localeCompare(b.created_at));
        const results = await Promise.all(available.map(async (recording) => {
          const result = await vexaAPI.getRecordingMasterStreamUrl(recording.id, "audio");
          return result ? { recordingId: recording.id, fragment: { src: result.url, duration: result.duration_seconds ?? 0, sessionUid: recording.session_uid, createdAt: recording.created_at } as AudioFragment } : null;
        }));
        if (cancelled) return;
        const resolved = results.filter((entry): entry is { recordingId: number; fragment: AudioFragment } => entry !== null);
        setRecordingFragments(resolved.map((entry) => entry.fragment));
        setRecordingDownloadTarget(resolved[0] ? { recordingId: resolved[0].recordingId, webmUrl: resolved[0].fragment.src } : null);
        setPlaybackConnectionError(null);
      } catch (error) {
        if (!cancelled) { setPlaybackConnectionError(error instanceof Error ? error.message : String(error)); setRecordingFragments([]); setRecordingDownloadTarget(null); }
      }
    })();
    return () => { cancelled = true; };
  }, [audioMediaSignature, recordings]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        for (const recording of recordings) {
          if ((recording.status !== "completed" && recording.status !== "in_progress") || !recording.playback_url?.video) continue;
          const result = await vexaAPI.getRecordingMasterStreamUrl(recording.id, "video");
          if (result && !cancelled) { setVideoSrc(result.url); setPlaybackConnectionError(null); return; }
        }
        if (!cancelled) setVideoSrc(null);
      } catch (error) {
        if (!cancelled) { setPlaybackConnectionError(error instanceof Error ? error.message : String(error)); setVideoSrc(null); }
      }
    })();
    return () => { cancelled = true; };
  }, [recordings]);

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
      // Single recording — start_time is the seek position
      audioPlayerRef.current?.seekTo(startTimeSeconds);
      videoPlayerRef.current?.seekTo(startTimeSeconds);
      setPlaybackTime(startTimeSeconds);
      setIsPlaybackActive(true);
      return;
    }

    // Multi-fragment: find which fragment this segment belongs to
    let targetFragmentIndex = 0;
    if (absoluteStartTime) {
      const segTimeMs = new Date(absoluteStartTime).getTime();
      const matchingSegment = transcripts.find(
        s => s.absolute_start_time === absoluteStartTime
      );
      if (matchingSegment?.session_uid) {
        const uidIndex = recordingFragments.findIndex(
          f => f.sessionUid === matchingSegment.session_uid
        );
        if (uidIndex >= 0) targetFragmentIndex = uidIndex;
      } else {
        // Fallback: find fragment by derived session start
        for (let i = recordingFragments.length - 1; i >= 0; i--) {
          const uid = recordingFragments[i].sessionUid;
          const sessionStart = sessionStarts.get(uid);
          if (sessionStart != null && sessionStart <= segTimeMs) {
            targetFragmentIndex = i;
            break;
          }
        }
      }
    }

    audioPlayerRef.current?.seekToFragment(targetFragmentIndex, startTimeSeconds);
    const virtualOffset = recordingFragments
      .slice(0, targetFragmentIndex)
      .reduce((sum, f) => sum + (f.duration || 0), 0);
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
        const start = sessionStarts.get(fragment.sessionUid); return start == null ? null : new Date(start + remaining * 1000).toISOString();
      }
      remaining -= fragment.duration || 0;
    }
    return null;
  }, [playbackTime, isPlaybackActive, recordingFragments, sessionStarts]);
  return { audioPlayerRef, videoPlayerRef, recordingFragments, videoSrc, playbackConnectionError, playbackTime, playbackAbsoluteTime, isPlaybackActive, hasRecordingAudio, recordingDownloadTarget, handlePlaybackTimeUpdate, handleFragmentChange, handleSegmentClick };
}
