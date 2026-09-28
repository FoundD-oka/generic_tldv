import type { TranscriptionSegment } from './segment-publisher';
import type { DraftSegment, TranscriptionHandleResult } from './speaker-streams';
import type { SessionClosePublisher } from './transcription-session-close';

/**
 * Delivery order for live transcript updates of one session.
 *
 * SpeakerStreamManager settles a recognition synchronously in
 * handleTranscriptionResult(), but the onSegmentReady callback that publishes
 * the result keeps running afterwards. SegmentPublisher.publishTranscript()
 * awaits several Redis commands (confirmed XADDs, pending SET/DEL, PUBLISH),
 * so two concurrent updates interleave: a slow older update could write its
 * pending snapshot after a newer update cleared it, reviving stale drafts.
 *
 * OrderedTranscriptPublisher runs every publish operation to completion in
 * the order it was requested. publishTranscriptUpdate() takes the snapshot and
 * requests the publish synchronously, so calling it right after
 * handleTranscriptionResult() (no await in between) makes delivery follow the
 * settle order. After publishSessionEnd() is requested, later transcript
 * publishes are dropped and reported instead of being written after
 * session_end / close.
 *
 * Shutdown is bounded by time, not by the number of queued updates:
 *  - drain() (called before the final flush / by publishSessionEnd) starts ONE
 *    delivery budget (endDrainTimeoutMs) for all transcript updates that are
 *    still queued or requested until it expires. Normal delivery stays in
 *    order and complete as long as it fits the budget.
 *  - When the budget expires, the transcript update in progress is abandoned
 *    (its Redis I/O already started and cannot be cancelled; its outcome is
 *    unknown, it is not counted as delivered), every queued update that has
 *    not started is dropped, and later transcript updates are not started.
 *    All of it is logged and counted (see the getters).
 *  - session_end and close are each bounded by endOpTimeoutMs, so a stuck
 *    Redis connection cannot hold the shutdown either.
 *  Worst case: endDrainTimeoutMs + 2 × endOpTimeoutMs, independent of how
 *  many updates were queued.
 */

export interface OrderedTranscriptPublisherOptions {
  log: (msg: string) => void;
  /**
   * Shutdown only: total time budget, starting at the first drain() /
   * publishSessionEnd() / close(), for delivering the transcript updates still
   * queued or requested within it. Default 5000 ms. Normal operation is never
   * time-bounded.
   */
  endDrainTimeoutMs?: number;
  /** Shutdown only: bound for publishSessionEnd and close, each. Default 5000 ms. */
  endOpTimeoutMs?: number;
}

type JobKind = 'transcript' | 'terminal';
interface Job { label: string; kind: JobKind; op: () => Promise<void>; resolve: () => void }
interface Running { job: Job; timer?: ReturnType<typeof setTimeout> }

export class OrderedTranscriptPublisher implements SessionClosePublisher {
  private readonly queue: Job[] = [];
  private running: Running | null = null;
  private drainWaiters: ((ok: boolean) => void)[] = [];
  private shutdownStarted = false;
  private budgetExpired = false;
  private budgetTimer: ReturnType<typeof setTimeout> | null = null;
  private ended = false;
  private endRequest: Promise<void> | null = null;
  private droppedAfterEnd = 0;
  private abandoned = 0;
  private notStarted = 0;
  private readonly log: (msg: string) => void;
  private readonly endDrainTimeoutMs: number;
  private readonly endOpTimeoutMs: number;

  constructor(private readonly inner: SessionClosePublisher, opts: OrderedTranscriptPublisherOptions) {
    this.log = opts.log;
    this.endDrainTimeoutMs = opts.endDrainTimeoutMs ?? 5000;
    this.endOpTimeoutMs = opts.endOpTimeoutMs ?? 5000;
  }

