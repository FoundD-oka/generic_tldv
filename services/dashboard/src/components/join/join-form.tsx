"use client";

import { useState, useMemo } from "react";
import { Video, Loader2, Check, AlertCircle, Sparkles, Mic, UserCheck, Monitor } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";
import { vexaAPI } from "@/lib/api";
import { useLiveStore } from "@/stores/live-store";
import { useRuntimeConfig } from "@/hooks/use-runtime-config";
import type { Platform } from "@/types/vexa";
import { PLATFORM_CONFIG } from "@/types/vexa";
import { LanguagePicker } from "@/components/language-picker";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { DocsLink } from "@/components/docs/docs-link";
import { useAuthStore } from "@/stores/auth-store";
import { shouldTriggerZoomOAuth, startZoomOAuth } from "@/lib/zoom-oauth-client";
import {
  DEFAULT_BOT_NAME,
  DEFAULT_TRANSCRIPTION_LANGUAGE,
  applyBotCreationDefaults,
  withPostMeetingAutoStop,
} from "@/lib/bot-create-defaults";

interface JoinFormProps {
  onSuccess?: (meetingId: string, platform: Platform, nativeId: string) => void;
}

type CreateBotRequestWithVideo = ReturnType<typeof withPostMeetingAutoStop> & {
  video?: boolean;
  video_receive_enabled?: boolean;
};

