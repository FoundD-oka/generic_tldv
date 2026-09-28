import { log } from '../utils';

export interface TranscriptionWord {
  word: string;
  start: number;
  end: number;
  probability: number;
}

export interface TranscriptionSegment {
  start: number;
  end: number;
  text: string;
  avg_logprob?: number;
  no_speech_prob?: number;
  compression_ratio?: number;
  words?: TranscriptionWord[];
}

export interface TranscriptionResult {
  text: string;
  language: string;
  language_probability?: number;
  duration: number;
  segments: TranscriptionSegment[];
}

export interface TranscriptionClientConfig {
  /** Base URL of transcription-service, e.g. "http://localhost:8083" */
  serviceUrl: string;
  /** Optional bearer token for authentication */
  apiToken?: string;
  /** Max retry attempts for transient failures. Default: 3 */
  maxRetries?: number;
  /** Base delay between retries in ms. Default: 1000 */
  retryDelayMs?: number;
  /** Sample rate of input audio. Default: 16000 */
  sampleRate?: number;
  /** Max speech segment duration in seconds. Whisper forces a segment split at this length.
   *  Lower values = more frequent confirmations = faster output. Default: server default (15s) */
  maxSpeechDurationSec?: number;
  /** Minimum silence duration (ms) for VAD to split segments. Lower = more splits at natural pauses.
   *  Default: server default (160ms). Use ~100ms for more granular segments. */
  minSilenceDurationMs?: number;
  /**
   * When a prompt (previous confirmed text) is sent and the result leaves speech
   * at the start of the audio unrecognized, re-transcribe once without the prompt
   * and use that result if it recognizes the head. Whisper drops speech that
   * repeats the end of its prompt ("違います。" prompt + "違います。承認…" audio
   * → "承認…"), so a legitimately repeated phrase after a commit would be lost.
   * Default: true
   */
  verifyPromptedHead?: boolean;
}

/** Frame length for the head-speech check (seconds). */
const HEAD_FRAME_SEC = 0.02;
/** Recognized-word start times are approximate: this much before the first word is not "uncovered". */
const HEAD_TOLERANCE_SEC = 0.1;
/** Uncovered voiced audio needed before the head is treated as dropped (seconds). */
const MIN_UNCOVERED_VOICED_SEC = 0.2;
/** A frame is voiced at this fraction of the loudest frame of the request... */
const VOICED_RELATIVE = 0.15;
/** ...and never below this absolute RMS (about -50 dBFS), so near-silence never counts. */
const VOICED_ABS_FLOOR = 0.003;

/**
 * Start (seconds) of the first recognized, non-empty text in a result.
 * Word timestamps are used when present, otherwise segment starts.
 * Infinity when nothing was recognized.
 */
export function firstRecognizedStartSec(result: Pick<TranscriptionResult, 'text' | 'segments'>): number {
  let first = Infinity;
  for (const seg of result.segments || []) {
    const words = (seg.words || []).filter(w => w && typeof w.word === 'string' && w.word.trim().length > 0);
    if (words.length > 0) {
      for (const w of words) if (Number.isFinite(w.start)) first = Math.min(first, w.start);
    } else if (seg.text && seg.text.trim().length > 0 && Number.isFinite(seg.start)) {
      first = Math.min(first, seg.start);
    }
  }
  if (first === Infinity && result.text && result.text.trim().length > 0 && (result.segments || []).length === 0) {
    // Text without any timing: cannot tell where it starts; treat the head as covered.
    return 0;
  }
  return first;
}

/**
 * Seconds of voiced audio in [0, untilSec) of `audio`. A frame is voiced when
 * its RMS reaches VOICED_RELATIVE of the loudest frame in the whole buffer and
 * VOICED_ABS_FLOOR, so the check adapts to the input level but ignores silence.
 */
export function voicedHeadSec(audio: Float32Array, sampleRate: number, untilSec: number): number {
  const frame = Math.max(1, Math.round(HEAD_FRAME_SEC * sampleRate));
  const rms: number[] = [];
  for (let off = 0; off < audio.length; off += frame) {
    const end = Math.min(audio.length, off + frame);
    let sum = 0;
    for (let i = off; i < end; i++) sum += audio[i] * audio[i];
    rms.push(Math.sqrt(sum / (end - off)));
  }
  const peak = rms.reduce((a, b) => Math.max(a, b), 0);
  const threshold = Math.max(VOICED_ABS_FLOOR, peak * VOICED_RELATIVE);
  const untilSample = Math.min(audio.length, Math.max(0, Math.floor(untilSec * sampleRate)));
  let voicedSamples = 0;
  for (let f = 0; f * frame < untilSample; f++) {
    if (rms[f] < threshold) continue;
    voicedSamples += Math.min(frame, untilSample - f * frame);
  }
  return voicedSamples / sampleRate;
}

/**
 * HTTP client for the transcription-service.
 * Converts Float32Array audio to WAV, sends as multipart form,
 * and returns transcription results.
 */
