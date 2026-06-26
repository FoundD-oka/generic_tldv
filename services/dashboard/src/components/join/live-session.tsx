"use client";

import { useEffect, useMemo } from "react";
import { StopCircle, Wifi, WifiOff, Loader2, Users, Clock, Mic } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { toast } from "sonner";
import { TranscriptSegment } from "@/components/transcript/transcript-segment";
import { useVexaWebSocket } from "@/hooks/use-vexa-websocket";
import { useLiveStore } from "@/stores/live-store";
import { vexaAPI } from "@/lib/api";
import type { Platform } from "@/types/vexa";
import { PLATFORM_CONFIG, MEETING_STATUS_CONFIG, getSpeakerColor } from "@/types/vexa";
import { cn } from "@/lib/utils";
import { DocsLink } from "@/components/docs/docs-link";

interface LiveSessionProps {
  platform: Platform;
  nativeId: string;
  onEnd?: () => void;
}

export function LiveSession({ platform, nativeId, onEnd }: LiveSessionProps) {
  const {
    activeMeeting,
    liveTranscripts,
    botStatus,
    clearLiveSession,
  } = useLiveStore();

  const { isConnecting, isConnected, error } = useVexaWebSocket({
    platform,
    nativeId,
    autoConnect: true,
    onError: (err) => {
      toast.error("WebSocketエラー", { description: err });
    },
  });

  // Get unique speakers in order of appearance
  const speakerOrder = useMemo(() => {
    const speakers: string[] = [];
    for (const segment of liveTranscripts) {
      if (!speakers.includes(segment.speaker)) {
        speakers.push(segment.speaker);
      }
    }
    return speakers;
  }, [liveTranscripts]);

  const handleStopBot = async () => {
    try {
      await vexaAPI.stopBot(platform, nativeId);
      toast.success("ボットを停止しました", {
        description: "文字起こしボットが会議から退出しました",
      });
      clearLiveSession();
      onEnd?.();
    } catch (error) {
      toast.error("ボットの停止に失敗しました", {
        description: (error as Error).message,
      });
    }
  };

  const platformConfig = PLATFORM_CONFIG[platform];
  const statusConfig = botStatus ? MEETING_STATUS_CONFIG[botStatus] : null;

  // Check if meeting is still active
  const isActive = botStatus === "active" || botStatus === "joining" || botStatus === "awaiting_admission";

  return (
    <div className="space-y-4">
      {/* Status Card */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              {/* Live indicator */}
              {isActive && (
                <span className="relative flex h-3 w-3">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75" />
                  <span className="relative inline-flex rounded-full h-3 w-3 bg-red-500" />
                </span>
              )}
              <CardTitle className="text-lg">
                {isActive ? "ライブセッション" : "セッション終了"}
              </CardTitle>
            </div>

            {isActive && (
              <div className="flex items-center">
                <Button variant="destructive" size="sm" onClick={handleStopBot}>
                  <StopCircle className="h-4 w-4 mr-2" />
                  ボットを停止
                </Button>
                <DocsLink href="/docs/rest/bots#stop-bot" />
              </div>
            )}
          </div>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-center gap-3">
            {/* Platform */}
            <Badge variant="outline" className={cn(platformConfig.bgColor, platformConfig.textColor)}>
              {platformConfig.name}
            </Badge>

            {/* Meeting ID */}
            <Badge variant="secondary" className="font-mono">
              {nativeId}
            </Badge>

            {/* Bot Status */}
            {statusConfig && (
              <Badge className={cn(statusConfig.bgColor, statusConfig.color)}>
                {statusConfig.label}
              </Badge>
            )}

            {/* WebSocket Status */}
            <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
              {isConnecting ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  <span>接続中...</span>
                </>
              ) : isConnected ? (
                <>
                  <Wifi className="h-4 w-4 text-green-500" />
                  <span>接続済み</span>
                </>
              ) : (
                <>
                  <WifiOff className="h-4 w-4 text-red-500" />
                  <span>切断中</span>
                </>
              )}
            </div>

            {/* Participants count */}
            {speakerOrder.length > 0 && (
              <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
                <Users className="h-4 w-4" />
                <span>{speakerOrder.length}名の話者</span>
              </div>
            )}
          </div>

          {error && (
            <p className="mt-3 text-sm text-red-500">{error}</p>
          )}
        </CardContent>
      </Card>

      {/* Live Transcript */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            ライブ文字起こし
            {liveTranscripts.length > 0 && (
              <Badge variant="secondary">{liveTranscripts.length}件</Badge>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <ScrollArea className="h-[400px] pr-4">
            {liveTranscripts.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full py-12 text-center">
                {isActive && activeMeeting?.data?.transcribe_enabled === false ? (
                  <>
                    <div className="relative mb-4">
                      <Mic className="h-8 w-8 text-red-500" />
                      <span className="absolute -top-1 -right-1 h-3 w-3 rounded-full bg-red-500 animate-pulse" />
                    </div>
                    <p className="text-muted-foreground">
                      録音中
                    </p>
                    <p className="text-sm text-muted-foreground mt-1">
                      音声を記録しています。会議終了後に文字起こしできます。
                    </p>
                  </>
                ) : isActive ? (
                  <>
                    <Loader2 className="h-8 w-8 animate-spin text-muted-foreground mb-4" />
                    <p className="text-muted-foreground">
                      発話を待っています...
                    </p>
                    <p className="text-sm text-muted-foreground mt-1">
                      文字起こしはリアルタイムでここに表示されます
                    </p>
                  </>
                ) : (
                  <p className="text-muted-foreground">文字起こしはありません</p>
                )}
              </div>
            ) : (
              <div className="space-y-1">
                {liveTranscripts.map((segment, index) => (
                  <TranscriptSegment
                    key={segment.id || `${segment.absolute_start_time}-${index}`}
                    segment={segment}
                    speakerColor={getSpeakerColor(segment.speaker, speakerOrder)}
                  />
                ))}
              </div>
            )}
          </ScrollArea>
        </CardContent>
      </Card>
    </div>
  );
}
