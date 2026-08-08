"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2, CheckCircle2, XCircle } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Logo } from "@/components/ui/logo";
import { withBasePath } from "@/lib/base-path";

type CallbackState = "loading" | "success" | "error";

function CalendarCallbackContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

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
        setError(
          oauthError === "access_denied"
            ? "Googleカレンダーの連携がキャンセルまたは拒否されました"
            : `Googleカレンダーの連携に失敗しました: ${oauthError}`
        );
        return;
      }

      if (!code || !stateParam) {
        if (!mounted) return;
        setState("error");
        setError("OAuthコールバックのパラメータが不足しています");
        return;
      }

      const completeResp = await fetch(withBasePath("/api/calendar/oauth/complete"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code, state: stateParam }),
      });

      const completeData = await completeResp.json();
      if (!completeResp.ok) {
        if (!mounted) return;
        setState("error");
        setError(completeData?.error || "Googleカレンダー連携の完了に失敗しました");
        return;
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
      setError((err as Error).message || "コールバック処理中に予期しないエラーが発生しました");
    });

    return () => {
      mounted = false;
    };
  }, [router, searchParams]);

  return (
    <Card className="border-0 shadow-xl">
      <CardHeader className="text-center">
        {state === "loading" && (
          <>
            <CardTitle className="text-xl">Googleカレンダーを接続しています…</CardTitle>
            <CardDescription>認証を完了しています</CardDescription>
          </>
        )}

        {state === "success" && (
          <>
            <div className="flex justify-center mb-4">
              <div className="h-16 w-16 rounded-full bg-green-100 dark:bg-green-900/30 flex items-center justify-center">
                <CheckCircle2 className="h-8 w-8 text-green-600 dark:text-green-400" />
              </div>
            </div>
            <CardTitle className="text-xl text-green-600 dark:text-green-400">カレンダーを接続しました</CardTitle>
            <CardDescription>画面を移動しています…</CardDescription>
          </>
        )}

        {state === "error" && (
          <>
            <div className="flex justify-center mb-4">
              <div className="h-16 w-16 rounded-full bg-destructive/10 flex items-center justify-center">
                <XCircle className="h-8 w-8 text-destructive" />
              </div>
            </div>
            <CardTitle className="text-xl text-destructive">カレンダー接続に失敗しました</CardTitle>
            <CardDescription>{error || "不明なエラー"}</CardDescription>
          </>
        )}
      </CardHeader>
      <CardContent className="flex flex-col items-center gap-4">
        {state === "loading" && (
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

function CalendarCallbackLoading() {
  return (
    <Card className="border-0 shadow-xl">
      <CardHeader className="text-center">
        <CardTitle className="text-xl">読み込み中…</CardTitle>
        <CardDescription>お待ちください</CardDescription>
      </CardHeader>
      <CardContent className="flex justify-center">
        <Loader2 className="h-10 w-10 animate-spin text-primary" />
      </CardContent>
    </Card>
  );
}

export default function GoogleCalendarCallbackPage() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-background to-muted/30 p-4">
      <div className="w-full max-w-md">
        <div className="flex flex-col items-center justify-center gap-2 mb-8">
          <Logo size="lg" showText />
          <p className="text-sm text-muted-foreground">会議文字起こし</p>
        </div>
        <Suspense fallback={<CalendarCallbackLoading />}>
          <CalendarCallbackContent />
        </Suspense>
      </div>
    </div>
  );
}