  /** Number of transcript publishes requested after session_end (not delivered). */
  get droppedAfterSessionEnd(): number { return this.droppedAfterEnd; }
  /**
   * Operations abandoned during shutdown while their I/O was in progress
   * (outcome unknown, not counted as delivered).
   */
  get stalledDuringShutdown(): number { return this.abandoned; }
  /** Transcript updates never started because the shutdown budget expired. */
  get notStartedAtShutdown(): number { return this.notStarted; }

  private enqueue(label: string, kind: JobKind, op: () => Promise<void>): Promise<void> {
    return new Promise<void>(resolve => {
      this.queue.push({ label, kind, op, resolve });
      this.pump();
    });
  }

  private pump(): void {
    if (this.running) return;
    let job = this.queue.shift();
    while (job && job.kind === 'transcript' && this.budgetExpired) {
      // Only reachable for jobs queued after expiry by internal paths; public
      // publishTranscript() already refuses them. Never start them.
      this.notStarted++;
      job.resolve();
      job = this.queue.shift();
    }
    if (!job) {
      const waiters = this.drainWaiters;
      this.drainWaiters = [];
      const ok = this.deliveredAll();
      waiters.forEach(w => w(ok));
      return;
    }
    const r: Running = { job };
    this.running = r;
    if (job.kind === 'terminal' && this.endOpTimeoutMs >= 0) {
      r.timer = setTimeout(() => {
        if (this.running !== r) return;
        this.abandoned++;
        this.log(`[TranscriptOrder] WARNING: ${job.label} did not finish within ${this.endOpTimeoutMs}ms during shutdown — continuing without it (outcome unknown)`);
        this.release(r);
      }, this.endOpTimeoutMs);
    }
    Promise.resolve()
      .then(job.op)
      .catch((err: any) => { this.log(`[TranscriptOrder] ${job.label} failed: ${err?.message ?? err}`); })
      .then(() => {
        if (r.timer) clearTimeout(r.timer);
        if (this.running !== r) {
          this.log(`[TranscriptOrder] ${job.label} finished after it was abandoned at shutdown (still counted as not delivered)`);
          return;
        }
        this.release(r);
      });
  }

  private release(r: Running): void {
    if (r.timer) clearTimeout(r.timer);
    if (this.running === r) this.running = null;
    r.job.resolve();
    this.pump();
  }

  private deliveredAll(): boolean { return this.notStarted === 0 && this.abandoned === 0; }

  /** Start the shutdown delivery budget once. */
  private startShutdown(): void {
    if (this.shutdownStarted) return;
    this.shutdownStarted = true;
    this.budgetTimer = setTimeout(() => this.expireBudget(), this.endDrainTimeoutMs);
  }

  private expireBudget(): void {
    this.budgetTimer = null;
    if (this.budgetExpired) return;
    const runningTranscript = this.running && this.running.job.kind === 'transcript' ? this.running : null;
    const queuedTranscripts = this.queue.filter(j => j.kind === 'transcript');
    if (!runningTranscript && queuedTranscripts.length === 0) {
      // Everything was delivered in time; later updates are still refused below.
      this.budgetExpired = true;
      return;
    }
    this.budgetExpired = true;
    for (const j of queuedTranscripts) this.queue.splice(this.queue.indexOf(j), 1);
    this.notStarted += queuedTranscripts.length;
    if (runningTranscript) this.abandoned++;
    this.log(`[TranscriptOrder] WARNING: shutdown delivery budget of ${this.endDrainTimeoutMs}ms expired — `
      + `${queuedTranscripts.length} queued transcript update(s) not started (not delivered)`
      + (runningTranscript ? `, ${runningTranscript.job.label} abandoned in progress (outcome unknown)` : '')
      + ` | totals: notStarted=${this.notStarted} abandoned=${this.abandoned}`);
    queuedTranscripts.forEach(j => j.resolve());
    if (runningTranscript) this.release(runningTranscript);
    else this.pump();
  }

