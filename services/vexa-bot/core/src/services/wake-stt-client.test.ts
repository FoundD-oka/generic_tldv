import assert from 'node:assert';
import { WakeSttClient } from './wake-stt-client';

const calls: { url: string; init: RequestInit }[] = [];
const originalFetch = globalThis.fetch;

globalThis.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
  calls.push({ url: String(url), init: init || {} });
  return new Response(JSON.stringify({ ok: true }), { status: 200 });
}) as typeof fetch;

async function main() {
  try {
    const client = new WakeSttClient({
      serviceUrl: 'http://wake-stt:8058',
      apiToken: 'secret',
      platform: 'google_meet',
      nativeMeetingId: 'abc-defg-hij',
      meetingId: 42,
      sampleRate: 16000,
      flushIntervalMs: 10,
      maxBatchDurationMs: 50,
    });

    client.feedAudio('speaker-1', 'Alice', new Float32Array(1600).fill(0.1));
    await client.close();

    assert.strictEqual(calls.length, 1);
    assert.strictEqual(calls[0].url, 'http://wake-stt:8058/v1/audio/ingest');
    assert.strictEqual((calls[0].init.headers as Record<string, string>)['Authorization'], 'Bearer secret');

    const body = JSON.parse(String(calls[0].init.body));
    assert.strictEqual(body.platform, 'google_meet');
    assert.strictEqual(body.native_meeting_id, 'abc-defg-hij');
    assert.strictEqual(body.speaker, 'Alice');
    assert.strictEqual(body.sample_rate, 16000);
    assert.strictEqual(body.audio_format, 'f32le');
    assert.strictEqual(body.duration_ms, 100);
    assert.match(body.wake_trace_id, /^google_meet:abc-defg-hij:speaker-1:\d+:[a-z0-9]+$/);
    assert.strictEqual(typeof body.bot_audio_received_ts_ms, 'number');
    assert.strictEqual(typeof body.audio_chunk_sent_to_stt_ts_ms, 'number');
    assert.ok(body.audio_chunk_sent_to_stt_ts_ms >= body.bot_audio_received_ts_ms);

    console.log('PASS wake-stt-client');
  } finally {
    globalThis.fetch = originalFetch;
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
