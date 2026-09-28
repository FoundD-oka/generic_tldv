import type { TranscriptionSegment } from './segment-publisher';
import type { FinalizeSummary, SpeakerStreamStats } from './speaker-streams';

/**
 * End-of-session ordering for the live transcription pipeline:
 *
 *   1. finalizeAll: wait for in-flight requests, transcribe remaining audio,
 *      and wait for onSegmentReady callbacks that are still publishing
 *   2. drain publishes still being delivered (ordered publisher: one overall
 *      time budget, not per queued update),
 *      then publish confirmed segments that were not published yet
 *   3. session_end, then close the publisher
 *
 * session_end is never sent while a callback of this session is still
 * publishing, unless the bounded final-flush timeout expired (reported).
 * Callers must close audio intake (and drain it) before calling this.
 */

export interface SessionCloseManager {
  finalizeAll(timeoutMs?: number): Promise<FinalizeSummary>;
  getStats(): SpeakerStreamStats;
}

export interface SessionClosePublisher {
  publishTranscript(speaker: string, confirmed: TranscriptionSegment[], pending: TranscriptionSegment[]): Promise<void>;
  publishSessionEnd(): Promise<void>;
  close(): Promise<void>;
  /**
   * Optional: wait (bounded by one overall budget) for publishes still being
   * delivered, so the final flush and session_end follow them. Returns false
   * if the budget expired and updates were abandoned or not started.
   */
  drain?(): Promise<boolean>;
}

export interface SessionCloseOptions {
  manager: SessionCloseManager | null;
  publisher: SessionClosePublisher | null;
  finalFlushTimeoutMs: number;
  /** Returns and clears confirmed segments not yet published (read after finalizeAll). */
  takeConfirmedBatches: () => Map<string, TranscriptionSegment[]>;
  log: (msg: string) => void;
  sampleRate?: number;
}

export async function closeTranscriptionSession(opts: SessionCloseOptions): Promise<FinalizeSummary | null> {
  const { manager, publisher, log } = opts;
  const sampleRate = opts.sampleRate ?? 16000;
  let summary: FinalizeSummary | null = null;

  if (manager) {
    summary = await manager.finalizeAll(opts.finalFlushTimeoutMs);
    const stats = manager.getStats();
    log(`[PerSpeaker] Final flush: ${summary.speakers} speaker(s), lost=${summary.lostSec.toFixed(2)}s${summary.timedOut ? ` (timed out, ${summary.pendingCallbacks} callback(s) still running)` : ''} | session lost=${(stats.lostSamples / sampleRate).toFixed(2)}s failures=${stats.requestFailures} stale=${stats.staleResults} timeouts=${stats.timedOutRequests}`);
  }

  // Read the batches only now: finalizeAll's removal step may have confirmed more.
  const batches = opts.takeConfirmedBatches();
  if (publisher) {
    if (publisher.drain) await publisher.drain();
    for (const [, batch] of batches) {
      if (batch.length > 0) {
        const speakerName = batch[0].speaker;
        log(`[PerSpeaker] Flushing ${batch.length} confirmed segment(s) for ${speakerName}`);
        await publisher.publishTranscript(speakerName, batch, []);
      }
    }
    await publisher.publishSessionEnd();
    await publisher.close();
  }
  return summary;
}
