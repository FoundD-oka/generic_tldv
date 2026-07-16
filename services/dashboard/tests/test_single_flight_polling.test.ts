import { afterEach, describe, expect, it, vi } from "vitest";

import { startSingleFlightPolling } from "@/lib/single-flight-polling";

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
});
