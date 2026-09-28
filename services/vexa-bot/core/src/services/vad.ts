/**
 * Silero VAD (Voice Activity Detection) for Node.js.
 *
 * Wraps the Silero ONNX model via onnxruntime-node.
 *
 * Two modes:
 * 1. Batch: `isSpeech(buffer)` — check if a buffer contains speech (legacy, used in tests)
 * 2. Streaming: per-speaker state with hysteresis for real-time speech/silence transitions
 *
 * Streaming mode uses 512-sample windows at 16kHz (32ms each) with a 64-sample
 * context buffer carried between calls. Each speaker gets their own LSTM state
 * to prevent cross-contamination.
 *
 * Reference: @jjhbw/silero-vad library (lib.js) and Silero VAD Python iterator.
 */

import { log } from '../utils';

let ort: any = null;

async function getOrt() {
  if (!ort) {
    ort = require('onnxruntime-node');
  }
  return ort;
}

/** Correct window size for 16kHz audio (per Silero spec) */
const WINDOW_SIZE = 512;   // 32ms at 16kHz
/** Context samples prepended to each window for boundary continuity */
const CONTEXT_SIZE = 64;
const SAMPLE_RATE = 16000;

/**
 * Per-speaker VAD state for streaming mode.
 * Each speaker gets their own LSTM state and hysteresis tracker.
 */
export interface VadSpeakerState {
  /** LSTM hidden state — carried between processChunk calls */
  lstmState: Float32Array;
  /** Last CONTEXT_SIZE samples from previous call — prepended to next window */
  context: Float32Array;
  /** Whether we're currently in a speech region */
  triggered: boolean;
  /** Sample position where silence was first detected (for min-silence check) */
  tempEnd: number;
  /** Total samples processed (for timing) */
  currentSample: number;
  /** Trailing samples (< WINDOW_SIZE) not yet evaluated — carried into the next call */
  remainder?: Float32Array;
  /** Serializes calls on this state so LSTM state/context are updated in order */
  queue?: Promise<unknown>;
}

/** Result of evaluating one streaming chunk. */
export interface StreamingVadResult {
  /** True if any part of the chunk was inside a speech region */
  speech: boolean;
  /** Hysteresis state after the chunk (speech still ongoing) */
  triggered: boolean;
  /** Highest speech probability seen in the chunk */
  maxProb: number;
}

/** Minimal streaming VAD interface (lets VadGate be tested without ONNX). */
export interface StreamingVad {
  createSpeakerState(): VadSpeakerState;
  processStreamingChunk(audio: Float32Array, state: VadSpeakerState): Promise<StreamingVadResult>;
}

export class SileroVAD implements StreamingVad {
  private session: any;
  private threshold: number;
  private negThreshold: number;
  /** Minimum silence duration (samples) before speech_end fires */
  private minSilenceSamples: number;
  /** Reusable sr tensor (immutable) */
  private srTensor: any;

  private constructor(session: any, threshold: number, minSilenceDurationMs: number) {
    this.session = session;
    this.threshold = threshold;
    this.negThreshold = Math.max(threshold - 0.15, 0.01);
    this.minSilenceSamples = (SAMPLE_RATE * minSilenceDurationMs) / 1000;
    this.srTensor = null; // initialized lazily with ort
  }

  static async create(threshold = 0.6, minSilenceDurationMs = 250): Promise<SileroVAD> {
    const ort = await getOrt();
    const path = require('path');
    const fs = require('fs');

    const candidates = [
      path.resolve(__dirname, '..', '..', 'node_modules', '@jjhbw', 'silero-vad', 'weights', 'silero_vad.onnx'),
      path.resolve(__dirname, '..', '..', '..', 'node_modules', '@jjhbw', 'silero-vad', 'weights', 'silero_vad.onnx'),
      '/app/vexa-bot/core/node_modules/@jjhbw/silero-vad/weights/silero_vad.onnx',
      '/app/silero_vad.onnx',
    ];

    let modelPath = '';
    for (const p of candidates) {
      if (fs.existsSync(p)) { modelPath = p; break; }
    }

    if (!modelPath) {
      throw new Error('Silero VAD model not found');
    }

    const session = await ort.InferenceSession.create(modelPath);
    log(`[VAD] Silero model loaded from ${modelPath}`);
    return new SileroVAD(session, threshold, minSilenceDurationMs);
  }

