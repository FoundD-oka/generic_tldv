"use client";

import { useState, useEffect, useMemo, useCallback } from "react";
import { useRouter } from "next/navigation";
import { Video, Loader2, Sparkles, Monitor, UserCheck, Info, Mic } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";
import { vexaAPI, VexaAPIError } from "@/lib/api";
import { useLiveStore } from "@/stores/live-store";
import { useJoinModalStore } from "@/stores/join-modal-store";
import { useMeetingsStore } from "@/stores/meetings-store";
import { useRuntimeConfig } from "@/hooks/use-runtime-config";
import type { Platform } from "@/types/vexa";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { getUserFriendlyError } from "@/lib/error-messages";
import { getWebappUrl } from "@/lib/docs/webapp-url";
import { parseMeetingInput } from "@/lib/parse-meeting-input";
import { useAuthStore } from "@/stores/auth-store";
import { shouldTriggerZoomOAuth, startZoomOAuth } from "@/lib/zoom-oauth-client";
import { withBasePath } from "@/lib/base-path";
import { DEFAULT_DASHBOARD_BRAND } from "@/lib/dashboard-brand";
import { getDashboardCopy } from "@/lib/dashboard-copy";
import {
  DEFAULT_TRANSCRIPTION_LANGUAGE,
  applyBotCreationDefaults,
  withPostMeetingAutoStop,
} from "@/lib/bot-create-defaults";

const VIDEO_RECORDING_COPY = {
  en: {
    label: "Screen recording",
    enabledHelp: "Records the meeting view seen by the bot. This uses more CPU and storage.",
    disabledHelp: "Turn this on only for meetings that need video. Default is off.",
    startWithRecording: "Start with Recording",
    joiningDescription: "The transcription bot is connecting and screen recording will start.",
  },
  ja: {
    label: "画面録画",
    enabledHelp: "ボットが見ている会議画面を録画します。負荷と保存容量が増えます。",
    disabledHelp: "必要な会議だけオンにしてください。デフォルトはオフです。",
    startWithRecording: "録画つきで開始",
    joiningDescription: "文字起こしボットが接続し、画面録画も開始します。",
  },
} as const;

type CreateBotRequestWithVideo = ReturnType<typeof withPostMeetingAutoStop> & {
  video?: boolean;
  video_receive_enabled?: boolean;
};

