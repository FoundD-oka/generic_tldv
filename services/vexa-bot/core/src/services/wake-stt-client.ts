import { log } from '../utils';

export interface WakeSttClientConfig {
  serviceUrl: string;
  apiToken?: string;
  platform: string;
  nativeMeetingId?: string;
  meetingId?: number;
  sampleRate?: number;
  flushIntervalMs?: number;
  maxBatchDurationMs?: number;
  maxInFlight?: number;
}

interface SpeakerAudioBatch {
  speakerName: string;
  chunks: Float32Array[];
  samples: number;
  firstCapturedAtMs: number;
  traceId: string;
  timer: ReturnType<typeof setTimeout> | null;
}

export class WakeSttClient {
  private serviceUrl: string;
  private apiToken: string | undefined;
  private platform: string;
  private nativeMeetingId: string | undefined;
  private meetingId: number | undefined;
  private sampleRate: number;
  private flushIntervalMs: number;
  private maxBatchSamples: number;
  private maxInFlight: number;
  private batches: Map<string, SpeakerAudioBatch> = new Map();
  private inFlight: Set<Promise<void>> = new Set();
  private closed = false;
  private droppedBatches = 0;
  private sentBatches = 0;

  constructor(config: WakeSttClientConfig) {
    this.serviceUrl = config.serviceUrl.replace(/\/+$/, '');
    if (!this.serviceUrl.endsWith('/v1/audio/ingest')) {
      this.serviceUrl += '/v1/audio/ingest';
    }
    this.apiToken = config.apiToken;
    this.platform = config.platform;
    this.nativeMeetingId = config.nativeMeetingId;
    this.meetingId = config.meetingId;
    this.sampleRate = config.sampleRate ?? 16000;
    this.flushIntervalMs = config.flushIntervalMs ?? 500;
    this.maxBatchSamples = Math.max(
      1,
      Math.floor(this.sampleRate * ((config.maxBatchDurationMs ?? 1200) / 1000))
    );
    this.maxInFlight = config.maxInFlight ?? 4;
  }

  feedAudio(speakerId: string, speakerName: string, audioData: Float32Array): void {
    if (this.closed || audioData.length === 0) return;

    const now = Date.now();
    let batch = this.batches.get(speakerId);
    if (!batch) {
      batch = {
        speakerName,
        chunks: [],
        samples: 0,
        firstCapturedAtMs: now,
        traceId: this.createTraceId(speakerId, now),
        timer: null,
      };
      this.batches.set(speakerId, batch);
    }

    batch.speakerName = speakerName || batch.speakerName || 'Unknown';
    batch.chunks.push(new Float32Array(audioData));
    batch.samples += audioData.length;

    if (batch.samples >= this.maxBatchSamples) {
      this.flushSpeaker(speakerId);
      return;
    }

    if (!batch.timer) {
      batch.timer = setTimeout(() => this.flushSpeaker(speakerId), this.flushIntervalMs);
    }
  }

  async close(): Promise<void> {
    this.closed = true;
    for (const speakerId of Array.from(this.batches.keys())) {
      this.flushSpeaker(speakerId);
    }
    await Promise.allSettled(Array.from(this.inFlight));
  }

  private flushSpeaker(speakerId: string): void {
    const batch = this.batches.get(speakerId);
    if (!batch || batch.samples === 0) return;

    if (batch.timer) {
      clearTimeout(batch.timer);
      batch.timer = null;
    }
    this.batches.delete(speakerId);

    if (this.inFlight.size >= this.maxInFlight) {
      this.droppedBatches++;
      if (this.droppedBatches === 1 || this.droppedBatches % 20 === 0) {
        log(`[WakeSTT] Dropping audio batch because wake-stt is behind (dropped=${this.droppedBatches})`);
      }
      return;
    }

    const audio = concatFloat32(batch.chunks, batch.samples);
    const request = this.sendBatch(speakerId, batch.speakerName, audio, batch.firstCapturedAtMs, batch.traceId)
      .catch((err: any) => {
        log(`[WakeSTT] Ingest failed for ${batch?.speakerName || speakerId}: ${err.message}`);
      });
    this.inFlight.add(request);
    request.finally(() => this.inFlight.delete(request));
  }

  private async sendBatch(
    speakerId: string,
    speakerName: string,
    audio: Float32Array,
    capturedAtMs: number,
    traceId: string,
  ): Promise<void> {
    const durationMs = Math.round((audio.length / this.sampleRate) * 1000);
    const sentAtMs = Date.now();
    const body = {
      platform: this.platform,
      native_meeting_id: this.nativeMeetingId,
      meeting_id: this.meetingId,
      speaker_id: speakerId,
      speaker: speakerName || 'Unknown',
      sample_rate: this.sampleRate,
      audio_format: 'f32le',
      audio_base64: float32ToBase64(audio),
      captured_at_ms: capturedAtMs,
      duration_ms: durationMs,
      wake_trace_id: traceId,
      bot_audio_received_ts_ms: capturedAtMs,
      audio_chunk_sent_to_stt_ts_ms: sentAtMs,
    };

    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    };
    if (this.apiToken) {
      headers['Authorization'] = `Bearer ${this.apiToken}`;
    }

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 2500);
    try {
      const response = await fetch(this.serviceUrl, {
        method: 'POST',
        headers,
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      if (!response.ok) {
        const text = await response.text().catch(() => '');
        throw new Error(`HTTP ${response.status}${text ? `: ${text.slice(0, 120)}` : ''}`);
      }
      this.sentBatches++;
      log(
        `[WakeSTT] Sent audio chunk #${this.sentBatches} trace=${traceId} speaker=${speakerName || speakerId} duration_ms=${durationMs}`
      );
    } finally {
      clearTimeout(timeout);
    }
  }

  private createTraceId(speakerId: string, timestampMs: number): string {
    const meeting = this.nativeMeetingId || String(this.meetingId || 'unknown');
    const random = Math.random().toString(36).slice(2, 8);
    return `${this.platform}:${meeting}:${speakerId}:${timestampMs}:${random}`;
  }
}

function concatFloat32(chunks: Float32Array[], totalSamples: number): Float32Array {
  const combined = new Float32Array(totalSamples);
  let offset = 0;
  for (const chunk of chunks) {
    combined.set(chunk, offset);
    offset += chunk.length;
  }
  return combined;
}

function float32ToBase64(audio: Float32Array): string {
  const buffer = Buffer.from(audio.buffer, audio.byteOffset, audio.byteLength);
  return buffer.toString('base64');
}