  publishTranscript(speaker: string, confirmed: TranscriptionSegment[], pending: TranscriptionSegment[]): Promise<void> {
    if (this.ended) {
      this.droppedAfterEnd++;
      this.log(`[TranscriptOrder] WARNING: transcript for ${speaker} requested after session_end — dropped (${confirmed.length}C ${pending.length}P, ${this.droppedAfterEnd} dropped so far)`);
      return Promise.resolve();
    }
    if (this.budgetExpired) {
      this.notStarted++;
      this.log(`[TranscriptOrder] WARNING: transcript for ${speaker} requested after the shutdown delivery budget expired — not started (${confirmed.length}C ${pending.length}P, notStarted=${this.notStarted})`);
      return Promise.resolve();
    }
    return this.enqueue(`publishTranscript(${speaker})`, 'transcript', () => this.inner.publishTranscript(speaker, confirmed, pending));
  }

  /**
   * Shutdown: wait until every publish requested so far (and any requested
   * while waiting) has been delivered, in order, within the shutdown budget.
   * Resolves true if everything was delivered, false if the budget expired
   * (updates were abandoned or not started; see the log and the getters).
   */
  drain(): Promise<boolean> {
    this.startShutdown();
    if (!this.running && this.queue.length === 0) return Promise.resolve(this.deliveredAll());
    return new Promise<boolean>(resolve => { this.drainWaiters.push(resolve); });
  }

  publishSessionEnd(): Promise<void> {
    if (this.endRequest) return this.endRequest;
    this.ended = true;
    this.endRequest = this.drain().then(() => this.enqueue('publishSessionEnd', 'terminal', () => this.inner.publishSessionEnd()));
    return this.endRequest;
  }

  close(): Promise<void> {
    this.ended = true;
    this.startShutdown();
    return (this.endRequest ?? this.drain().then(() => undefined))
      .then(() => this.enqueue('close', 'terminal', () => this.inner.close()))
      .then(() => {
        if (this.budgetTimer) { clearTimeout(this.budgetTimer); this.budgetTimer = null; }
      });
  }
}

export interface TranscriptUpdateContext {
  publisher: Pick<SessionClosePublisher, 'publishTranscript'>;
  sessionStartMs: number;
  /** Returns and clears the confirmed segments collected for this speaker. */
  takeConfirmed: (speakerId: string) => TranscriptionSegment[];
  /** Confirmed segments collected for this speaker (not cleared). */
  peekConfirmedCount: (speakerId: string) => number;
  identity: (speakerId: string, speakerName: string) => Partial<TranscriptionSegment>;
  onDraft?: (speakerName: string, lang: string, pending: TranscriptionSegment[]) => void;
  /** Called with the pending snapshot of every update that was requested. */
  onPublished?: (speakerId: string, pending: TranscriptionSegment[]) => void;
  log?: (msg: string) => void;
}

/**
 * Publish after a transcription result: confirmed segments collected by
 * onSegmentConfirmed + the manager's unconfirmed draft (with audio-based times).
 *
 * The snapshot and the publish request happen synchronously; only the returned
 * promise waits for delivery. Call it directly after handleTranscriptionResult().
 */
export function publishTranscriptUpdate(
  ctx: TranscriptUpdateContext,
  speakerId: string,
  speakerName: string,
  handled: TranscriptionHandleResult,
  lang: string,
  force: boolean,
): Promise<void> {
  if (handled.status !== 'applied') return Promise.resolve();
  if (!force && ctx.peekConfirmedCount(speakerId) === 0) return Promise.resolve();
  const speakerConfirmed = ctx.takeConfirmed(speakerId);
  const segmentIdentity = ctx.identity(speakerId, speakerName);
  const pending: TranscriptionSegment[] = handled.pending
    .map(d => ({
      speaker: speakerName,
      text: d.text.trim(),
      start: (d.startMs - ctx.sessionStartMs) / 1000,
      end: (d.endMs - ctx.sessionStartMs) / 1000,
      language: lang, completed: false,
      ...segmentIdentity,
      absolute_start_time: new Date(d.startMs).toISOString(),
      absolute_end_time: new Date(d.endMs).toISOString(),
    }))
    .filter(seg => seg.text);
  if (pending.length > 0) ctx.onDraft?.(speakerName, lang, pending);
  ctx.onPublished?.(speakerId, pending);
  ctx.log?.(`[📡 PUBLISH] ${speakerName} | ${speakerConfirmed.length}C ${pending.length}P`);
  return ctx.publisher.publishTranscript(speakerName, speakerConfirmed, pending);
}