export function JoinModal() {
  const router = useRouter();
  const { isOpen, closeModal } = useJoinModalStore();
  const { setActiveMeeting } = useLiveStore();
  const { setCurrentMeeting } = useMeetingsStore();
  const { config } = useRuntimeConfig();
  const user = useAuthStore((state) => state.user);
  const brand = config?.brand || DEFAULT_DASHBOARD_BRAND;
  const copy = getDashboardCopy(brand.locale).joinModal;
  const recordingCopy = VIDEO_RECORDING_COPY[brand.locale];

  const [mode, setMode] = useState<"meeting" | "browser">("meeting");
  const [meetingInput, setMeetingInput] = useState("");
  const [platform, setPlatform] = useState<Platform>("google_meet");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [passcode, setPasscode] = useState("");
  const [wakeWordEnabled, setWakeWordEnabled] = useState(true);
  const [videoRecordingEnabled, setVideoRecordingEnabled] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);

  // Reset form when modal closes.
  useEffect(() => {
    if (!isOpen) {
      setMode("meeting");
      setMeetingInput("");
      setPlatform("google_meet");
      setIsSubmitting(false);
      setPasscode("");
      setWakeWordEnabled(true);
      setVideoRecordingEnabled(false);
      setAuthenticated(false);
    }
  }, [isOpen]);

  // Parse input and auto-detect platform
  const parsedInput = useMemo(() => {
    return parseMeetingInput(meetingInput);
  }, [meetingInput]);

  // Update platform and passcode when detected from URL.
  // platformNeeded URLs (white-label / enterprise) seed the picker with
  // a heuristic default; the user CAN re-pick to override.
  useEffect(() => {
    if (parsedInput) {
      setPlatform(parsedInput.platform);
      if (parsedInput.passcode) {
        setPasscode(parsedInput.passcode);
      }
    }
  }, [parsedInput]);

  // Valid: parsed input present. platformNeeded shows extra UI; submit
  // uses whatever platform is currently selected (state), regardless.
  const isValid = parsedInput !== null;

  const handleSubmit = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();

    if (!parsedInput) {
      toast.error(copy.invalidTitle, {
        description: copy.invalidDescription,
      });
      return;
    }

    // platformNeeded: user-supplied platform is canonical; parser couldn't auto-detect
    // (white-label / enterprise URL like Linux Foundation Zoom). Use the picked
    // platform in the request.
    const effectivePlatform = parsedInput.platform || platform;
    if (parsedInput.platformNeeded && !effectivePlatform) {
      toast.error(copy.platformRequiredTitle, {
        description: copy.platformRequiredDescription,
      });
      return;
    }

    const finalPasscode = parsedInput.passcode || passcode.trim() || undefined;
    if (effectivePlatform === "teams" && !finalPasscode) {
      toast.error(copy.passcodeRequiredTitle, {
        description: copy.passcodeRequiredDescription,
      });
      return;
    }

    setIsSubmitting(true);

    // Path 3 (URL + platform): when parser identified platform, use parsed
    // meetingId. Otherwise (platformNeeded), send meeting_url + platform; backend
    // synthesizes/extracts native_meeting_id best-effort.
    const request: CreateBotRequestWithVideo = applyBotCreationDefaults(
      withPostMeetingAutoStop({
        platform: effectivePlatform!,
        native_meeting_id: parsedInput.meetingId || "",
        voice_agent_enabled: wakeWordEnabled,
      }),
      config
    );

    if ((effectivePlatform === "teams" || effectivePlatform === "zoom") && finalPasscode) {
      request.passcode = finalPasscode;
    }

    if (parsedInput.originalUrl) {
      request.meeting_url = parsedInput.originalUrl;
    }

    request.language = DEFAULT_TRANSCRIPTION_LANGUAGE;
    request.transcribe_enabled = true;

    if (authenticated) {
      request.authenticated = true;
    }

    if (videoRecordingEnabled) {
      request.video = true;
      request.video_receive_enabled = true;
    }

    try {
      const meeting = await vexaAPI.createBot(request);

      toast.success(copy.botJoining, {
        description: videoRecordingEnabled
          ? recordingCopy.joiningDescription
          : copy.botJoiningDescription,
      });

      setActiveMeeting(meeting);
      setCurrentMeeting(meeting);
      closeModal();

      router.push(`/meetings/${meeting.id}`);
    } catch (error) {
      console.error("Failed to create bot:", error);

      if (error instanceof VexaAPIError && error.status === 402) {
        toast.error(copy.subscriptionRequiredTitle, {
          description: copy.subscriptionRequiredDescription,
          action: {
            label: getDashboardCopy(brand.locale).meetings.viewPlans,
            onClick: () => window.open(`${getWebappUrl()}/pricing`, "_blank"),
          },
        });
        return;
      }

      if (
        shouldTriggerZoomOAuth(error, request.platform) &&
        request.platform === "zoom" &&
        user?.email
      ) {
        try {
          toast.info(copy.zoomAuthTitle, {
            description: copy.zoomAuthDescription,
          });
          await startZoomOAuth({
            userEmail: user.email,
            pendingRequest: request,
            returnTo: "/meetings",
          });
          return;
        } catch (oauthError) {
          toast.error(copy.zoomAuthStartFailed, {
            description: (oauthError as Error).message,
          });
        }
      }

      const { title, description } = getUserFriendlyError(error as Error);
      toast.error(title, { description });
    } finally {
      setIsSubmitting(false);
    }
  }, [
    parsedInput,
    platform,
    passcode,
    wakeWordEnabled,
    videoRecordingEnabled,
    authenticated,
    brand.locale,
    copy,
    recordingCopy,
    config,
    setActiveMeeting,
    setCurrentMeeting,
    closeModal,
    router,
    user,
  ]);

  const handleBrowserSession = useCallback(async () => {
    setIsSubmitting(true);
    try {
      const body: Record<string, string> = { mode: "browser_session" };
      try {
        const git = JSON.parse(localStorage.getItem("vexa-browser-git") || "{}");
        if (git.repo && git.token) {
          body.workspaceGitRepo = git.repo;
          body.workspaceGitToken = git.token;
          body.workspaceGitBranch = git.branch || "main";
        }
      } catch {}
      const response = await fetch(withBasePath("/api/vexa/bots"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        const err = await response.json().catch(() => ({ detail: "Failed" }));
        throw new Error(err.detail || "Failed to create browser session");
      }
      const meeting = await response.json();
      toast.success(copy.starting);
      closeModal();
      setTimeout(() => router.push(`/meetings/${meeting.id}`), 2000);
    } catch (error) {
      const { title, description } = getUserFriendlyError(error as Error);
      toast.error(title, { description });
    } finally {
      setIsSubmitting(false);
    }
  }, [closeModal, copy.starting, router]);

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && closeModal()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <div className="h-8 w-8 rounded-lg bg-primary flex items-center justify-center">
              <Video className="h-4 w-4 text-primary-foreground" />
            </div>
            {copy.title}
          </DialogTitle>
          <DialogDescription>
            {copy.description}
          </DialogDescription>
        </DialogHeader>

        {/* Mode toggle */}
        <div className="flex gap-1 p-1 bg-muted rounded-lg mt-2">
          <button
            type="button"
            className={cn(
              "flex-1 flex items-center justify-center gap-2 px-3 py-1.5 rounded-md text-sm font-medium transition-colors",
              mode === "meeting" ? "bg-background shadow-sm" : "text-muted-foreground hover:text-foreground"
            )}
            onClick={() => setMode("meeting")}
          >
            <Video className="h-3.5 w-3.5" />
            {copy.modeMeeting}
          </button>
          <button
            type="button"
            className={cn(
              "flex-1 flex items-center justify-center gap-2 px-3 py-1.5 rounded-md text-sm font-medium transition-colors",
              mode === "browser" ? "bg-background shadow-sm" : "text-muted-foreground hover:text-foreground"
            )}
            onClick={() => setMode("browser")}
          >
            <Monitor className="h-3.5 w-3.5" />
            {copy.modeBrowser}
          </button>
        </div>
        <p className="px-1 text-xs text-muted-foreground">
          {mode === "meeting" ? copy.modeMeetingDescription : copy.modeBrowserDescription}
        </p>

        {mode === "browser" ? (
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">
              {copy.browserDescription}
            </p>
            <Button
              className="w-full h-12 text-base"
              onClick={handleBrowserSession}
              disabled={isSubmitting}
            >
              {isSubmitting ? (
                <>
                  <Loader2 className="mr-2 h-5 w-5 animate-spin" />
                  {copy.starting}
                </>
              ) : (
                <>
                  <Monitor className="mr-2 h-5 w-5" />
                  {copy.startBrowserSession}
                </>
              )}
            </Button>
          </div>
        ) : (

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Meeting Input */}
          <div className="space-y-2">
            <Label htmlFor="meetingInput" className="sr-only">
              {copy.meetingInputLabel}
            </Label>
            <div className="relative">
              {parsedInput && (
                <div className="absolute left-3 top-1/2 -translate-y-1/2 z-10 animate-fade-in">
                  {parsedInput.platform === "google_meet" ? (
                    <div className="h-6 w-6 rounded-md bg-green-500 flex items-center justify-center shadow-sm">
                      <svg className="h-4 w-4 text-white" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/>
                      </svg>
                    </div>
                  ) : parsedInput.platform === "zoom" ? (
                    <div className="h-6 w-6 rounded-md bg-blue-500 flex items-center justify-center shadow-sm">
                      <Video className="h-4 w-4 text-white" />
                    </div>
                  ) : (
                    <div className="h-6 w-6 rounded-md bg-[#5059C9] flex items-center justify-center shadow-sm">
                      <svg className="h-4 w-4 text-white" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M19.98 7.89A2.14 2.14 0 1 0 17.84 10V7.89h2.14zm-5.27 0A2.14 2.14 0 1 0 12.58 10V7.89h2.13zM12.58 14.5h-1.11v-1.8h1.11zm4.13 0h-1.11v-1.8h1.11zM21 11.36v5.5a3 3 0 0 1-3 3h-3.86v-4.5H12.5v4.5H8.64v-4.5h-1.78a3 3 0 0 1-3-3v-5.5a3 3 0 0 1 3-3h11.14a3 3 0 0 1 3 3z"/>
                      </svg>
                    </div>
                  )}
                </div>
              )}
              <Input
                id="meetingInput"
                placeholder={copy.meetingInputPlaceholder}
                value={meetingInput}
                onChange={(e) => setMeetingInput(e.target.value)}
                className={cn(
                  "h-12 text-base pr-12 font-mono transition-all",
                  parsedInput ? "pl-12" : "pl-4",
                  meetingInput && (
                    isValid
                      ? parsedInput?.platform === "google_meet"
                        ? "border-green-500 focus-visible:ring-green-500/20"
                        : parsedInput?.platform === "zoom"
                        ? "border-blue-500 focus-visible:ring-blue-500/20"
                        : "border-[#5059C9] focus-visible:ring-[#5059C9]/20"
                      : "border-orange-500 focus-visible:ring-orange-500/20"
                  )
                )}
                autoFocus
                autoComplete="off"
              />
              {meetingInput && isValid && (
                <div className="absolute right-3 top-1/2 -translate-y-1/2">
                  <div className={cn(
                    "h-6 w-6 rounded-full flex items-center justify-center animate-fade-in",
                    parsedInput?.platform === "google_meet"
                      ? "bg-green-100 dark:bg-green-950"
                      : parsedInput?.platform === "zoom"
                      ? "bg-blue-100 dark:bg-blue-950"
                      : "bg-indigo-100 dark:bg-indigo-950"
                  )}>
                    <svg className={cn(
                      "h-4 w-4",
                      parsedInput?.platform === "google_meet"
                        ? "text-green-600 dark:text-green-400"
                        : parsedInput?.platform === "zoom"
                        ? "text-blue-600 dark:text-blue-400"
                        : "text-[#5059C9]"
                    )} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
                      <polyline points="20 6 9 17 4 12" />
                    </svg>
                  </div>
                </div>
              )}
            </div>

            {/* v0.10.5 — platformNeeded fallback for white-label / enterprise URLs */}
            {parsedInput && parsedInput.platformNeeded && (
              <div className="space-y-2 animate-fade-in rounded-lg border border-amber-500/30 bg-amber-500/5 p-3">
                <p className="text-xs text-amber-700 dark:text-amber-300">
                  {copy.platformNeeded}
                </p>
                <fieldset className="grid grid-cols-3 gap-2" role="radiogroup" aria-label={copy.platformRadioLabel}>
                  <button
                    type="button"
                    role="radio"
                    aria-checked={platform === "google_meet"}
                    onClick={() => setPlatform("google_meet")}
                    className={cn(
                      "flex items-center justify-center gap-2 px-3 py-2 rounded-md border-2 text-xs font-medium transition-all",
                      platform === "google_meet"
                        ? "border-green-500 bg-green-50 dark:bg-green-950 text-green-700 dark:text-green-300"
                        : "border-muted hover:border-green-500/50"
                    )}
                  >
                    Google Meet
                  </button>
                  <button
                    type="button"
                    role="radio"
                    aria-checked={platform === "zoom"}
                    onClick={() => setPlatform("zoom")}
                    className={cn(
                      "flex items-center justify-center gap-2 px-3 py-2 rounded-md border-2 text-xs font-medium transition-all",
                      platform === "zoom"
                        ? "border-blue-500 bg-blue-50 dark:bg-blue-950 text-blue-700 dark:text-blue-300"
                        : "border-muted hover:border-blue-500/50"
                    )}
                  >
                    Zoom
                  </button>
                  <button
                    type="button"
                    role="radio"
                    aria-checked={platform === "teams"}
                    onClick={() => setPlatform("teams")}
                    className={cn(
                      "flex items-center justify-center gap-2 px-3 py-2 rounded-md border-2 text-xs font-medium transition-all",
                      platform === "teams"
                        ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-950 text-indigo-700 dark:text-indigo-300"
                        : "border-muted hover:border-indigo-500/50"
                    )}
                  >
                    Microsoft Teams
                  </button>
                </fieldset>
              </div>
            )}

            {parsedInput && !parsedInput.platformNeeded && (
              <div className="flex items-center gap-2 text-sm animate-fade-in">
                <span className={cn(
                  "inline-flex items-center gap-1.5 px-2 py-1 rounded-md text-xs font-medium",
                  parsedInput.platform === "google_meet"
                    ? "bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-300"
                    : parsedInput.platform === "zoom"
                    ? "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300"
                    : "bg-indigo-100 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300"
                )}>
                  {parsedInput.platform === "google_meet" ? (
                    <svg className="h-3 w-3" viewBox="0 0 24 24" fill="currentColor">
                      <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/>
                    </svg>
                  ) : parsedInput.platform === "zoom" ? (
                    <Video className="h-3 w-3" />
                  ) : (
                    <svg className="h-3 w-3" viewBox="0 0 24 24" fill="currentColor">
                      <path d="M19.98 7.89A2.14 2.14 0 1 0 17.84 10V7.89h2.14zm-5.27 0A2.14 2.14 0 1 0 12.58 10V7.89h2.13zM12.58 14.5h-1.11v-1.8h1.11zm4.13 0h-1.11v-1.8h1.11zM21 11.36v5.5a3 3 0 0 1-3 3h-3.86v-4.5H12.5v4.5H8.64v-4.5h-1.78a3 3 0 0 1-3-3v-5.5a3 3 0 0 1 3-3h11.14a3 3 0 0 1 3 3z"/>
                    </svg>
                  )}
                  {parsedInput.platform === "google_meet" ? "Google Meet" : parsedInput.platform === "zoom" ? "Zoom" : "Microsoft Teams"}
                </span>
                <span className="font-mono text-xs bg-muted px-2 py-1 rounded-md truncate max-w-[200px]">
                  {parsedInput.meetingId}
                </span>
              </div>
            )}
          </div>

          {/* Wake Word Toggle */}
          <div className="space-y-1.5 rounded-lg border bg-muted/30 p-3">
            <div className="flex items-center justify-between gap-4">
              <Label htmlFor="modalWakeWordEnabled" className="text-sm flex items-center gap-2 cursor-pointer">
                <Mic className="h-3.5 w-3.5" />
                {copy.wakeWordLabel}
              </Label>
              <Switch
                id="modalWakeWordEnabled"
                checked={wakeWordEnabled}
                onCheckedChange={setWakeWordEnabled}
              />
            </div>
            <p className="text-xs text-muted-foreground">
              {wakeWordEnabled ? copy.wakeWordEnabledHelp : copy.wakeWordDisabledHelp}
            </p>
          </div>

          {/* Video Recording Toggle */}
          <div className="space-y-1.5 rounded-lg border bg-muted/30 p-3">
            <div className="flex items-center justify-between gap-4">
              <Label htmlFor="modalVideoRecordingEnabled" className="text-sm flex items-center gap-2 cursor-pointer">
                <Monitor className="h-3.5 w-3.5" />
                {recordingCopy.label}
              </Label>
              <Switch
                id="modalVideoRecordingEnabled"
                checked={videoRecordingEnabled}
                onCheckedChange={setVideoRecordingEnabled}
              />
            </div>
            <p className="text-xs text-muted-foreground">
              {videoRecordingEnabled ? recordingCopy.enabledHelp : recordingCopy.disabledHelp}
            </p>
          </div>

          {/* Authenticated Toggle — coming soon */}
          <div className="space-y-1.5 opacity-50">
            <div className="flex items-center justify-between gap-4">
              <Label htmlFor="authenticated" className="text-sm flex items-center gap-2 cursor-not-allowed">
                <UserCheck className="h-3.5 w-3.5" />
                {copy.authenticated}
                <span className="text-[10px] font-medium bg-muted px-1.5 py-0.5 rounded">{copy.soon}</span>
                <span title={copy.authenticatedHelp}>
                  <Info className="h-3 w-3" />
                </span>
              </Label>
              <Switch
                id="authenticated"
                checked={false}
                disabled
              />
            </div>
            <p className="text-xs text-muted-foreground">
              {copy.authenticatedHelp}
            </p>
          </div>

          {/* Passcode for Teams and Zoom */}
          {(platform === "teams" || platform === "zoom") && (
            <div className="space-y-2">
              <Label htmlFor="passcode" className="text-sm">
                {platform === "teams" ? copy.passcodeLabelTeams : copy.passcodeLabelZoom}
              </Label>
              <Input
                id="passcode"
                placeholder={copy.passcodePlaceholder}
                value={passcode}
                onChange={(e) => setPasscode(e.target.value)}
                className="h-10"
              />
            </div>
          )}

          {/* Submit Button */}
          <div className="flex items-center gap-2">
            <Button
              type="submit"
              className={cn(
                "flex-1 h-12 text-base transition-all duration-300",
                isValid && !isSubmitting && "shadow-lg shadow-primary/25"
              )}
              disabled={isSubmitting || !isValid}
            >
              {isSubmitting ? (
                <>
                  <Loader2 className="mr-2 h-5 w-5 animate-spin" />
                  {copy.connecting}
                </>
              ) : (
                <>
                  <Sparkles className="mr-2 h-5 w-5" />
                  {videoRecordingEnabled ? recordingCopy.startWithRecording : copy.startTranscription}
                </>
              )}
            </Button>
          </div>
        </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
