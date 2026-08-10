"use client";

import { useRef, useState } from "react";
import { Send, StopCircle, Volume2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { speakInMeeting, stopSpeakingInMeeting } from "@/lib/meeting-detail-api";

export function MeetingDetailSkeleton() {
  return (
    <div className="space-y-6">
      <Skeleton className="h-10 w-40" />
      <div className="space-y-2">
        <Skeleton className="h-8 w-64" />
        <div className="flex gap-2">
          <Skeleton className="h-6 w-24" />
          <Skeleton className="h-6 w-20" />
        </div>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2">
          <Skeleton className="h-[600px]" />
        </div>
        <div className="space-y-6">
          <Skeleton className="h-48" />
          <Skeleton className="h-40" />
        </div>
      </div>
    </div>
  );
}

export function TtsSpeakCard({ platform, nativeId }: { platform: string; nativeId: string }) {
  const [text, setText] = useState("");
  const [isSpeaking, setIsSpeaking] = useState(false);
  const speakTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  async function handleSpeak() {
    if (!text.trim()) return;
    setIsSpeaking(true);
    // Keep stop button visible — estimate ~100ms per character for TTS playback
    const estimatedMs = Math.max(3000, text.trim().length * 100);
    if (speakTimeoutRef.current) clearTimeout(speakTimeoutRef.current);
    speakTimeoutRef.current = setTimeout(() => setIsSpeaking(false), estimatedMs);
    try {
      await speakInMeeting(platform, nativeId, text.trim());
      setText("");
    } catch (error) {
      toast.error("読み上げに失敗しました: " + (error as Error).message);
      setIsSpeaking(false);
      if (speakTimeoutRef.current) clearTimeout(speakTimeoutRef.current);
    }
  }

  async function handleStop() {
    try {
      await stopSpeakingInMeeting(platform, nativeId);
    } catch {}
    setIsSpeaking(false);
    if (speakTimeoutRef.current) clearTimeout(speakTimeoutRef.current);
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm flex items-center gap-2">
          <Volume2 className="h-4 w-4" />
          会議で読み上げ
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex gap-2">
          <Input
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="読み上げる内容を入力..."
            className="text-sm"
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSpeak(); } }}
            disabled={isSpeaking}
          />
          {isSpeaking ? (
            <Button size="sm" variant="destructive" onClick={handleStop}>
              <StopCircle className="h-4 w-4" />
            </Button>
          ) : (
            <Button size="sm" onClick={handleSpeak} disabled={!text.trim()}>
              <Send className="h-4 w-4" />
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
