import { afterEach, describe, expect, it, vi } from "vitest";

import { vexaAPI } from "@/lib/api";
import { startSingleFlightPolling } from "@/lib/single-flight-polling";
import { startRetranscriptionStatusPolling } from "@/lib/retranscription-status";
import { useMeetingsStore } from "@/stores/meetings-store";
import type { Meeting } from "@/types/vexa";

describe("startSingleFlightPolling", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("prevents overlapping requests and stops future polling on cleanup", async () => {
    vi.useFakeTimers();
    const resolvers: Array<() => void> = [];
    const task = vi.fn(() => new Promise<void>((resolve) => resolvers.push(resolve)));

    const stop = startSingleFlightPolling(task, 2500);
    expect(task).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(7500);
    expect(task).toHaveBeenCalledTimes(1);

    resolvers.shift()?.();
    await Promise.resolve();
    await vi.advanceTimersByTimeAsync(2500);
    expect(task).toHaveBeenCalledTimes(2);

    stop();
    resolvers.shift()?.();
    await vi.advanceTimersByTimeAsync(5000);
    expect(task).toHaveBeenCalledTimes(2);
  });

  it("drives the real detail store from queued through running to succeeded and stops", async () => {
    vi.useFakeTimers();
    const meeting = (status: string): Meeting => ({
      id: "1",
      platform: "google_meet",
      platform_specific_id: "abc-defg-hij",
      status: "completed",
      start_time: null,
      end_time: null,
      bot_container_id: null,
      data: { final_transcription: { status } },
      created_at: "2026-07-16T00:00:00Z",
      updated_at: "2026-07-16T00:00:00Z",
    });
    const getMeeting = vi.spyOn(vexaAPI, "getMeeting")
      .mockResolvedValueOnce(meeting("running"))
      .mockResolvedValueOnce(meeting("succeeded"));
    useMeetingsStore.setState({ currentMeeting: meeting("queued"), meetings: [] });

    startRetranscriptionStatusPolling(
      () => useMeetingsStore.getState().refreshMeeting("1"),
      2500
    );
    await vi.advanceTimersByTimeAsync(0);
    expect(useMeetingsStore.getState().currentMeeting?.data.final_transcription?.status).toBe("running");

    await vi.advanceTimersByTimeAsync(2500);
    expect(useMeetingsStore.getState().currentMeeting?.data.final_transcription?.status).toBe("succeeded");

    await vi.advanceTimersByTimeAsync(5000);
    expect(getMeeting).toHaveBeenCalledTimes(2);
  });
});
