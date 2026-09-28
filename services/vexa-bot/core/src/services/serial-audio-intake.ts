/**
 * Per-key serialization of audio callbacks with an explicit intake gate.
 *
 * Browser audio callbacks (page.exposeFunction) run concurrently. A handler
 * that awaits anything before feeding (name resolution, VAD, publishing a
 * "joined" event) lets a later chunk of the same speaker overtake it. Tasks
 * enqueued under the same key run strictly in arrival order.
 *
 * close() stops accepting new tasks; tasks already queued still run, and
 * drain() waits for them (bounded), so shutdown sees every accepted chunk.
 */
export class SerialAudioIntake {
  private queues: Map<string, Promise<void>> = new Map();
  private accepting = true;

  constructor(private readonly onError: (key: string, err: unknown) => void = () => {}) {}

  /** Accept new tasks again (a new pipeline session). */
  open(): void {
    this.accepting = true;
  }

  /** Stop accepting new tasks. Already queued tasks keep running. */
  close(): void {
    this.accepting = false;
  }

  get isOpen(): boolean {
    return this.accepting;
  }

  /** Number of keys with queued or running tasks. */
  get size(): number {
    return this.queues.size;
  }

  /**
   * Queue `task` after every earlier task of `key`. Returns a promise that
   * settles when the task finished (errors are reported to onError, not thrown).
   * When intake is closed the task is not run and a resolved promise is returned.
   */
  enqueue(key: string, task: () => void | Promise<void>): Promise<void> {
    if (!this.accepting) return Promise.resolve();
    const prev = this.queues.get(key) ?? Promise.resolve();
    const next = prev
      .then(() => task())
      .catch((err: unknown) => { this.onError(key, err); });
    this.queues.set(key, next);
    void next.then(() => {
      if (this.queues.get(key) === next) this.queues.delete(key);
    });
    return next;
  }

  /**
   * Wait until all queued tasks finished, or `timeoutMs` elapsed.
   * Returns true when fully drained.
   */
  async drain(timeoutMs: number): Promise<boolean> {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let timedOut = false;
    const deadline = new Promise<void>(resolve => {
      timer = setTimeout(() => { timedOut = true; resolve(); }, timeoutMs);
    });
    const all = (async () => {
      while (this.queues.size > 0 && !timedOut) {
        await Promise.all(Array.from(this.queues.values()));
      }
    })();
    await Promise.race([all, deadline]);
    if (timer) clearTimeout(timer);
    return this.queues.size === 0;
  }
}
