"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AudioLines, Loader2, Mic, Square, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { vexaAPI, type SpeakerProfileSummary } from "@/lib/api";
import {
  VOICEPRINT_MAX_RECORDING_SECONDS,
  VOICEPRINT_MIN_RECORDING_SECONDS,
  blobToBase64,
  canEnrollRecordedVoiceprint,
  selectVoiceprintRecorderMimeType,
  voiceprintMediaFormatFromMimeType,
} from "@/lib/voiceprint-recording";

interface RecordedSample {
  blob: Blob;
  durationSeconds: number;
  mediaFormat: string;
}

export default function VoiceprintsPage() {
  const [profiles, setProfiles] = useState<SpeakerProfileSummary[]>([]);
  const [displayName, setDisplayName] = useState("");
  const [recordedSample, setRecordedSample] = useState<RecordedSample | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [isRecording, setIsRecording] = useState(false);
  const [isStarting, setIsStarting] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isPreparingPreview, setIsPreparingPreview] = useState(false);
  const [isPreviewReady, setIsPreviewReady] = useState(false);
  const [audioReviewConfirmed, setAudioReviewConfirmed] = useState(false);
  const [consentConfirmed, setConsentConfirmed] = useState(false);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const startedAtRef = useRef(0);
  const previewUrlRef = useRef<string | null>(null);
  const previewAudioRef = useRef<HTMLAudioElement | null>(null);
  const loadingOverlayRef = useRef<HTMLDivElement | null>(null);
  const tickerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const autoStopRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(false);
  const recordingStartRunRef = useRef(0);
  const recordingStartPendingRef = useRef(false);

  const loadProfiles = useCallback(async () => {
    try {
      const data = await vexaAPI.getSpeakerProfiles();
      setProfiles(data.profiles);
    } catch (error) {
      toast.error("声紋の読み込みに失敗しました", {
        description: (error as Error).message,
      });
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadProfiles();
  }, [loadProfiles]);

  const clearTimers = useCallback(() => {
    if (tickerRef.current) clearInterval(tickerRef.current);
    if (autoStopRef.current) clearTimeout(autoStopRef.current);
    tickerRef.current = null;
    autoStopRef.current = null;
  }, []);

  const stopTracks = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  }, []);

  const replacePreviewUrl = useCallback((nextUrl: string | null) => {
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    previewUrlRef.current = nextUrl;
    setPreviewUrl(nextUrl);
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      recordingStartRunRef.current += 1;
      recordingStartPendingRef.current = false;
      clearTimers();
      const recorder = recorderRef.current;
      if (recorder) {
        recorder.ondataavailable = null;
        recorder.onerror = null;
        recorder.onstop = null;
        if (recorder.state !== "inactive") recorder.stop();
      }
      stopTracks();
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
      previewUrlRef.current = null;
    };
  }, [clearTimers, stopTracks]);

  const stopRecording = useCallback(() => {
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") {
      setIsPreviewReady(false);
      setIsPreparingPreview(true);
      recorder.stop();
    }
  }, []);

  const finishPreparingPreview = useCallback(() => {
    setIsPreviewReady(true);
    setIsPreparingPreview(false);
  }, []);

  const failPreparingPreview = useCallback(() => {
    setIsPreviewReady(false);
    setIsPreparingPreview(false);
    setRecordedSample(null);
    replacePreviewUrl(null);
    toast.error("確認再生を準備できませんでした。録り直してください");
  }, [replacePreviewUrl]);

  useEffect(() => {
    if (isPreparingPreview) loadingOverlayRef.current?.focus();
  }, [isPreparingPreview]);

  useEffect(() => {
    if (!isPreparingPreview) return;
    if (previewUrl) {
      const audio = previewAudioRef.current;
      if (audio && audio.readyState >= HTMLMediaElement.HAVE_METADATA) {
        finishPreparingPreview();
        return;
      }
    }
    const timeout = window.setTimeout(() => {
      setIsPreviewReady(false);
      setIsPreparingPreview(false);
      setRecordedSample(null);
      replacePreviewUrl(null);
      toast.error("確認再生の準備に時間がかかっています。もう一度お試しください");
    }, 10_000);
    return () => window.clearTimeout(timeout);
  }, [finishPreparingPreview, isPreparingPreview, previewUrl, replacePreviewUrl]);

  const startRecording = async () => {
    if (recordingStartPendingRef.current || recorderRef.current?.state === "recording") {
      return;
    }
    if (typeof MediaRecorder === "undefined" || !navigator.mediaDevices?.getUserMedia) {
      toast.error("このブラウザではマイク録音を利用できません");
      return;
    }

    const startRun = recordingStartRunRef.current + 1;
    recordingStartRunRef.current = startRun;
    recordingStartPendingRef.current = true;
    setIsStarting(true);

    clearTimers();
    stopTracks();
    setIsPreparingPreview(false);
    setIsPreviewReady(false);
    replacePreviewUrl(null);
    setRecordedSample(null);
    setAudioReviewConfirmed(false);
    setConsentConfirmed(false);
    setElapsedSeconds(0);

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!mountedRef.current || recordingStartRunRef.current !== startRun) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      streamRef.current = stream;
      const mimeType = selectVoiceprintRecorderMimeType(
        typeof MediaRecorder.isTypeSupported === "function"
          ? MediaRecorder.isTypeSupported.bind(MediaRecorder)
          : undefined
      );
      const recorder = mimeType
        ? new MediaRecorder(stream, { mimeType })
        : new MediaRecorder(stream);
      recorderRef.current = recorder;
      chunksRef.current = [];
      startedAtRef.current = Date.now();

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onerror = () => {
        setIsPreviewReady(false);
        setIsPreparingPreview(false);
        toast.error("録音に失敗しました。マイク設定を確認してください");
      };
      recorder.onstop = () => {
        clearTimers();
        stopTracks();
        setIsRecording(false);
        const durationSeconds = Math.min(
          VOICEPRINT_MAX_RECORDING_SECONDS,
          Math.round(((Date.now() - startedAtRef.current) / 1000) * 10) / 10
        );
        const blob = new Blob(chunksRef.current, {
          type: recorder.mimeType || mimeType || "audio/webm",
        });
        if (blob.size === 0) {
          setIsPreviewReady(false);
          setIsPreparingPreview(false);
          toast.error("音声を録音できませんでした。もう一度お試しください");
          return;
        }
        setElapsedSeconds(durationSeconds);
        setRecordedSample({
          blob,
          durationSeconds,
          mediaFormat: voiceprintMediaFormatFromMimeType(blob.type),
        });
        replacePreviewUrl(URL.createObjectURL(blob));
      };

      recorder.start(250);
      setIsRecording(true);
      tickerRef.current = setInterval(() => {
        setElapsedSeconds(
          Math.min(
            VOICEPRINT_MAX_RECORDING_SECONDS,
            Math.round(((Date.now() - startedAtRef.current) / 1000) * 10) / 10
          )
        );
      }, 100);
      // MediaRecorderの停止処理にもわずかな遅延があるため、サーバー側の
      // 厳格な30秒上限を超えないよう0.5秒手前で自動停止する。
      autoStopRef.current = setTimeout(
        stopRecording,
        (VOICEPRINT_MAX_RECORDING_SECONDS - 0.5) * 1000
      );
    } catch (error) {
      stopTracks();
      setIsPreviewReady(false);
      setIsPreparingPreview(false);
      if (mountedRef.current && recordingStartRunRef.current === startRun) {
        toast.error("マイクを開始できませんでした", {
          description: (error as Error).message,
        });
      }
    } finally {
      if (recordingStartRunRef.current === startRun) {
        recordingStartPendingRef.current = false;
        if (mountedRef.current) setIsStarting(false);
      }
    }
  };

  const canSubmit = canEnrollRecordedVoiceprint({
    displayName,
    hasRecording: recordedSample !== null,
    previewReady: isPreviewReady,
    durationSeconds: recordedSample?.durationSeconds || 0,
    audioReviewConfirmed,
    consentConfirmed,
    submitting: isSubmitting,
  });

  const enroll = async () => {
    if (!recordedSample || !canSubmit) return;
    setIsSubmitting(true);
    try {
      const audioBase64 = await blobToBase64(recordedSample.blob);
      await vexaAPI.enrollVoiceprintFromAudio({
        displayName: displayName.trim(),
        audioBase64,
        mediaFormat: recordedSample.mediaFormat,
      });
      toast.success("声紋を登録しました", {
        description: "次のGemini会議後文字起こしから話者候補の照合に使われます",
      });
      setDisplayName("");
      setRecordedSample(null);
      setIsPreviewReady(false);
      setElapsedSeconds(0);
      setAudioReviewConfirmed(false);
      setConsentConfirmed(false);
      replacePreviewUrl(null);
      await loadProfiles();
    } catch (error) {
      toast.error("声紋の登録に失敗しました", {
        description: (error as Error).message,
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-4xl space-y-6 p-6">
      {isPreparingPreview && (
        <div
          ref={loadingOverlayRef}
          className="fixed inset-0 z-[100] flex items-center justify-center bg-background/80 backdrop-blur-sm"
          role="dialog"
          aria-modal="true"
          aria-live="polite"
          aria-label="確認再生を準備中"
          tabIndex={-1}
        >
          <div className="flex items-center gap-3 rounded-lg border bg-card px-5 py-4 shadow-lg">
            <Loader2 className="h-5 w-5 animate-spin" />
            <span className="font-medium">確認再生を準備しています...</span>
          </div>
        </div>
      )}
      <div
        className="space-y-6"
        inert={isPreparingPreview ? true : undefined}
        aria-hidden={isPreparingPreview ? true : undefined}
      >
      <div>
        <h1 className="flex items-center gap-2 text-3xl font-bold tracking-tight">
          <AudioLines className="h-7 w-7" />声紋管理
        </h1>
        <p className="mt-2 text-muted-foreground">
          会議前に本人の声を登録しておくと、次回以降のGemini会議後文字起こしで話者候補を提示できます。
          候補は自動確定されません。
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>マイクから声紋を登録</CardTitle>
          <CardDescription>
            雑音や他の人の声が入らない場所で、登録する本人が{VOICEPRINT_MIN_RECORDING_SECONDS}〜
            {VOICEPRINT_MAX_RECORDING_SECONDS}秒話してください。録音そのものは保存せず、暗号化した声の特徴だけを保存します。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="space-y-2">
            <Label htmlFor="voiceprint-profile-name">話者名</Label>
            <Input
              id="voiceprint-profile-name"
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
              placeholder="例: 田中"
              maxLength={255}
              disabled={isSubmitting || isRecording || isStarting || isPreparingPreview}
            />
          </div>

          <div className="rounded-lg border p-4">
            <p className="text-sm text-muted-foreground">
              読み上げ例:「本日は会議に参加します。音声確認のため、普段どおりの声で話しています。」
            </p>
            <div className="mt-4 flex flex-wrap items-center gap-3">
              {isRecording ? (
                <Button type="button" variant="destructive" onClick={stopRecording}>
                  <Square className="h-4 w-4" />録音を停止
                </Button>
              ) : (
                <Button type="button" onClick={() => void startRecording()} disabled={isSubmitting || isStarting || isPreparingPreview}>
                  {isStarting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Mic className="h-4 w-4" />}
                  {isStarting ? "マイクを準備中" : recordedSample ? "録り直す" : "録音を開始"}
                </Button>
              )}
              <span className="tabular-nums text-sm font-medium">
                {elapsedSeconds.toFixed(1)} / {VOICEPRINT_MAX_RECORDING_SECONDS}秒
              </span>
              {isRecording && elapsedSeconds < VOICEPRINT_MIN_RECORDING_SECONDS && (
                <span className="text-sm text-amber-600">
                  あと{Math.ceil(VOICEPRINT_MIN_RECORDING_SECONDS - elapsedSeconds)}秒以上話してください
                </span>
              )}
            </div>
          </div>

          {previewUrl && recordedSample && (
            <div className="space-y-3 rounded-lg border p-4">
              <div>
                <p className="font-medium">登録前の確認再生</p>
                <p className="text-sm text-muted-foreground">
                  この{recordedSample.durationSeconds.toFixed(1)}秒の音声からだけ声の特徴を作ります。
                </p>
              </div>
              <audio
                ref={previewAudioRef}
                className="w-full"
                controls
                preload="metadata"
                src={previewUrl}
                aria-label="登録する声の確認再生"
                onLoadedMetadata={finishPreparingPreview}
                onError={failPreparingPreview}
              />
            </div>
          )}

          <label className="flex items-start gap-2 rounded-md border p-3 text-sm">
            <input
              type="checkbox"
              checked={audioReviewConfirmed}
              onChange={(event) => setAudioReviewConfirmed(event.target.checked)}
              disabled={!recordedSample || !isPreviewReady || isSubmitting || isPreparingPreview}
              className="mt-0.5 h-4 w-4 accent-primary"
            />
            <span>録音を再生し、登録する本人の声だけで、他の人の声が混ざっていないことを確認しました。</span>
          </label>

          <label className="flex items-start gap-2 rounded-md border p-3 text-sm">
            <input
              type="checkbox"
              checked={consentConfirmed}
              onChange={(event) => setConsentConfirmed(event.target.checked)}
              disabled={!recordedSample || !isPreviewReady || isSubmitting || isPreparingPreview}
              className="mt-0.5 h-4 w-4 accent-primary"
            />
            <span>
              本人から、今後の会議で話者候補を提示するための声紋登録に同意を得ています。
              声紋は暗号化して保存されます。
            </span>
          </label>

          <Button type="button" onClick={() => void enroll()} disabled={!canSubmit}>
            {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" />}
            {isSubmitting ? "登録中" : "確認した音声で登録"}
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>登録済みの話者</CardTitle>
          <CardDescription>
            1人に複数の声紋を追加できます。削除すると、その話者の声紋と同意記録がすべて削除されます。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {isLoading ? (
            <div className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />読み込み中...
            </div>
          ) : profiles.length === 0 ? (
            <p className="text-sm text-muted-foreground">まだ声紋は登録されていません。</p>
          ) : (
            profiles.map((profile) => (
              <div key={profile.id} className="flex items-center gap-3 rounded-lg border p-3">
                <div className="min-w-0 flex-1">
                  <p className="font-medium">{profile.display_name}</p>
                  <p className="text-sm text-muted-foreground">登録音声 {profile.voiceprint_count}件</p>
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="text-destructive"
                  aria-label={`${profile.display_name}の声紋を削除`}
                  onClick={async () => {
                    if (!window.confirm(`「${profile.display_name}」の声紋をすべて削除しますか？`)) return;
                    try {
                      await vexaAPI.deleteSpeakerProfile(profile.id);
                      toast.success("声紋を削除しました");
                      await loadProfiles();
                    } catch (error) {
                      toast.error("声紋の削除に失敗しました", {
                        description: (error as Error).message,
                      });
                    }
                  }}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
            ))
          )}
        </CardContent>
      </Card>
      </div>
    </div>
  );
}
