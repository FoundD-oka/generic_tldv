export function startSingleFlightPolling(
  task: () => Promise<unknown>,
  intervalMs = 2500
): () => void {
  let stopped = false;
  let inFlight = false;

  const run = async () => {
    if (stopped || inFlight) return;
    inFlight = true;
    try {
      await task();
    } finally {
      inFlight = false;
    }
  };

  void run();
  const interval = globalThis.setInterval(() => void run(), intervalMs);
  return () => {
    stopped = true;
    globalThis.clearInterval(interval);
  };
}