export class TranscriptionClient {
  private serviceUrl: string;
  private apiToken: string | undefined;
  private maxRetries: number;
  private retryDelayMs: number;
  private sampleRate: number;
  private maxSpeechDurationSec: number | undefined;
  private minSilenceDurationMs: number | undefined;
  private verifyPromptedHead: boolean;
  /** Requests re-sent without the prompt because the prompted result dropped speech at the head. */
  promptHeadRetries = 0;
  constructor(config: TranscriptionClientConfig) {
    // Ensure serviceUrl ends with the transcriptions endpoint
    this.serviceUrl = config.serviceUrl.replace(/\/+$/, '');
    if (!this.serviceUrl.endsWith('/v1/audio/transcriptions')) {
      this.serviceUrl += '/v1/audio/transcriptions';
    }
    this.apiToken = config.apiToken;
    this.maxRetries = config.maxRetries ?? 3;
    this.retryDelayMs = config.retryDelayMs ?? 1000;
    this.sampleRate = config.sampleRate ?? 16000;
    this.maxSpeechDurationSec = config.maxSpeechDurationSec;
    this.minSilenceDurationMs = config.minSilenceDurationMs;
    this.verifyPromptedHead = config.verifyPromptedHead ?? true;
  }

  /**
   * Transcribe a Float32Array audio buffer.
   * Converts to WAV, POSTs to transcription-service, returns parsed result.
   * Retries on transient failures (503, network errors).
   */
  async transcribe(audioData: Float32Array, language?: string, prompt?: string): Promise<TranscriptionResult> {
    const wavBuffer = this.float32ToWav(audioData);
    const result = await this.transcribeWav(wavBuffer, language, prompt);
    if (!prompt || !this.verifyPromptedHead) return result;

    // The prompt is the previous confirmed text. Whisper treats audio that
    // repeats the end of the prompt as already transcribed and skips it, so a
    // real repetition right after a commit disappears. Detect speech before the
    // first recognized word and re-check that audio without the prompt.
    const firstStart = firstRecognizedStartSec(result);
    const audioSec = audioData.length / this.sampleRate;
    const uncoveredUntil = Math.min(firstStart, audioSec) - HEAD_TOLERANCE_SEC;
    if (uncoveredUntil <= 0) return result;
    const voiced = voicedHeadSec(audioData, this.sampleRate, uncoveredUntil);
    if (voiced < MIN_UNCOVERED_VOICED_SEC) return result;

    this.promptHeadRetries++;
    const shown = Number.isFinite(firstStart) ? `${firstStart.toFixed(2)}s` : 'nothing recognized';
    log(`[TranscriptionClient] Prompted result left ${voiced.toFixed(2)}s of speech unrecognized before the first word (${shown}) — re-transcribing without prompt`);
    let unprompted: TranscriptionResult;
    try {
      unprompted = await this.transcribeWav(wavBuffer, language, undefined);
    } catch (err: any) {
      log(`[TranscriptionClient] Unprompted re-check failed (${err?.message}) — keeping prompted result`);
      return result;
    }
    const altStart = firstRecognizedStartSec(unprompted);
    if (altStart < firstStart - HEAD_TOLERANCE_SEC) {
      log(`[TranscriptionClient] Unprompted result recognizes the head (from ${altStart.toFixed(2)}s) — using it`);
      return unprompted;
    }
    return result;
  }

  /** One request with transient-failure retries. */
  private async transcribeWav(wavBuffer: Buffer, language?: string, prompt?: string): Promise<TranscriptionResult> {
    for (let attempt = 0; attempt <= this.maxRetries; attempt++) {
      try {
        const result = await this.sendRequest(wavBuffer, language, prompt);
        return result;
      } catch (err: any) {
        const isTransient = err.statusCode === 503 || err.statusCode === 429 || err.statusCode === 500 || !err.statusCode;
        const isLastAttempt = attempt === this.maxRetries;

        if (isTransient && !isLastAttempt) {
          const delay = this.retryDelayMs * Math.pow(2, attempt);
          log(`[TranscriptionClient] Transient error (attempt ${attempt + 1}/${this.maxRetries + 1}): ${err.message}. Retrying in ${delay}ms...`);
          await new Promise(resolve => setTimeout(resolve, delay));
          continue;
        }

        // Non-transient error or exhausted retries
        log(`[TranscriptionClient] Transcription failed after ${attempt + 1} attempts: ${err.message}`);
        throw err;
      }
    }

    // Should never reach here, but TypeScript needs it
    throw new Error('Transcription failed: exhausted retries');
  }