export interface LiveTranscriptDeliveryOptions extends Omit<TranscriptUpdateContext, 'onPublished'> {
  /** Current unconfirmed draft of the speaker (SpeakerStreamManager.getPendingDraft). */
  pendingDraft: (speakerId: string) => DraftSegment[];
  /** Language for a draft published outside a recognition result. */
  language: (speakerId: string) => string;
  /** Defers the out-of-result publish until the confirming call has returned. Default: queueMicrotask. */
  defer?: (fn: () => void) => void;
}

/**
 * Live transcript delivery for one session, as wired by index.ts.
 *
 *  - afterResult(): publish right after handleTranscriptionResult()
 *    (see publishTranscriptUpdate).
 *  - afterDiscard(): a successful recognition whose text was discarded
 *    (quality gate / hallucination). Publishes when something was confirmed,
 *    on final requests, and when the manager's draft became empty while a
 *    draft is still shown, so the stale draft is cleared (QA F2).
 *  - confirmedOutsideResult(): called from onSegmentConfirmed. Segments
 *    confirmed without a recognition result in progress (idle / speaker-change
 *    flush committing the cached hypothesis, hard-cap forced commit, final
 *    failure fallback, speaker removal) are published promptly with the draft
 *    as it is after the commit (QA F1). The publish is deferred until the
 *    confirming call returns; a confirmation made inside
 *    handleTranscriptionResult() has been taken by afterResult() by then, so
 *    nothing is sent twice. It goes through the same ordered publisher.
 */
export class LiveTranscriptDelivery {
  private readonly ctx: TranscriptUpdateContext;
  private readonly draftShown = new Set<string>();
  private readonly scheduled = new Set<string>();
  private readonly defer: (fn: () => void) => void;

  constructor(private readonly opts: LiveTranscriptDeliveryOptions) {
    this.defer = opts.defer ?? (fn => queueMicrotask(fn));
    this.ctx = {
      ...opts,
      onPublished: (speakerId, pending) => {
        if (pending.length > 0) this.draftShown.add(speakerId);
        else this.draftShown.delete(speakerId);
      },
    };
  }

  afterResult(speakerId: string, speakerName: string, handled: TranscriptionHandleResult, lang: string, force: boolean): Promise<void> {
    return publishTranscriptUpdate(this.ctx, speakerId, speakerName, handled, lang, force);
  }

  afterDiscard(speakerId: string, speakerName: string, handled: TranscriptionHandleResult, lang: string, isFinal: boolean): Promise<void> {
    const clearsShownDraft = handled.pending.length === 0 && this.draftShown.has(speakerId);
    return publishTranscriptUpdate(this.ctx, speakerId, speakerName, handled, lang, isFinal || clearsShownDraft);
  }

  confirmedOutsideResult(speakerId: string, speakerName: string): void {
    if (this.scheduled.has(speakerId)) return;
    this.scheduled.add(speakerId);
    this.defer(() => {
      this.scheduled.delete(speakerId);
      if (this.opts.peekConfirmedCount(speakerId) === 0) return; // delivered by afterResult
      const handled: TranscriptionHandleResult = { status: 'applied', confirmed: [], pending: this.opts.pendingDraft(speakerId) };
      publishTranscriptUpdate(this.ctx, speakerId, speakerName, handled, this.opts.language(speakerId), false)
        .catch((err: any) => this.opts.log?.(`[📡 PUBLISH] confirmed-only publish for ${speakerName} failed: ${err?.message ?? err}`));
    });
  }
}