  /** Create a fresh per-speaker VAD state */
  createSpeakerState(): VadSpeakerState {
    return {
      lstmState: new Float32Array(2 * 1 * 128),
      context: new Float32Array(CONTEXT_SIZE),
      triggered: false,
      tempEnd: 0,
      currentSample: 0,
    };
  }

  /**
   * Process a single 512-sample window through the ONNX model with speaker-specific state.
   * Prepends the context buffer (64 samples) for boundary continuity.
   * Updates the speaker's LSTM state and context in-place.
   */
  private async processWindow(window: Float32Array, state: VadSpeakerState): Promise<number> {
    const ort = await getOrt();

    // Build input: context (64) + window (512) = 576 samples.
    // Allocated per call: a shared buffer would be overwritten by another
    // speaker's window while session.run() is awaited.
    const input = new Float32Array(CONTEXT_SIZE + WINDOW_SIZE);
    input.set(state.context, 0);
    input.set(window, CONTEXT_SIZE);

    const inputTensor = new ort.Tensor('float32', input, [1, CONTEXT_SIZE + WINDOW_SIZE]);
    const stateTensor = new ort.Tensor('float32', state.lstmState, [2, 1, 128]);
    if (!this.srTensor) {
      this.srTensor = new ort.Tensor('int64', new BigInt64Array([BigInt(SAMPLE_RATE)]), [1]);
    }

    const results = await this.session.run({
      input: inputTensor,
      state: stateTensor,
      sr: this.srTensor,
    });

    const prob = results.output.data[0] as number;

    // Update speaker state
    state.lstmState = new Float32Array(results.stateN.data as Float32Array);
    // Carry last CONTEXT_SIZE samples as context for next call
    state.context.set(input.subarray(input.length - CONTEXT_SIZE));

    return prob;
  }

  /**
   * Process an audio chunk (typically 4096 samples = 256ms from browser ScriptProcessor)
   * through a speaker's VAD state.
   *
   * Uses hysteresis: speech starts at `threshold`, ends when probability
   * stays below `negThreshold` for `minSilenceDurationMs`.
   *
   * `speech` is true if ANY window of the chunk was inside a speech region —
   * including a window where speech ended — so a chunk whose speech ends
   * mid-chunk is not dropped. Samples that do not fill a whole window are
   * carried into the next call instead of being skipped.
   */
  processStreamingChunk(audio: Float32Array, state: VadSpeakerState): Promise<StreamingVadResult> {
    const run = async (): Promise<StreamingVadResult> => {
      let input = audio;
      if (state.remainder && state.remainder.length > 0) {
        input = new Float32Array(state.remainder.length + audio.length);
        input.set(state.remainder, 0);
        input.set(audio, state.remainder.length);
      }
      let speech = false;
      let maxProb = 0;
      let i = 0;
      for (; i + WINDOW_SIZE <= input.length; i += WINDOW_SIZE) {
        const window = input.subarray(i, i + WINDOW_SIZE);
        const wasTriggered = state.triggered;
        const prob = await this.processWindow(window, state);
        if (prob > maxProb) maxProb = prob;
        state.currentSample += WINDOW_SIZE;

        // Hysteresis logic (matches Silero VADIterator / @jjhbw getSpeechTimestamps)
        if (prob >= this.threshold && state.tempEnd) {
          // Was in tentative silence, but speech resumed — cancel silence detection
          state.tempEnd = 0;
        }

        if (prob >= this.threshold && !state.triggered) {
          // Speech start
          state.triggered = true;
        }

        if (prob < this.negThreshold && state.triggered) {
          // Possible speech end — start counting silence duration
          if (!state.tempEnd) {
            state.tempEnd = state.currentSample;
          }
          if (state.currentSample - state.tempEnd >= this.minSilenceSamples) {
            // Confirmed speech end — silence lasted long enough
            state.triggered = false;
            state.tempEnd = 0;
          }
        }

        if (wasTriggered || state.triggered) speech = true;
      }
      state.remainder = i < input.length ? input.slice(i) : undefined;
      return { speech, triggered: state.triggered, maxProb };
    };
    const prev = state.queue ?? Promise.resolve();
    const next = prev.then(run, run);
    state.queue = next.catch(() => undefined);
    return next;
  }

