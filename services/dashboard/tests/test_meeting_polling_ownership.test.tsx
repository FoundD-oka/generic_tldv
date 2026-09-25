// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useMeetingLiveData } from "@/hooks/use-meeting-live-data";
import { vexaAPI } from "@/lib/api";
import {
  type UseMeetingPollingOptions,
  useMeetingPolling,
} from "@/hooks/use-meeting-polling";
import { useMeetingsStore } from "@/stores/meetings-store";
import type { Meeting } from "@/types/vexa";

vi.mock("@/hooks/use-live-transcripts", () => ({
  useLiveTranscripts: () => ({}),
}));

type Deferred<T = void> = {
  promise: Promise<T>;
  resolve: (value: T | PromiseLike<T>) => void;
  reject: (reason?: unknown) => void;
};

function deferred<T = void>(): Deferred<T> {
  let resolve!: Deferred<T>["resolve"];
  let reject!: Deferred<T>["reject"];
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function PollingHarness(props: UseMeetingPollingOptions) {
  useMeetingPolling(props);
  return null;
}

const baseMeeting = (status: Meeting["status"]): Meeting => ({
  id: "1",
  platform: "google_meet",
  platform_specific_id: "abc-defg-hij",
  status,
  start_time: null,
  end_time: null,
  bot_container_id: null,
  data: { recording_enabled: true },
  created_at: "2026-09-05T00:00:00Z",
  updated_at: "2026-09-05T00:00:00Z",
});

describe("meeting polling ownership", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.useFakeTimers();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("R06 artifact polling has one owner", async () => {
    const refreshes = [deferred(), deferred()];
    const transcripts = [deferred(), deferred()];
    const chats = [deferred(), deferred()];
    const refreshMeeting = vi.fn(() => refreshes[refreshMeeting.mock.calls.length - 1].promise);
    const fetchTranscripts = vi.fn(() => transcripts[fetchTranscripts.mock.calls.length - 1].promise);
    const fetchChatMessages = vi.fn(() => chats[fetchChatMessages.mock.calls.length - 1].promise);

    await act(async () => {
      root.render(<PollingHarness
        meetingId="1"
        meetingPlatform="google_meet"
        meetingNativeId="abc-defg-hij"
        meetingNumericId="1"
        shouldPollMeetingStatus
        shouldPollPostMeetingArtifacts
        refreshMeeting={refreshMeeting}
        fetchTranscripts={fetchTranscripts}
        fetchChatMessages={fetchChatMessages}
      />);
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(refreshMeeting).toHaveBeenCalledTimes(1);
    expect(fetchTranscripts).toHaveBeenCalledTimes(1);
    expect(fetchChatMessages).toHaveBeenCalledTimes(1);

    await act(async () => vi.advanceTimersByTimeAsync(7500));
    expect(refreshMeeting).toHaveBeenCalledTimes(1);
    refreshes[0].resolve();
    transcripts[0].resolve();
    await act(async () => vi.advanceTimersByTimeAsync(2500));
    expect(refreshMeeting).toHaveBeenCalledTimes(1);

    chats[0].resolve();
    await act(async () => {
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(2500);
    });
    expect(refreshMeeting).toHaveBeenCalledTimes(2);
    expect(fetchTranscripts).toHaveBeenCalledTimes(2);
    expect(fetchChatMessages).toHaveBeenCalledTimes(2);
    refreshes[1].resolve();
    transcripts[1].resolve();
    chats[1].resolve();
  });

  it("R06 switching mode does not overlap same meeting", async () => {
    const statusFlight = deferred();
    const refreshMeeting = vi.fn()
      .mockImplementationOnce(() => statusFlight.promise)
      .mockResolvedValue(undefined);
    const fetchTranscripts = vi.fn().mockResolvedValue(undefined);
    const fetchChatMessages = vi.fn().mockResolvedValue(undefined);
    const common = {
      meetingId: "1",
      meetingPlatform: "google_meet" as const,
      meetingNativeId: "abc-defg-hij",
      meetingNumericId: "1",
      refreshMeeting,
      fetchTranscripts,
      fetchChatMessages,
    };

    await act(async () => {
      root.render(<PollingHarness {...common} shouldPollMeetingStatus shouldPollPostMeetingArtifacts={false} />);
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(refreshMeeting).toHaveBeenCalledTimes(1);

    await act(async () => {
      root.render(<PollingHarness {...common} shouldPollMeetingStatus shouldPollPostMeetingArtifacts />);
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(refreshMeeting).toHaveBeenCalledTimes(1);
    expect(fetchTranscripts).not.toHaveBeenCalled();

    statusFlight.resolve();
    await act(async () => {
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(2500);
    });
    expect(refreshMeeting).toHaveBeenCalledTimes(2);
    expect(fetchTranscripts).toHaveBeenCalledTimes(1);
    expect(fetchChatMessages).toHaveBeenCalledTimes(1);
  });

  it("R06 bootstrap is not duplicated by polling", async () => {
    const refreshMeeting = vi.fn().mockResolvedValue(null);
    const fetchTranscripts = vi.fn().mockResolvedValue(undefined);
    const fetchChatMessages = vi.fn().mockResolvedValue(undefined);
    const fetchMeeting = vi.fn().mockResolvedValue(undefined);
    const common = {
      meetingId: "1",
      transcripts: [],
      forcePostMeetingMode: false,
      audioResolutionError: null,
      hasLoadedRef: { current: false },
      handleStatusChange: vi.fn(),
      setForcePostMeetingMode: vi.fn(),
      setCurrentLanguage: vi.fn(),
      fetchMeeting,
      refreshMeeting,
      clearCurrentMeeting: vi.fn(),
      fetchTranscripts,
      fetchChatMessages,
    };
    const LiveHarness = ({ meeting, hasRecordingAudio = false }: { meeting: Meeting; hasRecordingAudio?: boolean }) => {
      useMeetingLiveData({ ...common, currentMeeting: meeting, hasRecordingAudio });
      return null;
    };

    await act(async () => {
      root.render(<LiveHarness meeting={baseMeeting("active")} />);
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(fetchTranscripts).toHaveBeenCalledTimes(1);
    expect(fetchChatMessages).toHaveBeenCalledTimes(1);

    fetchTranscripts.mockClear();
    fetchChatMessages.mockClear();
    await act(async () => {
      root.render(<LiveHarness meeting={baseMeeting("stopping")} />);
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(fetchTranscripts).toHaveBeenCalledTimes(1);
    expect(fetchChatMessages).toHaveBeenCalledTimes(1);

    fetchTranscripts.mockClear();
    fetchChatMessages.mockClear();
    await act(async () => {
      root.render(<LiveHarness meeting={baseMeeting("completed")} hasRecordingAudio />);
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(fetchTranscripts).toHaveBeenCalledTimes(1);
    expect(fetchChatMessages).toHaveBeenCalledTimes(1);
  });

  it("R06 cleanup and rejected tasks leave no unhandled work", async () => {
    const unhandled = vi.fn();
    process.on("unhandledRejection", unhandled);
    const refreshMeeting = vi.fn(() => { throw new Error("sync refresh failure"); });
    const fetchTranscripts = vi.fn().mockRejectedValue(new Error("transcript failure"));
    const fetchChatMessages = vi.fn().mockRejectedValue(new Error("chat failure"));

    await act(async () => {
      root.render(<PollingHarness
        meetingId="1"
        meetingPlatform="google_meet"
        meetingNativeId="abc-defg-hij"
        meetingNumericId="1"
        shouldPollMeetingStatus
        shouldPollPostMeetingArtifacts
        refreshMeeting={refreshMeeting}
        fetchTranscripts={fetchTranscripts}
        fetchChatMessages={fetchChatMessages}
      />);
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(refreshMeeting).toHaveBeenCalledTimes(1);
    expect(fetchTranscripts).toHaveBeenCalledTimes(1);
    expect(fetchChatMessages).toHaveBeenCalledTimes(1);

    await act(async () => root.unmount());
    await vi.advanceTimersByTimeAsync(10000);
    expect(refreshMeeting).toHaveBeenCalledTimes(1);
    expect(unhandled).not.toHaveBeenCalled();
    process.off("unhandledRejection", unhandled);
    root = createRoot(container);

    const oldResponse = deferred<Meeting>();
    const oldMeeting = baseMeeting("active");
    const newMeeting = { ...baseMeeting("active"), id: "2", platform_specific_id: "new-meeting" };
    useMeetingsStore.getState().clearCurrentMeeting();
    useMeetingsStore.setState({ currentMeeting: oldMeeting, meetings: [oldMeeting, newMeeting] });
    vi.spyOn(vexaAPI, "getMeeting").mockImplementation((id) =>
      String(id) === "1" ? oldResponse.promise : Promise.resolve(newMeeting)
    );
    const storeRefresh = (id: string) => useMeetingsStore.getState().refreshMeeting(id);
    const noTranscripts = vi.fn().mockResolvedValue(undefined);
    const noChat = vi.fn().mockResolvedValue(undefined);

    await act(async () => {
      root.render(<PollingHarness
        meetingId="1"
        meetingPlatform="google_meet"
        meetingNativeId="abc-defg-hij"
        meetingNumericId="1"
        shouldPollMeetingStatus
        shouldPollPostMeetingArtifacts={false}
        refreshMeeting={storeRefresh}
        fetchTranscripts={noTranscripts}
        fetchChatMessages={noChat}
      />);
      await vi.advanceTimersByTimeAsync(0);
    });
    useMeetingsStore.getState().clearCurrentMeeting();
    useMeetingsStore.setState({ currentMeeting: newMeeting });
    await act(async () => {
      root.render(<PollingHarness
        meetingId="2"
        meetingPlatform="google_meet"
        meetingNativeId="new-meeting"
        meetingNumericId="2"
        shouldPollMeetingStatus
        shouldPollPostMeetingArtifacts={false}
        refreshMeeting={storeRefresh}
        fetchTranscripts={noTranscripts}
        fetchChatMessages={noChat}
      />);
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(useMeetingsStore.getState().currentMeeting?.id).toBe("2");

    oldResponse.resolve({ ...oldMeeting, status: "completed", updated_at: "late" });
    await act(async () => { await Promise.resolve(); });
    expect(useMeetingsStore.getState().currentMeeting?.id).toBe("2");
  });
});
