import { log } from '../utils';
import { isHallucination } from './hallucination-filter';

/**
 * Per-speaker streaming transcription buffer (Whisper HTTP path).
 *
 * Audio lifetimes are kept distinct:
 *   - confirmed audio: before `confirmedAbs`; already transcribed and emitted, trimmed from memory
 *   - submitted snapshot: [startSample, endSample) of the one request in flight
 *   - pending audio: everything after `confirmedAbs`, including audio that
 *     arrived while a request was in flight. It is never discarded because a
 *     response arrived — a response only confirms audio inside its own snapshot.
 *
 * All sample positions are absolute per-speaker indices, so a response is only
 * applied to the audio it was produced from (request id + snapshot start).
 *
 * Confirmation is LocalAgreement-n over successive hypotheses that each contain
 * more audio: the common prefix of the last `confirmThreshold` hypotheses is
 * committed at a boundary that exists in all of them. Comparison is done on
 * normalized characters, not whitespace-split words, so Japanese (no spaces)
 * works. Cut points come from backend word timestamps when present; responses
 * without word timestamps fall back to segment boundaries; responses without
 * segments fall back to whole-text agreement.
 *
 * Audio is only cut where the newest hypothesis shows a pause between words
 * (`minCutGapSec`), and the cut is placed inside that pause (`cutPadSec`),
 * never exactly at a word timestamp. Word timestamps are approximate, and
 * Whisper drops a word whose onset is clipped: cutting continuous speech at a
 * word boundary lost the ending of the committed sentence in a real replay
 * (see __tests__/fixtures/cut-repro-technical.json).
 */

export interface WhisperWord {
  word: string;
  start: number;
  end: number;
  probability?: number;
}

export interface WhisperSegment {
  text: string;
  start: number;
  end: number;
  /** Optional backend word timestamps (seconds, relative to submitted audio start). */
  words?: WhisperWord[];
}

export type SubmissionMode = 'partial' | 'forced' | 'final';
export type SubmissionReason = 'interval' | 'hard-cap' | 'idle' | 'flush' | 'close';

/** Identity of one transcription request. Passed to onSegmentReady. */
export interface SubmissionInfo {
  requestId: number;
  mode: SubmissionMode;
  reason: SubmissionReason;
  /** Absolute per-speaker sample index of the first submitted sample */
  startSample: number;
  /** Absolute per-speaker sample index after the last submitted sample */
  endSample: number;
}

/** Unconfirmed tail of the latest hypothesis, with wall-clock times. */
export interface DraftSegment {
  text: string;
  startMs: number;
  endMs: number;
}

export interface ConfirmedSegmentInfo {
  text: string;
  startMs: number;
  endMs: number;
  segmentId: string;
}

export interface TranscriptionHandleResult {
  /** applied: used for confirmation. stale: ignored (superseded / other audio). */
  status: 'applied' | 'stale' | 'unknown-speaker';
  confirmed: ConfirmedSegmentInfo[];
  pending: DraftSegment[];
}

export interface HandleTranscriptionOptions {
  /** Request id from SubmissionInfo. Without it, the result is attributed to the request in flight (legacy callers). */
  requestId?: number;
}

export interface SpeakerStreamStats {
  /** Samples dropped without being transcribed (pending overflow, removal, final failure) */
  lostSamples: number;
  requestFailures: number;
  staleResults: number;
  timedOutRequests: number;
}

export interface FinalizeSummary {
  speakers: number;
  /** Seconds of audio that could not be transcribed before close */
  lostSec: number;
  timedOut: boolean;
  /** onSegmentReady callbacks (e.g. publishes) still running when finalizeAll returned */
  pendingCallbacks: number;
}

type Outcome = 'result' | 'error' | 'timeout' | 'cancelled' | 'stale';

interface InFlightRequest extends SubmissionInfo {
  sentAtMs: number;
  settled: boolean;
  outcome?: Outcome;
  done: Promise<Outcome>;
  resolve: (o: Outcome) => void;
}

interface AudioChunk {
  data: Float32Array;
  /** Absolute sample index of data[0] */
  startSample: number;
  /** Wall-clock time (ms) of data[0] */
  startMs: number;
}

interface Token {
  /** Original text, concatenated to rebuild display text */
  display: string;
  /** Normalized text used for agreement (NFKC, lowercase, no punctuation/whitespace) */
  key: string;
  /** Seconds relative to hypothesis base; undefined when the backend gave no timing */
  start?: number;
  end?: number;
  segIndex: number;
  /** Whether a commit may cut right after this token */
  boundaryAfter: boolean;
  /** Cut after this token ends a Whisper segment or a sentence */
  strongBoundary: boolean;
  /** Token comes from backend word timestamps (not a whole segment) */
  wordLevel: boolean;
}

interface Hypothesis {
  baseSample: number;
  endSample: number;
  /** false for results that did not come from a known request (legacy direct calls) */
  endKnown: boolean;
  tokens: Token[];
  timed: boolean;
}

interface SpeakerBuffer {
  speakerId: string;
  speakerName: string;
  chunks: AudioChunk[];
  /** Samples currently held in memory (all unconfirmed) */
  totalSamples: number;
  /** Absolute sample index of chunks[0] */
  bufferStartAbs: number;
  /** Absolute sample index of the first unconfirmed sample */
  confirmedAbs: number;
  /** Absolute end of the last submitted snapshot (to avoid resubmitting identical audio) */
  lastSubmittedEnd: number;
  history: Hypothesis[];
  inFlight: InFlightRequest | null;
  lastAudioMs: number;
  retryAfterMs: number;
  consecutiveFailures: number;
  finalizeActive: boolean;
  finalizePromise: Promise<void> | null;
  /**
   * Absolute end of the audio the running finalize must cover. A flush/close
   * requested while one is running extends it to the audio present at that
   * call, so the later request is not satisfied by the earlier, shorter one.
   */
  finalizeTarget: number;
  finalizeReason: SubmissionReason;
  sequenceNumber: number;
  lastConfirmedText: string;
  /** Wall-clock end of the last fed chunk (for contiguous timing) */
  lastChunkEndMs: number;
}

