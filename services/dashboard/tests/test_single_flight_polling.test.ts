import { afterEach, describe, expect, it, vi } from "vitest";

import { startSingleFlightPolling } from "@/lib/single-flight-polling";
import { isRetranscriptionInProgress } from "@/lib/retranscription-status";

describe("startSingleFlightPolling", () => {
  afterEach(() => {
    vi.useRealTimers();
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

  it("drives a reloaded detail state from queued through running to succeeded", async () => {
    vi.useFakeTimers();
    let status = "queued";
    const responses = ["running", "succeeded"];
    const task = vi.fn(async () => {
      status = responses.shift() || status;
    });

    const stop = startSingleFlightPolling(task, 2500);
    await Promise.resolve();
    expect(status).toBe("running");
    expect(isRetranscriptionInProgress({ final_transcription: { status } })).toBe(true);

    await vi.advanceTimersByTimeAsync(2500);
    expect(status).toBe("succeeded");
    expect(isRetranscriptionInProgress({ final_transcription: { status } })).toBe(false);

    stop();
    await vi.advanceTimersByTimeAsync(5000);
    expect(task).toHaveBeenCalledTimes(2);
  });
});
