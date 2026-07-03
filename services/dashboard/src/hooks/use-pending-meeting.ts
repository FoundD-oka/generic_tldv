"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { consumePendingMeetingUrl } from "@/lib/pending-meeting";
import { parseMeetingInput } from "@/lib/parse-meeting-input";
import { vexaAPI } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useLiveStore } from "@/stores/live-store";
import { useMeetingsStore } from "@/stores/meetings-store";
import { getUserFriendlyError } from "@/lib/error-messages";
import { applyBotCreationDefaults, withPostMeetingAutoStop } from "@/lib/bot-create-defaults";
import { useRuntimeConfig } from "@/hooks/use-runtime-config";

export function usePendingMeeting() {
  const router = useRouter();
  const { config } = useRuntimeConfig();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const setActiveMeeting = useLiveStore((s) => s.setActiveMeeting);
  const setCurrentMeeting = useMeetingsStore((s) => s.setCurrentMeeting);
  const processedRef = useRef(false);

  useEffect(() => {
    if (!isAuthenticated || processedRef.current) return;

    const meetingUrl = consumePendingMeetingUrl();
    if (!meetingUrl) return;

    processedRef.current = true;

    const parsed = parseMeetingInput(meetingUrl);
    if (!parsed) {
      toast.error("保存されていた会議URLが無効になりました");
      return;
    }

    const request = applyBotCreationDefaults(
      withPostMeetingAutoStop({
        platform: parsed.platform,
        native_meeting_id: parsed.meetingId,
      }),
      config
    );
    if (parsed.passcode) {
      request.passcode = parsed.passcode;
    }
    if (parsed.originalUrl) {
      request.meeting_url = parsed.originalUrl;
    }

    toast.promise(
      vexaAPI.createBot(request).then((meeting) => {
        setActiveMeeting(meeting);
        setCurrentMeeting(meeting);
        router.push(`/meetings/${meeting.id}?apiView=1`);
        return meeting;
      }),
      {
        loading: "会議へ参加しています...",
        success: "カボスが会議へ接続しています",
        error: (err) => {
          const { title, description } = getUserFriendlyError(err);
          return `${title}${description ? `: ${description}` : ""}`;
        },
      }
    );
  }, [config, isAuthenticated, router, setActiveMeeting, setCurrentMeeting]);
}
