export function startSingleFlightPolling<T>(
  task: () => Promise<T>,
  intervalMs = 2500,
  shouldContinue: (result: T) => boolean = () => true
): () => void {
  let stopped = false;
  let inFlight = false;
  let interval: ReturnType<typeof globalThis.setInterval> | null = null;

  const stop = () => {
    stopped = true;
    if (interval !== null) globalThis.clearInterval(interval);
  };

  const run = async () => {
    if (stopped || inFlight) return;
    inFlight = true;
    try {
      const result = await task();
      if (!shouldContinue(result)) stop();
    } finally {
      inFlight = false;
    }
  };

  void run();
  interval = globalThis.setInterval(() => void run(), intervalMs);
  return stop;
}
