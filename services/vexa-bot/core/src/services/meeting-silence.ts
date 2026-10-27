import type { StreamingVad, VadSpeakerState } from './vad';

export const MEETING_SILENCE_TIMEOUT_MS = 10 * 60 * 1000;

/** Independent of participant count and transcription (including deferred ASR). */
export class MeetingSilenceMonitor {
  private state: VadSpeakerState;
  private lastSpeechAt: number | null = null;
  private stopped = false;
  private processing = false;

  constructor(
    private readonly vad: StreamingVad,
    private readonly onTimeout: () => void,
    private readonly onError: (error: unknown) => void,
    private readonly now: () => number = () => performance.now(),
  ) {
    this.state = vad.createSpeakerState();
  }

  // Arm only once the post-admission audio processor is ready. Waiting-room
  // time and a failed audio setup must never count as meeting silence.
  start(): void {
    if (!this.stopped && this.lastSpeechAt === null) this.lastSpeechAt = this.now();
  }

  async observeAudio(samples: Float32Array): Promise<void> {
    if (this.stopped || this.lastSpeechAt === null || this.processing || !samples.length) return;
    this.processing = true;
    const capturedAt = this.now();
    try {
      const result = await this.vad.processStreamingChunk(samples, this.state);
      if (!this.stopped && result.speech) this.lastSpeechAt = capturedAt;
    } catch (error) {
      // An unavailable detector is not evidence of silence. Existing alone
      // and maximum-duration limits remain in force if VAD fails.
      this.stop();
      this.onError(error);
    } finally {
      this.processing = false;
    }
  }

  check(): void {
    if (this.stopped || this.processing || this.lastSpeechAt === null) return;
    if (this.now() - this.lastSpeechAt >= MEETING_SILENCE_TIMEOUT_MS) {
      this.stop();
      this.onTimeout();
    }
  }

  stop(): void {
    this.stopped = true;
  }
}