  /**
   * Send the WAV buffer to the transcription-service as multipart form data.
   */
  private async sendRequest(wavBuffer: Buffer, language?: string, prompt?: string): Promise<TranscriptionResult> {
    // Build multipart form data manually (no external dependency needed)
    const boundary = `----FormBoundary${Date.now().toString(36)}`;

    const parts: Buffer[] = [];

    // File part
    parts.push(Buffer.from(
      `--${boundary}\r\n` +
      `Content-Disposition: form-data; name="file"; filename="audio.wav"\r\n` +
      `Content-Type: audio/wav\r\n\r\n`
    ));
    parts.push(wavBuffer);
    parts.push(Buffer.from('\r\n'));

    // Model part (required by OpenAI-compatible API)
    parts.push(Buffer.from(
      `--${boundary}\r\n` +
      `Content-Disposition: form-data; name="model"\r\n\r\n` +
      `whisper-1\r\n`
    ));

    // Response format part
    parts.push(Buffer.from(
      `--${boundary}\r\n` +
      `Content-Disposition: form-data; name="response_format"\r\n\r\n` +
      `verbose_json\r\n`
    ));

    // Language part (if specified)
    if (language) {
      parts.push(Buffer.from(
        `--${boundary}\r\n` +
        `Content-Disposition: form-data; name="language"\r\n\r\n` +
        `${language}\r\n`
      ));
    }

    // Request word-level timestamps
    parts.push(Buffer.from(
      `--${boundary}\r\n` +
      `Content-Disposition: form-data; name="timestamp_granularities"\r\n\r\n` +
      `word\r\n`
    ));

    // Max speech segment duration (controls how often Whisper splits segments)
    if (this.maxSpeechDurationSec !== undefined) {
      parts.push(Buffer.from(
        `--${boundary}\r\n` +
        `Content-Disposition: form-data; name="max_speech_duration_s"\r\n\r\n` +
        `${this.maxSpeechDurationSec}\r\n`
      ));
    }

    // Min silence duration for VAD segment splitting (lower = more splits at natural pauses)
    if (this.minSilenceDurationMs !== undefined) {
      parts.push(Buffer.from(
        `--${boundary}\r\n` +
        `Content-Disposition: form-data; name="min_silence_duration_ms"\r\n\r\n` +
        `${this.minSilenceDurationMs}\r\n`
      ));
    }

    // Prompt: previous confirmed text as context for streaming continuity
    if (prompt) {
      parts.push(Buffer.from(
        `--${boundary}\r\n` +
        `Content-Disposition: form-data; name="prompt"\r\n\r\n` +
        `${prompt}\r\n`
      ));
    }

    // End boundary
    parts.push(Buffer.from(`--${boundary}--\r\n`));

    const body = Buffer.concat(parts);

    const headers: Record<string, string> = {
      'Content-Type': `multipart/form-data; boundary=${boundary}`,
    };
    if (this.apiToken) {
      headers['Authorization'] = `Bearer ${this.apiToken}`;
    }

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 30000);

    try {
      const response = await fetch(this.serviceUrl, {
        method: 'POST',
        headers,
        body,
        signal: controller.signal,
      });

      if (!response.ok) {
        const errorText = await response.text().catch(() => 'Unable to read error response');
        const err: any = new Error(`Transcription service returned HTTP ${response.status}: ${errorText}`);
        err.statusCode = response.status;
        throw err;
      }

      const data = await response.json() as any;

      return {
        text: data.text || '',
        language: data.language || language || 'unknown',
        language_probability: data.language_probability ?? 0,
        duration: data.duration || 0,
        segments: (data.segments || []).map((s: any) => ({
          start: s.start || 0,
          end: s.end || 0,
          text: s.text || '',
          avg_logprob: s.avg_logprob,
          no_speech_prob: s.no_speech_prob,
          compression_ratio: s.compression_ratio,
          words: s.words,
        })),
      };
    } finally {
      clearTimeout(timeoutId);
    }
  }

  /**
   * Convert Float32Array audio samples to a WAV file buffer.
   * Output: 16-bit PCM, mono, at this.sampleRate (default 16kHz).
   */
  private float32ToWav(samples: Float32Array): Buffer {
    const numChannels = 1;
    const bitsPerSample = 16;
    const bytesPerSample = bitsPerSample / 8;
    const dataSize = samples.length * bytesPerSample;
    const headerSize = 44;
    const buffer = Buffer.alloc(headerSize + dataSize);

    // RIFF header
    buffer.write('RIFF', 0);
    buffer.writeUInt32LE(36 + dataSize, 4);
    buffer.write('WAVE', 8);

    // fmt sub-chunk
    buffer.write('fmt ', 12);
    buffer.writeUInt32LE(16, 16);              // Sub-chunk size
    buffer.writeUInt16LE(1, 20);               // PCM format
    buffer.writeUInt16LE(numChannels, 22);     // Mono
    buffer.writeUInt32LE(this.sampleRate, 24);  // Sample rate
    buffer.writeUInt32LE(this.sampleRate * numChannels * bytesPerSample, 28); // Byte rate
    buffer.writeUInt16LE(numChannels * bytesPerSample, 32); // Block align
    buffer.writeUInt16LE(bitsPerSample, 34);   // Bits per sample

    // data sub-chunk
    buffer.write('data', 36);
    buffer.writeUInt32LE(dataSize, 40);

    // Convert Float32 [-1, 1] to Int16
    let offset = headerSize;
    for (let i = 0; i < samples.length; i++) {
      let sample = samples[i];
      // Clamp to [-1, 1]
      sample = Math.max(-1, Math.min(1, sample));
      // Convert to 16-bit integer
      const int16 = sample < 0 ? sample * 0x8000 : sample * 0x7FFF;
      buffer.writeInt16LE(Math.round(int16), offset);
      offset += 2;
    }

    return buffer;
  }
}