export interface SpeakerStreamManagerConfig {
  /** Minimum unconfirmed audio before submission (seconds). Default: 2 */
  minAudioDuration?: number;
  /** Interval between submissions (seconds). Default: 2 */
  submitInterval?: number;
  /** Number of successive hypotheses (each with more audio) that must agree. Default: 2 */
  confirmThreshold?: number;
  /** Max unconfirmed audio per request before a forced commit (seconds). Default: 30 */
  maxBufferDuration?: number;
  /** Idle timeout — finalize pending audio after this many seconds without audio. Default: 15 */
  idleTimeoutSec?: number;
  /** Sample rate. Default: 16000 */
  sampleRate?: number;
  /** Tokens ending this close to the snapshot end are not committed (partial/forced). Default: 0.3 */
  tailGuardSec?: number;
  /** A request without response for this long is expired and may be resubmitted. Default: 150 */
  requestTimeoutSec?: number;
  /** Hard memory bound for pending audio; older audio beyond this is dropped with a log. Default: max(120, 4 × maxBufferDuration) */
  maxPendingDurationSec?: number;
  /** Final (flush/idle/close) attempts before giving up on pending audio. Default: 2 */
  maxFinalAttempts?: number;
  /**
   * Minimum silence between two timed words for audio to be cut there (seconds).
   * Inside continuous speech backend word timestamps touch (gap 0); pauses show
   * up as gaps. Default: 0.15
   */
  minCutGapSec?: number;
  /** A cut is placed this far (at most half the pause) after the last committed word. Default: 0.3 */
  cutPadSec?: number;
  /** Clock (ms). Default: Date.now */
  now?: () => number;
  /** Start a per-speaker setInterval. Disable for tests that drive `tick()` manually. Default: true */
  autoTimers?: boolean;
}

const SENTENCE_END = /[。．.!?！？]\s*$/u;

function normalizeKey(text: string): string {
  return text.normalize('NFKC').toLowerCase().replace(/[\p{P}\p{Z}\s]/gu, '');
}