  /**
   * Returns true if the chunk contains speech (see processStreamingChunk).
   * Before this fix it returned only the state at the END of the chunk, which
   * dropped chunks whose speech ended mid-chunk.
   */
  async isSpeechStreaming(audio: Float32Array, state: VadSpeakerState): Promise<boolean> {
    return (await this.processStreamingChunk(audio, state)).speech;
  }

  /**
   * Legacy batch API: check if a buffer contains speech.
   * Processes in WINDOW_SIZE (512) sample chunks using a temporary state.
   * Used in tests and backward-compat paths.
   */
  async isSpeech(audio: Float32Array): Promise<boolean> {
    const tempState = this.createSpeakerState();
    let maxProb = 0;

    for (let i = 0; i + WINDOW_SIZE <= audio.length; i += WINDOW_SIZE) {
      const window = audio.subarray(i, i + WINDOW_SIZE);
      const prob = await this.processWindow(window, tempState);
      if (prob > maxProb) maxProb = prob;
      if (maxProb > this.threshold) return true;
    }

    return maxProb > this.threshold;
  }

  resetState(): void {
    // Legacy — only relevant if using shared state (deprecated)
  }
}

export interface VadGateOptions {
  /** Audio kept before speech onset (ms). Default 500 */
  preRollMs?: number;
  /** Audio kept after speech end (ms). Default 500 */
  postRollMs?: number;
  sampleRate?: number;
}

export interface GatedChunk {
  data: Float32Array;
  /** Wall-clock time the chunk finished capturing */
  captureEndMs: number;
}

/**
 * Per-speaker VAD gate that keeps word onsets and endings: when speech starts,
 * the preceding non-speech audio (pre-roll) is released first; after speech
 * ends, audio keeps flowing for the post-roll. Chunks are released in order.
 */
export class VadGate {
  private state: VadSpeakerState;
  private preRoll: GatedChunk[] = [];
  private preRollSamples: number;
  private postRollSamples: number;
  private postRollRemaining = 0;

  constructor(private vad: StreamingVad, options?: VadGateOptions) {
    const sr = options?.sampleRate ?? SAMPLE_RATE;
    this.preRollSamples = Math.max(0, Math.round(((options?.preRollMs ?? 500) / 1000) * sr));
    this.postRollSamples = Math.max(0, Math.round(((options?.postRollMs ?? 500) / 1000) * sr));
    this.state = vad.createSpeakerState();
  }

  /** Returns the chunks to forward (possibly pre-roll + current), empty for silence. */
  async process(audio: Float32Array, captureEndMs: number): Promise<{ speech: boolean; chunks: GatedChunk[] }> {
    const result = await this.vad.processStreamingChunk(audio, this.state);
    const current: GatedChunk = { data: audio, captureEndMs };
    if (result.speech) {
      const chunks = [...this.preRoll, current];
      this.preRoll = [];
      this.postRollRemaining = this.postRollSamples;
      return { speech: true, chunks };
    }
    if (this.postRollRemaining > 0) {
      this.postRollRemaining -= audio.length;
      return { speech: false, chunks: [current] };
    }
    if (this.preRollSamples > 0) {
      this.preRoll.push(current);
      let total = this.preRoll.reduce((a, c) => a + c.data.length, 0);
      while (this.preRoll.length > 0 && total > this.preRollSamples) {
        const first = this.preRoll[0];
        const excess = total - this.preRollSamples;
        if (excess >= first.data.length) {
          this.preRoll.shift();
          total -= first.data.length;
        } else {
          // Keep only the newest part of the oldest chunk (its end time is unchanged)
          this.preRoll[0] = { data: first.data.subarray(excess), captureEndMs: first.captureEndMs };
          total -= excess;
        }
      }
    }
    return { speech: false, chunks: [] };
  }
}
