"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2, CheckCircle2, XCircle } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Logo } from "@/components/ui/logo";
import { toast } from "sonner";
import { vexaAPI } from "@/lib/api";
import { consumePendingZoomBotRequest } from "@/lib/zoom-oauth-client";
import { useLiveStore } from "@/stores/live-store";
import { useMeetingsStore } from "@/stores/meetings-store";
import { getUserFriendlyError } from "@/lib/error-messages";
import { withBasePath } from "@/lib/base-path";
import { applyBotCreationDefaults, withPostMeetingAutoStop } from "@/lib/bot-create-defaults";

type CallbackState = "loading" | "starting_meeting" | "success" | "error";

function ZoomCallbackContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { setActiveMeeting } = useLiveStore();
  const { setCurrentMeeting } = useMeetingsStore();

  const [state, setState] = useState<CallbackState>("loading");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;

    async function run() {
      const code = searchParams.get("code");
      const stateParam = searchParams.get("state");
      const oauthError = searchParams.get("error");

      if (oauthError) {
        if (!mounted) return;
        setState("error");
        const msg =
          oauthError === "access_denied"
            ? "Zoom認証がキャンセルまたは拒否されました。"
            : `Zoom認証が完了しませんでした: ${oauthError}。「Application not found」が表示された場合は、カボスのZoomアプリを利用できるZoomアカウントでログインしてから再試行してください。`;
        setError(msg);
        return;
      }

      if (!code || !stateParam) {
        if (!mounted) return;
        setState("error");
        setError("OAuthコールバックに必要なパラメータが不足しています");
        return;
      }

      const completeResp = await fetch(withBasePath("/api/zoom/oauth/complete"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code, state: stateParam }),
      });

      const completeData = await completeResp.json();
      if (!completeResp.ok) {
        if (!mounted) return;
        setState("error");
        setError(completeData?.error || "Zoom OAuthの完了に失敗しました");
        return;
      }

      const pendingRequest = consumePendingZoomBotRequest();
      if (pendingRequest) {
        if (!mounted) return;
        setState("starting_meeting");
        try {
          const meeting = await vexaAPI.createBot(applyBotCreationDefaults(withPostMeetingAutoStop(pendingRequest)));
          if (!mounted) return;
          setActiveMeeting(meeting);
          setCurrentMeeting(meeting);
          toast.success("Zoomに接続しました", {
            description: "カボスがZoom会議へ参加しています。",
          });
          router.replace(`/meetings/${meeting.id}`);
          return;
        } catch (err) {
          if (!mounted) return;
          const friendly = getUserFriendlyError(err as Error);
          setState("error");
          setError(`${friendly.title}: ${friendly.description}`);
          return;
        }
      }

      if (!mounted) return;
      setState("success");
      setTimeout(() => {
        router.replace(completeData?.returnTo || "/meetings");
      }, 900);
    }

    run().catch((err) => {
      if (!mounted) return;
      setState("error");
      setError((err as Error).message || "Zoomコールバック中に予期しないエラーが発生しました");
    });

    return () => {
      mounted = false;
    };
  }, [router, searchParams, setActiveMeeting, setCurrentMeeting]);

  return (
    <Card className="border-0 shadow-xl">
      <CardHeader className="text-center">
        {state === "loading" && (
          <>
            <CardTitle className="text-xl">Zoomへ接続しています...</CardTitle>
            <CardDescription>Zoom認証を完了しています</CardDescription>
          </>
        )}

        {state === "starting_meeting" && (
          <>
            <CardTitle className="text-xl">会議ボットを起動しています...</CardTitle>
            <CardDescription>Zoomアカウントに接続しました。カボスを会議へ送っています。</CardDescription>
          </>
        )}

        {state === "success" && (
          <>
            <div className="flex justify-center mb-4">
              <div className="h-16 w-16 rounded-full bg-green-100 dark:bg-green-900/30 flex items-center justify-center">
                <CheckCircle2 className="h-8 w-8 text-green-600 dark:text-green-400" />
              </div>
            </div>
            <CardTitle className="text-xl text-green-600 dark:text-green-400">Zoomに接続しました</CardTitle>
            <CardDescription>移動しています...</CardDescription>
          </>
        )}

        {state === "error" && (
          <>
            <div className="flex justify-center mb-4">
              <div className="h-16 w-16 rounded-full bg-destructive/10 flex items-center justify-center">
                <XCircle className="h-8 w-8 text-destructive" />
              </div>
            </div>
            <CardTitle className="text-xl text-destructive">Zoom接続に失敗しました</CardTitle>
            <CardDescription>{error || "不明なエラー"}</CardDescription>
          </>
        )}
      </CardHeader>
      <CardContent className="flex flex-col items-center gap-4">
        {(state === "loading" || state === "starting_meeting") && (
          <Loader2 className="h-10 w-10 animate-spin text-primary" />
        )}
        {state === "error" && (
          <Button onClick={() => router.replace("/meetings")} className="w-full">
            会議一覧へ戻る
          </Button>
        )}
      </CardContent>
    </Card>
  );
}

function ZoomCallbackLoading() {
  return (
    <Card className="border-0 shadow-xl">
      <CardHeader className="text-center">
        <CardTitle className="text-xl">読み込み中...</CardTitle>
        <CardDescription>しばらくお待ちください</CardDescription>
      </CardHeader>
      <CardContent className="flex justify-center">
        <Loader2 className="h-10 w-10 animate-spin text-primary" />
      </CardContent>
    </Card>
  );
}

export default function ZoomCallbackPage() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-background to-muted/30 p-4">
      <div className="w-full max-w-md">
        <div className="flex flex-col items-center justify-center gap-2 mb-8">
          <Logo size="lg" showText />
          <p className="text-sm text-muted-foreground">会議文字起こし</p>
        </div>
        <Suspense fallback={<ZoomCallbackLoading />}>
          <ZoomCallbackContent />
        </Suspense>
      </div>
    </div>
  );
}