function isFiniteNumber(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

export class SpeakerStreamManager {
  private buffers: Map<string, SpeakerBuffer> = new Map();
  private timers: Map<string, ReturnType<typeof setInterval>> = new Map();
  private minAudioDuration: number;
  private submitInterval: number;
  private confirmThreshold: number;
  private maxBufferDuration: number;
  private idleTimeoutSec: number;
  private sampleRate: number;
  private tailGuardSec: number;
  private requestTimeoutMs: number;
  private maxPendingSamples: number;
  private maxFinalAttempts: number;
  private minCutGapSec: number;
  private cutPadSec: number;
  private now: () => number;
  private autoTimers: boolean;
  private nextRequestId = 1;
  /** onSegmentReady promises still running (e.g. publishing after the result was handled) */
  private pendingCallbacks: Set<Promise<void>> = new Set();
  private stats: SpeakerStreamStats = { lostSamples: 0, requestFailures: 0, staleResults: 0, timedOutRequests: 0 };

  /**
   * Called when a snapshot needs transcription. The callback must answer with
   * handleTranscriptionResult() or handleTranscriptionError() (passing
   * info.requestId). If it returns a promise that settles without answering,
   * the request is treated as failed and the audio is kept.
   */
  onSegmentReady: ((speakerId: string, speakerName: string, audioBuffer: Float32Array, info: SubmissionInfo) => void | Promise<void>) | null = null;

  /** Called when a segment is confirmed and should be published. */
  onSegmentConfirmed: ((speakerId: string, speakerName: string, transcript: string, bufferStartMs: number, bufferEndMs: number, segmentId: string) => void) | null = null;

  constructor(config?: SpeakerStreamManagerConfig) {
    this.minAudioDuration = config?.minAudioDuration ?? 2;
    this.submitInterval = config?.submitInterval ?? 2;
    this.confirmThreshold = Math.max(1, Math.floor(config?.confirmThreshold ?? 2));
    this.maxBufferDuration = config?.maxBufferDuration ?? 30;
    this.idleTimeoutSec = config?.idleTimeoutSec ?? 15;
    this.sampleRate = config?.sampleRate ?? 16000;
    this.tailGuardSec = config?.tailGuardSec ?? 0.3;
    this.requestTimeoutMs = (config?.requestTimeoutSec ?? 150) * 1000;
    const maxPendingSec = config?.maxPendingDurationSec ?? Math.max(120, this.maxBufferDuration * 4);
    this.maxPendingSamples = Math.floor(Math.max(maxPendingSec, this.maxBufferDuration) * this.sampleRate);
    this.maxFinalAttempts = Math.max(1, config?.maxFinalAttempts ?? 2);
    this.minCutGapSec = Math.max(0, config?.minCutGapSec ?? 0.15);
    this.cutPadSec = Math.max(0, config?.cutPadSec ?? 0.3);
    this.now = config?.now ?? (() => Date.now());
    this.autoTimers = config?.autoTimers ?? true;
  }

  addSpeaker(speakerId: string, speakerName: string): void {
    if (this.buffers.has(speakerId)) return;

    const now = this.now();
    this.buffers.set(speakerId, {
      speakerId,
      speakerName,
      chunks: [],
      totalSamples: 0,
      bufferStartAbs: 0,
      confirmedAbs: 0,
      lastSubmittedEnd: 0,
      history: [],
      inFlight: null,
      lastAudioMs: now,
      retryAfterMs: 0,
      consecutiveFailures: 0,
      finalizeActive: false,
      finalizePromise: null,
      finalizeTarget: 0,
      finalizeReason: 'flush',
      sequenceNumber: 0,
      lastConfirmedText: '',
      lastChunkEndMs: 0,
    });

    if (this.autoTimers) {
      const timer = setInterval(() => {
        this.trySubmit(speakerId).catch((err: any) => log(`[SpeakerStreams] trySubmit error for ${speakerId}: ${err?.message}`));
      }, this.submitInterval * 1000);
      this.timers.set(speakerId, timer);
    }

    log(`[SpeakerStreams] Added speaker "${speakerName}" (${speakerId})`);
  }

  /**
   * Append audio. Never blocks and never discards: audio fed during an
   * in-flight request, flush or idle finalization stays pending.
   *
   * @param captureEndMs - wall-clock time the chunk finished capturing (default: now)
   */
  feedAudio(speakerId: string, audioData: Float32Array, captureEndMs?: number): void {
    const buffer = this.buffers.get(speakerId);
    if (!buffer || audioData.length === 0) return;

    const now = this.now();
    const endMs = isFiniteNumber(captureEndMs) ? captureEndMs : now;
    const durMs = (audioData.length / this.sampleRate) * 1000;
    let startMs = endMs - durMs;
    // Keep back-to-back chunks contiguous despite delivery jitter; chunks of one
    // stream cannot overlap (bursty delivery). Real gaps (> 150 ms, e.g. VAD
    // skipped silence) are preserved.
    if (buffer.lastChunkEndMs > 0 && startMs < buffer.lastChunkEndMs + 150) {
      startMs = buffer.lastChunkEndMs;
    }

    buffer.chunks.push({ data: audioData, startSample: buffer.bufferStartAbs + buffer.totalSamples, startMs });
    buffer.totalSamples += audioData.length;
    buffer.lastChunkEndMs = startMs + durMs;
    buffer.lastAudioMs = now;

    this.enforcePendingLimit(buffer);
  }

  /**
   * Handle a successful transcription (an empty transcript means "recognized, no speech").
   *
   * @param segmentEndSec - kept for backward compatibility; segment/word times are used instead
   * @param segments - Whisper segments (optionally with word timestamps), relative to the snapshot start
   */
  handleTranscriptionResult(
    speakerId: string,
    transcript: string,
    segmentEndSec?: number,
    segments?: WhisperSegment[],
    options?: HandleTranscriptionOptions,
  ): TranscriptionHandleResult {
    const buffer = this.buffers.get(speakerId);
    if (!buffer) return { status: 'unknown-speaker', confirmed: [], pending: [] };

    const req = this.resolveRequest(buffer, options?.requestId);
    if (req === 'stale') {
      this.stats.staleResults++;
      log(`[SpeakerStreams] Stale result ignored for "${buffer.speakerName}" (request ${options?.requestId})`);
      return { status: 'stale', confirmed: [], pending: this.draftFrom(buffer) };
    }

    let mode: SubmissionMode = 'partial';
    let baseSample = buffer.confirmedAbs;
    let endSample = buffer.bufferStartAbs + buffer.totalSamples;
    let endKnown = false;
    if (req) {
      mode = req.mode;
      baseSample = req.startSample;
      endSample = req.endSample;
      endKnown = true;
      this.settle(buffer, req, 'result');
      buffer.consecutiveFailures = 0;
      buffer.retryAfterMs = 0;
    }

    let hyp = this.buildHypothesis(transcript, segments, baseSample, endSample, endKnown);
    const fullText = hyp.tokens.map(t => t.display).join('').trim();
    if (hyp.tokens.length > 0 && isHallucination(fullText)) {
      log(`[SpeakerStreams] [FILTERED] Hallucination for "${buffer.speakerName}": "${fullText.substring(0, 60)}"`);
      hyp = { ...hyp, tokens: [] };
    }

    const confirmed: ConfirmedSegmentInfo[] = [];

    if (mode === 'final') {
      let finalHyp = hyp;
      if (finalHyp.tokens.length === 0) {
        // Final pass produced nothing usable: fall back to the latest earlier
        // hypothesis for this same audio rather than losing it.
        const prev = this.currentHypothesis(buffer);
        if (prev && prev.tokens.length > 0) finalHyp = prev;
      }
      confirmed.push(...this.commit(buffer, finalHyp, finalHyp.tokens.length, Math.max(endSample, finalHyp.endSample)));
      buffer.history = [];
      return { status: 'applied', confirmed, pending: [] };
    }

    this.pushHypothesis(buffer, hyp);

    if (mode === 'forced') {
      confirmed.push(...this.commitForced(buffer, hyp));
    } else {
      const cut = this.findAgreementCut(buffer);
      if (cut) confirmed.push(...this.commit(buffer, hyp, cut.tokenCount, cut.cutSample, cut.keyLen));
    }

    return { status: 'applied', confirmed, pending: this.draftFrom(buffer) };
  }

  /** Handle a failed transcription request. Audio stays pending and is retried. */
  handleTranscriptionError(speakerId: string, error?: unknown, options?: HandleTranscriptionOptions): void {
    const buffer = this.buffers.get(speakerId);
    if (!buffer) return;
    const req = this.resolveRequest(buffer, options?.requestId);
    if (!req || req === 'stale') return;
    this.settle(buffer, req, 'error');
    // No hypothesis was produced: the same audio must be submitted again.
    buffer.lastSubmittedEnd = Math.min(buffer.lastSubmittedEnd, req.startSample);
    this.stats.requestFailures++;
    buffer.consecutiveFailures++;
    const backoffMs = Math.min(10_000, 500 * Math.pow(2, buffer.consecutiveFailures - 1));
    buffer.retryAfterMs = this.now() + backoffMs;
    const msg = (error as any)?.message ?? String(error ?? 'unknown error');
    const pendingSec = this.unconfirmedSamples(buffer) / this.sampleRate;
    log(`[SpeakerStreams] Transcription failed for "${buffer.speakerName}" (request ${req.requestId}, ${pendingSec.toFixed(1)}s pending kept, retry in ${backoffMs}ms): ${msg}`);
  }

  /**
   * Remove a speaker synchronously. Commits the latest hypothesis for its
   * audio; audio that was never transcribed is logged and counted as lost.
   * Prefer flushSpeaker()/finalizeAll() when the caller can await.
   */
  removeSpeaker(speakerId: string): void {
    const timer = this.timers.get(speakerId);
    if (timer) clearInterval(timer);
    this.timers.delete(speakerId);

    const buffer = this.buffers.get(speakerId);
    if (buffer) {
      if (buffer.inFlight) this.settle(buffer, buffer.inFlight, 'cancelled');
      const hyp = this.currentHypothesis(buffer);
      if (hyp && hyp.tokens.length > 0) {
        this.commit(buffer, hyp, hyp.tokens.length, hyp.endSample);
      }
      const lost = this.unconfirmedSamples(buffer);
      if (lost > 0) {
        this.stats.lostSamples += lost;
        log(`[SpeakerStreams] WARNING: ${(lost / this.sampleRate).toFixed(2)}s of untranscribed audio discarded on removal of "${buffer.speakerName}"`);
      }
    }

    this.buffers.delete(speakerId);
  }

  hasSpeaker(speakerId: string): boolean {
    return this.buffers.has(speakerId);
  }

  updateSpeakerName(speakerId: string, newName: string): boolean {
    const buffer = this.buffers.get(speakerId);
    if (!buffer || buffer.speakerName === newName) return false;
    log(`[SpeakerStreams] Updated speaker name "${buffer.speakerName}" → "${newName}" (${speakerId})`);
    buffer.speakerName = newName;
    return true;
  }

  getSpeakerName(speakerId: string): string | undefined {
    return this.buffers.get(speakerId)?.speakerName;
  }

  getSegmentId(speakerId: string): string {
    const buffer = this.buffers.get(speakerId);
    const seq = buffer?.sequenceNumber ?? 0;
    return `${speakerId}:${seq}`;
  }

  getActiveSpeakers(): string[] {
    return Array.from(this.buffers.keys());
  }

  /** Wall-clock time of the first unconfirmed sample. */
  getBufferStartMs(speakerId: string): number {
    const buffer = this.buffers.get(speakerId);
    if (!buffer) return this.now();
    return this.timeAt(buffer, buffer.confirmedAbs);
  }

  getLastConfirmedText(speakerId: string): string {
    return this.buffers.get(speakerId)?.lastConfirmedText ?? '';
  }

  /** Current unconfirmed draft (tail of the latest hypothesis). */
  getPendingDraft(speakerId: string): DraftSegment[] {
    const buffer = this.buffers.get(speakerId);
    return buffer ? this.draftFrom(buffer) : [];
  }

  getStats(): SpeakerStreamStats {
    return { ...this.stats };
  }

  /** Synchronous removal of all speakers (see removeSpeaker). */
  removeAll(): void {
    for (const speakerId of Array.from(this.buffers.keys())) {
      this.removeSpeaker(speakerId);
    }
  }

  /**
   * End-of-session: stop timers, wait for in-flight requests, transcribe all
   * remaining audio as final, wait for the onSegmentReady callbacks to finish
   * (a callback may still be publishing a result it already handed back), then
   * remove every speaker. Anything that could not be transcribed within
   * `timeoutMs` is logged and reported as lost.
   *
   * Recognition outcome and callback completion are tracked separately: the
   * final flush continues as soon as a result is handed back, and only the
   * session end waits for the callbacks, so a callback never waits on itself.
   */
  async finalizeAll(timeoutMs = 20_000): Promise<FinalizeSummary> {
    const lostBefore = this.stats.lostSamples;
    const ids = Array.from(this.buffers.keys());
    let timedOut = false;
    const all = Promise.all(ids.map(id => this.flushSpeaker(id, true, 'close').catch((err: any) => {
      log(`[SpeakerStreams] Final flush error for ${id}: ${err?.message}`);
    }))).then(() => this.waitForCallbacks());
    let timer: ReturnType<typeof setTimeout> | undefined;
    const deadline = new Promise<void>(resolve => {
      timer = setTimeout(() => { timedOut = true; resolve(); }, timeoutMs);
    });
    await Promise.race([all, deadline]);
    if (timer) clearTimeout(timer);
    const pendingCallbacks = this.pendingCallbacks.size;
    if (timedOut) log(`[SpeakerStreams] WARNING: final flush did not finish within ${timeoutMs}ms (${pendingCallbacks} callback(s) still running)`);
    this.removeAll();
    return {
      speakers: ids.length,
      lostSec: (this.stats.lostSamples - lostBefore) / this.sampleRate,
      timedOut,
      pendingCallbacks,
    };
  }

  /** Resolves once no onSegmentReady callback is running (including ones started while waiting). */
  async waitForCallbacks(): Promise<void> {
    while (this.pendingCallbacks.size > 0) {
      await Promise.allSettled(Array.from(this.pendingCallbacks));
    }
  }

  /**
   * Finalize a speaker's pending audio (speaker change, idle, end of stream):
   * waits for the in-flight request, transcribes the rest as final, and
   * confirms it. Audio fed after the flush started stays pending.
   *
   * A call made while a finalize is already running joins it and extends its
   * target to the audio present at this call (e.g. finalizeAll during a
   * speaker-change flush): the returned promise resolves only after that
   * audio was finalized too.
   *
   * @param force - kept for compatibility; flush always finalizes all audio present when called
   */
  flushSpeaker(speakerId: string, force: boolean = false, reason: SubmissionReason = 'flush'): Promise<void> {
    const buffer = this.buffers.get(speakerId);
    if (!buffer) return Promise.resolve();
    if (buffer.finalizePromise) {
      const target = this.totalAbs(buffer);
      if (target > buffer.finalizeTarget) {
        buffer.finalizeTarget = target;
        buffer.finalizeReason = reason;
      }
      return buffer.finalizePromise;
    }
    const p = this.runFinalize(buffer, reason);
    if (buffer.finalizeActive) {
      buffer.finalizePromise = p.finally(() => { buffer.finalizePromise = null; });
      return buffer.finalizePromise;
    }
    return p;
  }

  /** Drive one submission cycle (used by the interval timer and by tests with autoTimers=false). */
  tick(speakerId?: string): Promise<void> {
    const ids = speakerId ? [speakerId] : Array.from(this.buffers.keys());
    return Promise.all(ids.map(id => this.trySubmit(id))).then(() => undefined);
  }

  // ── Private ──────────────────────────────────────────────────

  private unconfirmedSamples(buffer: SpeakerBuffer): number {
    return buffer.bufferStartAbs + buffer.totalSamples - buffer.confirmedAbs;
  }

  private totalAbs(buffer: SpeakerBuffer): number {
    return buffer.bufferStartAbs + buffer.totalSamples;
  }

  private async runFinalize(buffer: SpeakerBuffer, reason: SubmissionReason): Promise<void> {
    buffer.finalizeActive = true;
    buffer.finalizeTarget = this.totalAbs(buffer);
    buffer.finalizeReason = reason;
    try {
      // Consecutive failed final attempts. Reset after each result: a result
      // always advances confirmedAbs, so the loop still terminates when the
      // target is extended by a joining flush/close.
      let failures = 0;
      while (this.buffers.get(buffer.speakerId) === buffer) {
        if (buffer.inFlight) {
          const req = buffer.inFlight;
          if (!req.settled) await req.done;
          continue;
        }
        const targetEnd = buffer.finalizeTarget;
        reason = buffer.finalizeReason;
        if (buffer.confirmedAbs >= targetEnd) break;

        // The latest hypothesis already covers everything to finalize.
        const hyp = this.currentHypothesis(buffer);
        if (hyp && hyp.tokens.length > 0 && hyp.endSample >= targetEnd) {
          this.commit(buffer, hyp, hyp.tokens.length, hyp.endSample);
          buffer.history = [];
          continue;
        }

        if (!this.onSegmentReady) break;
        const req = this.submit(buffer, 'final', reason, targetEnd);
        if (!req) break;
        const outcome = req.settled ? req.outcome! : await req.done;
        if (outcome === 'result') { failures = 0; continue; }
        if (outcome === 'cancelled') break;
        failures++;
        if (failures >= this.maxFinalAttempts) {
          const fallback = this.currentHypothesis(buffer);
          if (fallback && fallback.tokens.length > 0) {
            log(`[SpeakerStreams] Final transcription failed for "${buffer.speakerName}" — confirming last draft for its audio`);
            this.commit(buffer, fallback, fallback.tokens.length, fallback.endSample);
            buffer.history = [];
          }
          const lostEnd = Math.min(targetEnd, this.totalAbs(buffer));
          const lost = Math.max(0, lostEnd - buffer.confirmedAbs);
          if (lost > 0) {
            this.stats.lostSamples += lost;
            log(`[SpeakerStreams] ERROR: ${(lost / this.sampleRate).toFixed(2)}s of audio for "${buffer.speakerName}" could not be transcribed (${reason}, ${failures} failed attempts) — discarded`);
            this.advanceTo(buffer, lostEnd);
            buffer.history = [];
          }
          break;
        }
      }
    } finally {
      buffer.finalizeActive = false;
    }
  }

  private async trySubmit(speakerId: string): Promise<void> {
    const buffer = this.buffers.get(speakerId);
    if (!buffer) return;
    const now = this.now();

    if (buffer.inFlight) {
      if (now - buffer.inFlight.sentAtMs <= this.requestTimeoutMs) return;
      this.stats.timedOutRequests++;
      log(`[SpeakerStreams] Request ${buffer.inFlight.requestId} for "${buffer.speakerName}" timed out after ${((now - buffer.inFlight.sentAtMs) / 1000).toFixed(0)}s — audio kept for retry`);
      const expired = buffer.inFlight;
      this.settle(buffer, expired, 'timeout');
      buffer.lastSubmittedEnd = Math.min(buffer.lastSubmittedEnd, expired.startSample);
      buffer.consecutiveFailures++;
    }
    if (buffer.finalizeActive) return;

    const pending = this.unconfirmedSamples(buffer);
    if (pending <= 0) return;
    if (now < buffer.retryAfterMs) return;

    const idleMs = now - buffer.lastAudioMs;
    if (idleMs > this.idleTimeoutSec * 1000) {
      log(`[SpeakerStreams] Idle finalize for "${buffer.speakerName}" (${(idleMs / 1000).toFixed(1)}s idle, ${(pending / this.sampleRate).toFixed(1)}s pending)`);
      await this.flushSpeaker(speakerId, true, 'idle');
      return;
    }

    const maxSamples = Math.floor(this.maxBufferDuration * this.sampleRate);
    if (pending > maxSamples) {
      const hyp = this.currentHypothesis(buffer);
      if (hyp && hyp.tokens.length > 0) {
        log(`[SpeakerStreams] Hard cap forced commit for "${buffer.speakerName}" (${(pending / this.sampleRate).toFixed(1)}s > ${this.maxBufferDuration}s unconfirmed)`);
        this.commitForced(buffer, hyp);
        return;
      }
      this.submit(buffer, 'forced', 'hard-cap', buffer.confirmedAbs + maxSamples);
      return;
    }

    if (pending >= this.minAudioDuration * this.sampleRate && this.totalAbs(buffer) > buffer.lastSubmittedEnd) {
      this.submit(buffer, 'partial', 'interval');
    }
  }

  /**
   * Submit [confirmedAbs, min(total, endLimit, confirmedAbs + maxBufferDuration)).
   * Synchronously invokes onSegmentReady.
   */
  private submit(buffer: SpeakerBuffer, mode: SubmissionMode, reason: SubmissionReason, endLimit?: number): InFlightRequest | null {
    if (!this.onSegmentReady || buffer.inFlight) return null;
    const start = buffer.confirmedAbs;
    const maxSamples = Math.floor(this.maxBufferDuration * this.sampleRate);
    let end = Math.min(this.totalAbs(buffer), start + maxSamples);
    if (endLimit !== undefined) end = Math.min(end, endLimit);
    if (end <= start) return null;
    // A final request that cannot cover everything up to its target is a forced commit
    if (mode === 'final' && endLimit !== undefined && end < endLimit) mode = 'forced';

    const audio = this.extract(buffer, start, end);
    let resolve!: (o: Outcome) => void;
    const done = new Promise<Outcome>(r => { resolve = r; });
    const req: InFlightRequest = {
      requestId: this.nextRequestId++,
      mode,
      reason,
      startSample: start,
      endSample: end,
      sentAtMs: this.now(),
      settled: false,
      done,
      resolve,
    };
    buffer.inFlight = req;
    buffer.lastSubmittedEnd = Math.max(buffer.lastSubmittedEnd, end);

    const info: SubmissionInfo = {
      requestId: req.requestId, mode, reason, startSample: start, endSample: end,
    };
    try {
      const ret = this.onSegmentReady(buffer.speakerId, buffer.speakerName, audio, info);
      if (ret && typeof (ret as Promise<void>).then === 'function') {
        const tracked: Promise<void> = (ret as Promise<void>).then(
          () => {
            if (!req.settled) this.handleTranscriptionError(buffer.speakerId, new Error('callback finished without a result'), { requestId: req.requestId });
          },
          (err) => {
            if (!req.settled) this.handleTranscriptionError(buffer.speakerId, err, { requestId: req.requestId });
            else log(`[SpeakerStreams] onSegmentReady failed after its result was handled for "${buffer.speakerName}" (request ${req.requestId}): ${err?.message ?? err}`);
          },
        ).finally(() => { this.pendingCallbacks.delete(tracked); });
        this.pendingCallbacks.add(tracked);
      }
    } catch (err) {
      if (!req.settled) this.handleTranscriptionError(buffer.speakerId, err, { requestId: req.requestId });
    }
    return req;
  }

  private resolveRequest(buffer: SpeakerBuffer, requestId?: number): InFlightRequest | null | 'stale' {
    const req = buffer.inFlight;
    if (requestId !== undefined) {
      if (!req || req.requestId !== requestId) return 'stale';
    }
    if (!req) return null;
    if (req.startSample !== buffer.confirmedAbs) {
      // Confirmed position moved since submission — the snapshot no longer lines up
      this.settle(buffer, req, 'stale');
      return 'stale';
    }
    return req;
  }

  private settle(buffer: SpeakerBuffer, req: InFlightRequest, outcome: Outcome): void {
    if (req.settled) return;
    req.settled = true;
    req.outcome = outcome;
    if (buffer.inFlight === req) buffer.inFlight = null;
    req.resolve(outcome);
  }

  private extract(buffer: SpeakerBuffer, start: number, end: number): Float32Array {
    const out = new Float32Array(end - start);
    for (const chunk of buffer.chunks) {
      const cStart = chunk.startSample;
      const cEnd = cStart + chunk.data.length;
      if (cEnd <= start) continue;
      if (cStart >= end) break;
      const from = Math.max(start, cStart);
      const to = Math.min(end, cEnd);
      out.set(chunk.data.subarray(from - cStart, to - cStart), from - start);
    }
    return out;
  }

  /** Wall-clock ms for an absolute sample index. */
  private timeAt(buffer: SpeakerBuffer, abs: number): number {
    const chunks = buffer.chunks;
    if (chunks.length === 0) return buffer.lastChunkEndMs || this.now();
    let chosen = chunks[0];
    for (const c of chunks) {
      if (c.startSample <= abs) chosen = c; else break;
    }
    return chosen.startMs + ((abs - chosen.startSample) / this.sampleRate) * 1000;
  }

  private currentHypothesis(buffer: SpeakerBuffer): Hypothesis | null {
    const h = buffer.history[buffer.history.length - 1];
    if (!h || h.baseSample !== buffer.confirmedAbs) return null;
    return h;
  }

  private pushHypothesis(buffer: SpeakerBuffer, hyp: Hypothesis): void {
    buffer.history = buffer.history.filter(h => h.baseSample === hyp.baseSample);
    const last = buffer.history[buffer.history.length - 1];
    // Re-recognizing the same audio is not evidence of stability: replace, don't count.
    if (last && last.endKnown && hyp.endKnown && hyp.endSample <= last.endSample) {
      buffer.history[buffer.history.length - 1] = hyp;
    } else {
      buffer.history.push(hyp);
    }
    const keep = Math.max(1, this.confirmThreshold);
    if (buffer.history.length > keep) buffer.history = buffer.history.slice(-keep);
  }

  private buildHypothesis(transcript: string, segments: WhisperSegment[] | undefined, baseSample: number, endSample: number, endKnown: boolean): Hypothesis {
    const tokens: Token[] = [];
    let timed = false;
    const segs = (segments || []).filter(s => s && typeof s.text === 'string');
    if (segs.length > 0) {
      timed = true;
      segs.forEach((seg, segIndex) => {
        const words = (seg.words || []).filter(w => w && typeof w.word === 'string' && w.word.length > 0);
        const wordsTimed = words.length > 0 && words.every(w => isFiniteNumber(w.start) && isFiniteNumber(w.end));
        if (wordsTimed) {
          words.forEach((w, i) => {
            const last = i === words.length - 1;
            tokens.push({
              display: w.word,
              key: normalizeKey(w.word),
              start: w.start,
              end: w.end,
              segIndex,
              boundaryAfter: true,
              strongBoundary: last || SENTENCE_END.test(w.word),
              wordLevel: true,
            });
          });
        } else {
          // No word timestamps: the only safe cut is the segment end.
          tokens.push({
            display: seg.text,
            key: normalizeKey(seg.text),
            start: isFiniteNumber(seg.start) ? seg.start : undefined,
            end: isFiniteNumber(seg.end) ? seg.end : undefined,
            segIndex,
            boundaryAfter: true,
            strongBoundary: true,
            wordLevel: false,
          });
        }
      });
      if (tokens.some(t => t.start === undefined || t.end === undefined)) timed = false;
    } else if (transcript && transcript.trim()) {
      tokens.push({ display: transcript, key: normalizeKey(transcript), segIndex: 0, boundaryAfter: true, strongBoundary: true, wordLevel: false });
    }
    const nonEmpty = tokens.filter(t => t.display.trim().length > 0);
    return { baseSample, endSample, endKnown, tokens: nonEmpty, timed };
  }

  /** Cumulative key length after each token. */
  private cutPositions(h: Hypothesis): number[] {
    const out: number[] = [];
    let acc = 0;
    for (const t of h.tokens) { acc += t.key.length; out.push(acc); }
    return out;
  }

  /**
   * LocalAgreement over the last `confirmThreshold` hypotheses (same base).
   * Returns the largest cut at a boundary shared by all of them, within their
   * common normalized prefix, excluding the unstable tail of the newest one.
   */
  private findAgreementCut(buffer: SpeakerBuffer): { tokenCount: number; cutSample: number; keyLen: number } | null {
    const hyps = buffer.history;
    if (hyps.length < this.confirmThreshold) return null;
    const window = hyps.slice(-this.confirmThreshold);
    const cur = window[window.length - 1];
    if (cur.tokens.length === 0) return null;

    const keys = window.map(h => h.tokens.map(t => t.key).join(''));
    let common = keys[0].length;
    for (const k of keys) common = Math.min(common, k.length);
    for (let i = 0; i < common; i++) {
      const c = keys[0][i];
      if (!keys.every(k => k[i] === c)) { common = i; break; }
    }
    if (common === 0) return null;

    const curCuts = this.cutPositions(cur);
    const otherCutSets = window.slice(0, -1).map(h => {
      const positions = this.cutPositions(h);
      const s = new Set<number>();
      h.tokens.forEach((t, i) => { if (t.boundaryAfter) s.add(positions[i]); });
      return s;
    });
    const snapshotSec = (cur.endSample - cur.baseSample) / this.sampleRate;
    // Prefer cuts at segment/sentence ends (readable captions). Cut inside a
    // segment only once the unconfirmed window is long, to avoid stalling.
    // Either way the cut must fall in a pause.
    const allowWeak = snapshotSec >= Math.min(6, this.maxBufferDuration / 2);

    let best: number | null = null;
    let bestStrong: number | null = null;
    for (let i = 0; i < cur.tokens.length; i++) {
      const t = cur.tokens[i];
      const pos = curCuts[i];
      if (pos > common) break;
      if (!t.boundaryAfter || pos === 0) continue;
      if (!otherCutSets.every(s => s.has(pos))) continue;
      if (cur.timed) {
        if (t.end! > snapshotSec - this.tailGuardSec && cur.endKnown) continue;
        // Only cut audio where the speaker paused (see class comment)
        if (!this.isPauseAfter(cur, i)) continue;
      } else if (i !== cur.tokens.length - 1) {
        // Without timing a partial cut cannot be mapped to audio.
        continue;
      }
      best = i;
      if (t.strongBoundary) bestStrong = i;
    }
    const chosen = allowWeak ? best : bestStrong;
    if (chosen === null) return null;
    const cutSample = this.cutSampleAfter(cur, chosen);
    // Emitting text without advancing past audio that exists would re-transcribe
    // (and duplicate) it. With no audio behind the hypothesis nothing is re-sent.
    // The committed words themselves must span audio: padding into the pause
    // must not turn degenerate (zero) timestamps into a cut.
    if (cur.endSample > cur.baseSample) {
      if (cutSample <= cur.baseSample) return null;
      if (cur.timed && cur.tokens[chosen].end! <= 0) return null;
    }
    return { tokenCount: chosen + 1, cutSample, keyLen: curCuts[chosen] };
  }

  /**
   * Whether the audio after token `idx` is a pause that can be cut.
   * Timed word tokens need a gap of at least minCutGapSec before the next
   * token. Segment-only tokens rely on the backend's segment boundary. The
   * last token is followed by the snapshot tail, which the caller checks
   * against tailGuardSec.
   */
  private isPauseAfter(h: Hypothesis, idx: number): boolean {
    const t = h.tokens[idx];
    const next = h.tokens[idx + 1];
    if (!next) return true;
    if (!t.wordLevel && !next.wordLevel) return true;
    if (t.end === undefined || next.start === undefined) return false;
    return next.start - t.end >= this.minCutGapSec;
  }

  /**
   * Audio position after token `idx`: inside the following pause (half of it,
   * at most cutPadSec), so neither the committed word's ending nor the next
   * word's onset is clipped by timestamp error.
   */
  private cutSampleAfter(h: Hypothesis, idx: number): number {
    if (!h.timed) {
      // Untimed hypotheses are only ever committed as a whole.
      return h.endSample;
    }
    let cutSec = h.tokens[idx].end!;
    const next = h.tokens[idx + 1];
    const limitSec = next && next.start !== undefined
      ? next.start
      : (!next && h.endKnown ? (h.endSample - h.baseSample) / this.sampleRate : undefined);
    if (limitSec !== undefined) {
      if (limitSec < cutSec) cutSec = limitSec;
      else cutSec += Math.min((limitSec - cutSec) / 2, this.cutPadSec);
    }
    const s = h.baseSample + Math.round(cutSec * this.sampleRate);
    return Math.max(h.baseSample, Math.min(h.endSample, s));
  }

  /**
   * Forced commit (hard cap): confirm everything except the unstable tail,
   * or everything if nothing precedes the tail.
   */
  private commitForced(buffer: SpeakerBuffer, hyp: Hypothesis): ConfirmedSegmentInfo[] {
    if (hyp.tokens.length === 0) {
      // Recognized but empty (silence/noise) — release the snapshot audio
      this.advanceTo(buffer, hyp.endSample);
      buffer.history = [];
      return [];
    }
    let count = hyp.tokens.length;
    let cutSample = hyp.endSample;
    if (hyp.timed) {
      const snapshotSec = (hyp.endSample - hyp.baseSample) / this.sampleRate;
      let latest = -1;
      let latestPause = -1;
      for (let i = 0; i < hyp.tokens.length; i++) {
        if (hyp.tokens[i].end! > snapshotSec - this.tailGuardSec) continue;
        latest = i;
        if (this.isPauseAfter(hyp, i)) latestPause = i;
      }
      // Prefer a pause; without any pause in the whole window, cut anyway so
      // the hard cap still makes progress (logged: the next word may be clipped).
      const idx = latestPause >= 0 ? latestPause : latest;
      if (idx >= 0 && latestPause < 0 && idx < hyp.tokens.length - 1) {
        log(`[SpeakerStreams] Forced commit for "${buffer.speakerName}" without a pause in ${snapshotSec.toFixed(1)}s — cutting inside continuous speech`);
      }
      if (idx >= 0 && idx < hyp.tokens.length - 1) {
        const c = this.cutSampleAfter(hyp, idx);
        // Degenerate timestamps must not stall progress: then commit everything
        if (c > hyp.baseSample && hyp.tokens[idx].end! > 0) {
          count = idx + 1;
          cutSample = c;
        }
      }
    }
    const out = this.commit(buffer, hyp, count, cutSample);
    buffer.history = buffer.history.slice(-1);
    return out;
  }

  /**
   * Emit the first `count` tokens of `hyp` and advance the confirmed position
   * to `cutSample`. Remaining tokens of the hypotheses are rebased.
   */
  private commit(buffer: SpeakerBuffer, hyp: Hypothesis, count: number, cutSample: number, keyLen?: number): ConfirmedSegmentInfo[] {
    const out: ConfirmedSegmentInfo[] = [];
    const committed = hyp.tokens.slice(0, count);
    const snapshotStartMs = this.timeAt(buffer, hyp.baseSample);

    // One confirmed segment per Whisper segment (keeps sentence boundaries)
    const groups: Token[][] = [];
    for (const t of committed) {
      const g = groups[groups.length - 1];
      if (g && g[0].segIndex === t.segIndex) g.push(t); else groups.push([t]);
    }
    const cutMs = this.timeAt(buffer, cutSample);
    for (const g of groups) {
      const text = g.map(t => t.display).join('').trim();
      if (!text || g.every(t => !t.key)) continue;
      if (isHallucination(text)) {
        log(`[SpeakerStreams] [FILTERED] Hallucination segment for "${buffer.speakerName}": "${text.substring(0, 60)}"`);
        continue;
      }
      const startMs = hyp.timed && g[0].start !== undefined
        ? this.timeAt(buffer, hyp.baseSample + Math.round(g[0].start * this.sampleRate))
        : snapshotStartMs;
      const endMs = hyp.timed && g[g.length - 1].end !== undefined
        ? this.timeAt(buffer, hyp.baseSample + Math.round(g[g.length - 1].end! * this.sampleRate))
        : cutMs;
      const segmentId = `${buffer.speakerId}:${buffer.sequenceNumber}`;
      buffer.sequenceNumber++;
      buffer.lastConfirmedText = text;
      out.push({ text, startMs, endMs: Math.max(startMs, endMs), segmentId });
      if (this.onSegmentConfirmed) {
        this.onSegmentConfirmed(buffer.speakerId, buffer.speakerName, text, startMs, Math.max(startMs, endMs), segmentId);
      }
    }

    // Rebase the hypotheses that agree on the committed prefix
    const shiftSec = (cutSample - hyp.baseSample) / this.sampleRate;
    const committedKeyLen = keyLen ?? committed.reduce((a, t) => a + t.key.length, 0);
    const rebased: Hypothesis[] = [];
    for (const h of buffer.history) {
      if (h.baseSample !== hyp.baseSample) continue;
      let dropCount: number;
      if (h === hyp) {
        dropCount = count;
      } else {
        const cuts = this.cutPositions(h);
        const idx = cuts.findIndex((p, i) => p === committedKeyLen && h.tokens[i].boundaryAfter);
        if (idx < 0) continue;
        dropCount = idx + 1;
      }
      const rest = h.tokens.slice(dropCount).map(t => ({
        ...t,
        start: t.start !== undefined ? Math.max(0, t.start - shiftSec) : undefined,
        end: t.end !== undefined ? Math.max(0, t.end - shiftSec) : undefined,
      }));
      rebased.push({ ...h, baseSample: cutSample, endSample: Math.max(cutSample, h.endSample), tokens: rest });
    }
    buffer.history = rebased;
    this.advanceTo(buffer, cutSample);
    return out;
  }

  /** Move the confirmed position forward and free confirmed audio. */
  private advanceTo(buffer: SpeakerBuffer, abs: number): void {
    const target = Math.min(Math.max(abs, buffer.confirmedAbs), this.totalAbs(buffer));
    buffer.confirmedAbs = target;
    const newChunks: AudioChunk[] = [];
    for (const chunk of buffer.chunks) {
      const cEnd = chunk.startSample + chunk.data.length;
      if (cEnd <= target) continue;
      if (chunk.startSample < target) {
        const skip = target - chunk.startSample;
        newChunks.push({
          data: chunk.data.subarray(skip),
          startSample: target,
          startMs: chunk.startMs + (skip / this.sampleRate) * 1000,
        });
      } else {
        newChunks.push(chunk);
      }
    }
    const end = this.totalAbs(buffer);
    buffer.chunks = newChunks;
    buffer.bufferStartAbs = target;
    buffer.totalSamples = end - target;
    if (buffer.lastSubmittedEnd < target) buffer.lastSubmittedEnd = target;
  }

  /** Bound memory: drop the oldest pending audio beyond maxPendingSamples, loudly. */
  private enforcePendingLimit(buffer: SpeakerBuffer): void {
    const pending = this.unconfirmedSamples(buffer);
    if (pending <= this.maxPendingSamples) return;
    const drop = pending - this.maxPendingSamples;
    this.stats.lostSamples += drop;
    log(`[SpeakerStreams] ERROR: pending audio for "${buffer.speakerName}" exceeded ${(this.maxPendingSamples / this.sampleRate).toFixed(0)}s (transcription backlog) — dropping oldest ${(drop / this.sampleRate).toFixed(2)}s untranscribed`);
    if (buffer.inFlight) this.settle(buffer, buffer.inFlight, 'stale');
    buffer.history = [];
    this.advanceTo(buffer, buffer.confirmedAbs + drop);
  }

  /** Draft = unconfirmed tail of the latest hypothesis. */
  private draftFrom(buffer: SpeakerBuffer): DraftSegment[] {
    const hyp = this.currentHypothesis(buffer);
    if (!hyp || hyp.tokens.length === 0) return [];
    const groups: Token[][] = [];
    for (const t of hyp.tokens) {
      const g = groups[groups.length - 1];
      if (g && g[0].segIndex === t.segIndex) g.push(t); else groups.push([t]);
    }
    const baseMs = this.timeAt(buffer, hyp.baseSample);
    const endMs = this.timeAt(buffer, hyp.endSample);
    const out: DraftSegment[] = [];
    for (const g of groups) {
      const text = g.map(t => t.display).join('').trim();
      if (!text) continue;
      const s = hyp.timed && g[0].start !== undefined ? this.timeAt(buffer, hyp.baseSample + Math.round(g[0].start * this.sampleRate)) : baseMs;
      const e = hyp.timed && g[g.length - 1].end !== undefined ? this.timeAt(buffer, hyp.baseSample + Math.round(g[g.length - 1].end! * this.sampleRate)) : endMs;
      out.push({ text, startMs: s, endMs: Math.max(s, e) });
    }
    return out;
  }
}
