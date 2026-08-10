import { afterEach, describe, expect, it, vi } from "vitest";

import {
  MEETING_STATUS_POLL_INTERVAL_MS,
  POST_MEETING_ARTIFACT_POLL_INTERVAL_MS,
  startImmediateIntervalPolling,
} from "@/hooks/use-meeting-polling";

describe("meeting detail polling", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("runs immediately and repeats at the 5 second meeting status interval", async () => {
    vi.useFakeTimers();
    const task = vi.fn();
    const stop = startImmediateIntervalPolling(task, MEETING_STATUS_POLL_INTERVAL_MS);

    expect(task).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(4999);
    expect(task).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(task).toHaveBeenCalledTimes(2);
    stop();
  });

  it("keeps the post-meeting artifact interval at 2.5 seconds", async () => {
    vi.useFakeTimers();
    const task = vi.fn();
    const stop = startImmediateIntervalPolling(task, POST_MEETING_ARTIFACT_POLL_INTERVAL_MS);

    await vi.advanceTimersByTimeAsync(7500);
    expect(task).toHaveBeenCalledTimes(4);
    stop();
  });

  it("stops future polling during cleanup", async () => {
    vi.useFakeTimers();
    const task = vi.fn();
    const stop = startImmediateIntervalPolling(task, 2500);

    stop();
    await vi.advanceTimersByTimeAsync(10000);
    expect(task).toHaveBeenCalledTimes(1);
  });

  it("preserves overlapping invocation behavior for unresolved tasks", async () => {
    vi.useFakeTimers();
    const task = vi.fn(() => new Promise<void>(() => undefined));
    const stop = startImmediateIntervalPolling(task, 2500);

    await vi.advanceTimersByTimeAsync(5000);
    expect(task).toHaveBeenCalledTimes(3);
    stop();
  });
});
