"use client";

import { useState } from "react";
import { JoinForm } from "@/components/join/join-form";
import { LiveSession } from "@/components/join/live-session";
import type { Platform } from "@/types/vexa";

interface ActiveSession {
  meetingId: string;
  platform: Platform;
  nativeId: string;
}

export default function JoinPage() {
  const [activeSession, setActiveSession] = useState<ActiveSession | null>(null);

  const handleJoinSuccess = (meetingId: string, platform: Platform, nativeId: string) => {
    setActiveSession({ meetingId, platform, nativeId });
  };

  const handleSessionEnd = () => {
    setActiveSession(null);
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold tracking-tight">会議に参加</h1>
        <p className="text-muted-foreground">
          文字起こしボットを会議に参加させ、リアルタイムで記録・文字起こしします
        </p>
      </div>

      {/* Content */}
      <div className="max-w-2xl">
        {activeSession ? (
          <LiveSession
            platform={activeSession.platform}
            nativeId={activeSession.nativeId}
            onEnd={handleSessionEnd}
          />
        ) : (
          <JoinForm onSuccess={handleJoinSuccess} />
        )}
      </div>
    </div>
  );
}