export function JoinForm({ onSuccess }: JoinFormProps) {
  const { setActiveMeeting } = useLiveStore();
  const { config } = useRuntimeConfig();
  const user = useAuthStore((state) => state.user);
  const isHosted = config?.hostedMode ?? false;

  // Depleted state: user has active subscription but max_concurrent_bots === 0
  const isDepleted = isHosted && user?.max_concurrent_bots === 0;

  const [platform, setPlatform] = useState<Platform>("google_meet");
  const [meetingId, setMeetingId] = useState("");
  const [passcode, setPasscode] = useState("");
  const [botName, setBotName] = useState(() => {
    if (typeof window !== "undefined") {
      return localStorage.getItem("vexa-join-bot-name") || DEFAULT_BOT_NAME;
    }
    return DEFAULT_BOT_NAME;
  });
  const [language, setLanguage] = useState(DEFAULT_TRANSCRIPTION_LANGUAGE);
  const [transcribeEnabled, setTranscribeEnabled] = useState(true);
  const [videoRecordingEnabled, setVideoRecordingEnabled] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [touched, setTouched] = useState<Record<string, boolean>>({});

  const platformConfig = PLATFORM_CONFIG[platform];

  const validateMeetingId = (id: string): boolean => {
    if (!id.trim()) return false;
    if (platform === "google_meet") {
      return /^[a-z]{3}-[a-z]{4}-[a-z]{3}$/.test(id.trim().toLowerCase());
    }
    if (platform === "zoom") {
      return /^\d{9,11}$/.test(id.trim());
    }
    return id.trim().length > 0;
  };

  const meetingIdValidation = useMemo(() => {
    if (!meetingId) return { valid: false, message: "" };
    const isValid = validateMeetingId(meetingId);
    return {
      valid: isValid,
      message: isValid
        ? "有効な会議IDです"
        : platform === "google_meet"
        ? "形式: abc-defg-hij"
        : "有効な会議IDを入力してください",
    };
  }, [meetingId, platform]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    const cleanMeetingId = meetingId.trim().toLowerCase();

    if (!validateMeetingId(cleanMeetingId)) {
      toast.error("会議IDを確認してください", {
        description: `${platformConfig.name} の有効な会議IDを入力してください`,
      });
      return;
    }

    if (platform === "teams" && !passcode.trim()) {
      toast.error("パスコードが必要です", {
        description: "Microsoft Teamsの会議にはパスコードが必要です",
      });
      return;
    }

    setIsSubmitting(true);

    const request: CreateBotRequestWithVideo = applyBotCreationDefaults(
      withPostMeetingAutoStop({
        platform,
        native_meeting_id: cleanMeetingId,
      }),
      config
    );

    if ((platform === "teams" || platform === "zoom") && passcode) {
      request.passcode = passcode.trim();
    }

    // Set bot name - use custom name or configured default
    request.bot_name = botName.trim() || request.bot_name || DEFAULT_BOT_NAME;

    // Persist to localStorage
    if (typeof window !== "undefined") {
      localStorage.setItem("vexa-join-bot-name", request.bot_name);
    }

    if (language === "auto") {
      delete request.language;
    } else if (language) {
      request.language = language;
    }

    if (!transcribeEnabled) {
      request.transcribe_enabled = false;
    }

    if (authenticated) {
      request.authenticated = true;
    }

    if (videoRecordingEnabled) {
      request.video = true;
      request.video_receive_enabled = true;
    }

    try {
      const meeting = await vexaAPI.createBot(request);

      toast.success("ボットが会議に参加中です", {
        description: videoRecordingEnabled
          ? "文字起こしボットが会議に接続し、画面録画も開始します"
          : "文字起こしボットが会議に接続しています",
      });

      setActiveMeeting(meeting);
      onSuccess?.(meeting.id, platform, cleanMeetingId);

    } catch (error) {
      console.error("Failed to create bot:", error);

      if (
        shouldTriggerZoomOAuth(error, request.platform) &&
        request.platform === "zoom" &&
        user?.email
      ) {
        try {
          toast.info("Zoom認証が必要です", {
            description:
              "Zoomへ移動します。アプリ利用権限のあるZoomアカウントでログインしてください。",
          });
          await startZoomOAuth({
            userEmail: user.email,
            pendingRequest: request,
            returnTo: "/join",
          });
          return;
        } catch (oauthError) {
          toast.error("Zoom認証の開始に失敗しました", {
            description: (oauthError as Error).message,
          });
        }
      }

      toast.error("会議への参加に失敗しました", {
        description: (error as Error).message,
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  const submitLabel = videoRecordingEnabled
    ? "録画つきで開始"
    : transcribeEnabled
    ? "文字起こしを開始"
    : "録音を開始";

  const helperText = videoRecordingEnabled
    ? transcribeEnabled
      ? "ボットが会議に参加し、リアルタイム文字起こしと画面録画を行います"
      : "ボットが会議に参加し、音声録音と画面録画を行います"
    : transcribeEnabled
    ? "ボットが会議に参加し、リアルタイムで文字起こしします"
    : "ボットが会議に参加し、音声のみ録音します";

  return (
    <Card>
      <CardHeader>
        <CardTitle>会議に参加</CardTitle>
        <CardDescription>
          文字起こしボットを会議に参加させ、記録と文字起こしを行います
        </CardDescription>
      </CardHeader>
      <CardContent>
        {/* Depleted banner */}
        {isDepleted && (
          <div className="mb-6 rounded-lg bg-amber-950/20 border border-amber-900/30 p-3">
            <p className="text-sm text-amber-300 font-medium">
              ボット起動は無効です。クレジットが不足しています
            </p>
            <p className="text-xs text-amber-400/60 mt-1">
              <a
                href={`${config?.webappUrl || "https://vexa.ai"}/account`}
                className="underline hover:text-amber-300"
              >
                残高を追加
              </a>{" "}
              するとボット起動を再開できます。
            </p>
          </div>
        )}
        <form onSubmit={handleSubmit} className={cn("space-y-6", isDepleted && "opacity-50 pointer-events-none")}>
          {/* Platform Selection */}
          <fieldset className="space-y-3">
            <legend className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70">会議サービス</legend>
            <div className="grid grid-cols-3 gap-3" role="radiogroup" aria-label="会議サービスを選択">
              <button
                type="button"
                role="radio"
                aria-checked={platform === "google_meet"}
                onClick={() => {
                  setPlatform("google_meet");
                  setMeetingId("");
                  setTouched({});
                }}
                className={cn(
                  "relative flex flex-col items-center gap-2 p-4 rounded-xl border-2 transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-green-500 focus-visible:ring-offset-2",
                  platform === "google_meet"
                    ? "border-green-500 bg-green-50/50 dark:bg-green-950/30 shadow-sm shadow-green-500/20"
                    : "border-muted hover:border-green-500/50 hover:bg-green-50/30 dark:hover:bg-green-950/10"
                )}
              >
                {platform === "google_meet" && (
                  <div className="absolute top-2 right-2">
                    <Check className="h-4 w-4 text-green-500" />
                  </div>
                )}
                <div className={cn(
                  "w-10 h-10 rounded-full flex items-center justify-center transition-all",
                  platform === "google_meet"
                    ? "bg-green-500 shadow-lg shadow-green-500/30"
                    : "bg-green-500/20"
                )}>
                  <Video className={cn(
                    "h-5 w-5 transition-colors",
                    platform === "google_meet" ? "text-white" : "text-green-600 dark:text-green-400"
                  )} />
                </div>
                <span className={cn(
                  "font-medium text-sm transition-colors",
                  platform === "google_meet" ? "text-green-700 dark:text-green-300" : "text-muted-foreground"
                )}>
                  Google Meet
                </span>
              </button>
              <button
                type="button"
                role="radio"
                aria-checked={platform === "teams"}
                onClick={() => {
                  setPlatform("teams");
                  setMeetingId("");
                  setTouched({});
                }}
                className={cn(
                  "relative flex flex-col items-center gap-2 p-4 rounded-xl border-2 transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2",
                  platform === "teams"
                    ? "border-blue-500 bg-blue-50/50 dark:bg-blue-950/30 shadow-sm shadow-blue-500/20"
                    : "border-muted hover:border-blue-500/50 hover:bg-blue-50/30 dark:hover:bg-blue-950/10"
                )}
              >
                {platform === "teams" && (
                  <div className="absolute top-2 right-2">
                    <Check className="h-4 w-4 text-blue-500" />
                  </div>
                )}
                <div className={cn(
                  "w-10 h-10 rounded-full flex items-center justify-center transition-all",
                  platform === "teams"
                    ? "bg-blue-600 shadow-lg shadow-blue-500/30"
                    : "bg-blue-500/20"
                )}>
                  <Video className={cn(
                    "h-5 w-5 transition-colors",
                    platform === "teams" ? "text-white" : "text-blue-600 dark:text-blue-400"
                  )} />
                </div>
                <span className={cn(
                  "font-medium text-sm transition-colors",
                  platform === "teams" ? "text-blue-700 dark:text-blue-300" : "text-muted-foreground"
                )}>
                  Microsoft Teams
                </span>
              </button>
              <button
                type="button"
                role="radio"
                aria-checked={platform === "zoom"}
                aria-disabled={isHosted}
                onClick={() => {
                  if (isHosted) return;
                  setPlatform("zoom");
                  setMeetingId("");
                  setTouched({});
                }}
                className={cn(
                  "relative flex flex-col items-center gap-2 p-4 rounded-xl border-2 transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 focus-visible:ring-offset-2",
                  isHosted
                    ? "border-muted opacity-50 cursor-not-allowed"
                    : platform === "zoom"
                    ? "border-blue-400 bg-blue-50/50 dark:bg-blue-950/30 shadow-sm shadow-blue-400/20"
                    : "border-muted hover:border-blue-400/50 hover:bg-blue-50/30 dark:hover:bg-blue-950/10"
                )}
              >
                {!isHosted && platform === "zoom" && (
                  <div className="absolute top-2 right-2">
                    <Check className="h-4 w-4 text-blue-400" />
                  </div>
                )}
                <div className={cn(
                  "w-10 h-10 rounded-full flex items-center justify-center transition-all",
                  isHosted
                    ? "bg-blue-400/10"
                    : platform === "zoom"
                    ? "bg-blue-500 shadow-lg shadow-blue-400/30"
                    : "bg-blue-400/20"
                )}>
                  <Video className={cn(
                    "h-5 w-5 transition-colors",
                    isHosted
                      ? "text-muted-foreground"
                      : platform === "zoom" ? "text-white" : "text-blue-500 dark:text-blue-400"
                  )} />
                </div>
                <span className={cn(
                  "font-medium text-sm transition-colors",
                  isHosted
                    ? "text-muted-foreground"
                    : platform === "zoom" ? "text-blue-600 dark:text-blue-300" : "text-muted-foreground"
                )}>
                  Zoom
                </span>
                {isHosted && (
                  <span className="text-[10px] font-medium text-muted-foreground bg-muted px-1.5 py-0.5 rounded-full">
                    近日対応
                  </span>
                )}
              </button>
            </div>
          </fieldset>

          {/* Meeting ID */}
          <div className="space-y-2">
            <Label htmlFor="meetingId">会議ID</Label>
            <div className="relative">
              <Input
                id="meetingId"
                placeholder={platformConfig.placeholder}
                value={meetingId}
                onChange={(e) => setMeetingId(e.target.value)}
                onBlur={() => setTouched({ ...touched, meetingId: true })}
                aria-describedby="meetingId-description"
                aria-invalid={touched.meetingId && meetingId ? !meetingIdValidation.valid : undefined}
                className={cn(
                  "font-mono pr-10 transition-all",
                  touched.meetingId && meetingId && (
                    meetingIdValidation.valid
                      ? "border-green-500 focus-visible:ring-green-500/20"
                      : "border-red-500 focus-visible:ring-red-500/20"
                  )
                )}
              />
              {touched.meetingId && meetingId && (
                <div className="absolute right-3 top-1/2 -translate-y-1/2">
                  {meetingIdValidation.valid ? (
                    <Check className="h-4 w-4 text-green-500" />
                  ) : (
                    <AlertCircle className="h-4 w-4 text-red-500" />
                  )}
                </div>
              )}
            </div>
            <p
              id="meetingId-description"
              className={cn(
                "text-xs transition-colors",
                touched.meetingId && meetingId
                  ? meetingIdValidation.valid
                    ? "text-green-600 dark:text-green-400"
                    : "text-red-500"
                  : "text-muted-foreground"
              )}
              role={touched.meetingId && meetingId && !meetingIdValidation.valid ? "alert" : undefined}
            >
              {touched.meetingId && meetingId && meetingIdValidation.message
                ? meetingIdValidation.message
                : platform === "google_meet"
                ? "URL内の会議コードを入力してください（例: abc-defg-hij）"
                : "Teams招待に記載された会議IDを入力してください"}
            </p>
          </div>

          {/* Passcode (Teams and Zoom) */}
          {(platform === "teams" || platform === "zoom") && (
            <div className="space-y-2">
              <Label htmlFor="passcode">パスコード</Label>
              <Input
                id="passcode"
                placeholder="会議パスコードを入力"
                value={passcode}
                onChange={(e) => setPasscode(e.target.value)}
              />
            </div>
          )}

          {/* Bot Name (optional) */}
          <div className="space-y-2">
            <Label htmlFor="botName">ボット名（任意）</Label>
            <Input
              id="botName"
              placeholder="会議アシスタント"
              value={botName}
              onChange={(e) => setBotName(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              この名前が会議の参加者一覧に表示されます
            </p>
          </div>

          {/* Transcription Toggle */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label htmlFor="transcribeEnabled" className="flex items-center gap-2 cursor-pointer">
                <Mic className="h-3.5 w-3.5" />
                リアルタイム文字起こし
              </Label>
              <Switch
                id="transcribeEnabled"
                checked={transcribeEnabled}
                onCheckedChange={setTranscribeEnabled}
              />
            </div>
            {!transcribeEnabled && (
              <p className="text-xs text-muted-foreground">
                音声のみ録音します。文字起こしは会議ページから後で実行できます。
              </p>
            )}
          </div>

          {/* Video Recording Toggle */}
          <div className="space-y-2 rounded-lg border bg-muted/30 p-3">
            <div className="flex items-center justify-between gap-4">
              <Label htmlFor="videoRecordingEnabled" className="flex items-center gap-2 cursor-pointer">
                <Monitor className="h-3.5 w-3.5" />
                画面録画
              </Label>
              <Switch
                id="videoRecordingEnabled"
                checked={videoRecordingEnabled}
                onCheckedChange={setVideoRecordingEnabled}
              />
            </div>
            <p className="text-xs text-muted-foreground">
              {videoRecordingEnabled
                ? "ボットが見ている会議画面を録画します。負荷と保存容量が増えます。"
                : "必要な会議だけオンにしてください。デフォルトはオフです。"}
            </p>
          </div>

          {/* Authenticated Toggle — coming soon */}
          <div className="space-y-2">
            <div className="flex items-center justify-between opacity-50">
              <Label htmlFor="authenticated" className="flex items-center gap-2 cursor-not-allowed">
                <UserCheck className="h-3.5 w-3.5" />
                認証済み参加
                <span className="text-[10px] font-medium bg-muted px-1.5 py-0.5 rounded">近日</span>
              </Label>
              <Switch
                id="authenticated"
                checked={false}
                disabled
              />
            </div>
          </div>

          {/* Language */}
          {transcribeEnabled && (
          <div className="space-y-2">
            <Label htmlFor="language">文字起こし言語</Label>
            <LanguagePicker
              value={language}
              onValueChange={setLanguage}
              triggerClassName="w-full justify-between"
            />
            {language === "auto" && (
              <p className="text-xs text-muted-foreground">
                自動判定: サービスが言語を自動判定します。
              </p>
            )}
          </div>
          )}

          {/* Submit */}
          <div className="flex items-center">
            <Button
              type="submit"
              className={cn(
                "w-full relative overflow-hidden transition-all duration-300",
                !isSubmitting && meetingIdValidation.valid && "bg-primary hover:bg-primary/90 shadow-lg shadow-primary/25"
              )}
              size="lg"
              disabled={isSubmitting || !meetingIdValidation.valid || isDepleted}
            >
              {isSubmitting ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  会議に接続中...
                </>
              ) : (
                <>
                  <Sparkles className="mr-2 h-4 w-4" />
                  {submitLabel}
                </>
              )}
            </Button>
            {/* <DocsLink href="/docs/rest/bots#create-bot" /> */}
          </div>

          {/* Helpful tip */}
          <p className="text-xs text-center text-muted-foreground">
            {helperText}
          </p>
        </form>
      </CardContent>
    </Card>
  );
}
