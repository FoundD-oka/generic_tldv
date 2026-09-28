/**
 * Regression: a legitimately repeated phrase right after a commit must not be
 * dropped because the next request is prompted with the committed text.
 *
 * Fixture (fixtures/prompt-repeat-drop.json) is the real failure from the
 * isolated faster-whisper small replay: '違います。違います。承認はまだです。'
 * became '違います。承認はまだです。' with confirmThreshold=1. Whisper skips audio
 * that repeats the end of its prompt; the same audio without the prompt is
 * recognized. Audio is synthesized from the recorded 10 ms RMS envelope and the
 * server is replaced by the recorded real responses, so the test is
 * deterministic and needs no network.
 *
 * Run: cd services/vexa-bot/core && npx tsx src/services/__tests__/prompt-repeat-retention.test.ts
 */

import assert from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';
import { SpeakerStreamManager } from '../speaker-streams';
import { TranscriptionClient, firstRecognizedStartSec, voicedHeadSec } from '../transcription-client';

const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'prompt-repeat-drop.json'), 'utf8'));
const SR: number = fixture.sampleRate;
const BASE: number = fixture.startSample;

let passed = 0;
let failed = 0;
const failures: string[] = [];

async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try {
    await fn();
    passed++;
    console.log(`  PASS  ${name}`);
  } catch (err: any) {
    failed++;
    failures.push(name);
    console.log(`  FAIL  ${name}`);
    console.log(`        ${err?.stack?.split('\n').slice(0, 12).join('\n        ') ?? err}`);
  }
}

/** Signal with the recorded envelope: a 220 Hz tone at each 10 ms frame's RMS. */
function synthesize(): Float32Array {
  const frame: number = fixture.frameSamples;
  const out = new Float32Array(fixture.endSample - fixture.startSample);
  for (let i = 0; i < out.length; i++) {
    const r = fixture.rms[Math.floor(i / frame)] ?? 0;
    out[i] = r * Math.SQRT2 * Math.sin((2 * Math.PI * 220 * i) / SR);
  }
  return out;
}
const AUDIO = synthesize();
/** Audio of absolute benchmark samples [start, end). */
const slice = (start: number, end: number) => AUDIO.slice(start - BASE, end - BASE);

interface SeenRequest { prompt: string | undefined; samples: number }

function field(body: string, name: string): string | undefined {
  const marker = `name="${name}"\r\n\r\n`;
  const at = body.indexOf(marker);
  if (at < 0) return undefined;
  const from = at + marker.length;
  return body.slice(from, body.indexOf('\r\n', from));
}

/**
 * Replace fetch with a server that answers from `pick(prompt, samples)`.
 * Returns the requests seen and a restore function.
 */
function fakeServer(pick: (prompt: string | undefined, samples: number) => any | { status: number }) {
  const seen: SeenRequest[] = [];
  const original = globalThis.fetch;
  (globalThis as any).fetch = async (_url: string, init: any) => {
    const buf: Buffer = init.body;
    const text = buf.toString('utf8');
    const riff = buf.indexOf('RIFF');
    const samples = buf.readUInt32LE(riff + 40) / 2;
    const prompt = field(text, 'prompt');
    seen.push({ prompt, samples });
    const r = pick(prompt, samples);
    if (typeof r?.status === 'number') {
      return { ok: false, status: r.status, text: async () => 'fake failure', json: async () => ({}) } as any;
    }
    return { ok: true, status: 200, json: async () => ({ text: r.text, language: 'ja', language_probability: 1, duration: samples / SR, segments: r.segments }) } as any;
  };
  return { seen, restore: () => { (globalThis as any).fetch = original; } };
}

const R = fixture.responses;
const client = (extra: Record<string, unknown> = {}) =>
  new TranscriptionClient({ serviceUrl: 'http://fake', maxRetries: 0, ...extra });
const norm = (t: string) => t.normalize('NFKC').replace(/[\p{P}\p{Z}\s]/gu, '');

async function main() {
  console.log('\n  prompt-repeat retention (real failure fixture)\n');

  await test('fixture reproduces the drop: prompted tail misses the repeated 違います, unprompted keeps it', () => {
    assert.strictEqual(norm(R.tailPrompted.text), '承認はまだです');
    assert.ok(norm(R.tailUnprompted.text).startsWith('違います承認'));
    // The dropped phrase is audible at the head of the request, before the first prompted word.
    const audio = slice(R.tailPrompted.requestStartSample, R.tailPrompted.requestEndSample);
    const first = firstRecognizedStartSec(R.tailPrompted);
    assert.ok(first > 0.7, `first prompted word at ${first}`);
    assert.ok(voicedHeadSec(audio, SR, first - 0.1) > 0.3);
  });

  await test('prompted result that drops head speech is re-checked without prompt and the unprompted result is used', async () => {
    const srv = fakeServer(prompt => (prompt ? R.tailPrompted : R.tailUnprompted));
    try {
      const c = client();
      const r = await c.transcribe(slice(R.tailPrompted.requestStartSample, R.tailPrompted.requestEndSample), 'ja', '違います。');
      assert.ok(norm(r.text).startsWith('違います承認はまだです'), r.text);
      assert.deepStrictEqual(srv.seen.map(s => s.prompt), ['違います。', undefined]);
      assert.strictEqual(c.promptHeadRetries, 1);
    } finally { srv.restore(); }
  });

  await test('prompted result that covers the head is used as is (one request)', async () => {
    const srv = fakeServer(() => R.tailOtherPrompt);
    try {
      const c = client();
      const r = await c.transcribe(slice(R.tailOtherPrompt.requestStartSample, R.tailOtherPrompt.requestEndSample), 'ja', '重複を確認してください。');
      assert.strictEqual(r.text, R.tailOtherPrompt.text);
      assert.strictEqual(srv.seen.length, 1);
      assert.strictEqual(c.promptHeadRetries, 0);
    } finally { srv.restore(); }
  });

  await test('silence before the first word is not a dropped head (no retry)', async () => {
    const srv = fakeServer(prompt => (prompt ? R.tailPrompted : R.tailUnprompted));
    try {
      const audio = slice(R.tailPrompted.requestStartSample, R.tailPrompted.requestEndSample);
      const first = firstRecognizedStartSec(R.tailPrompted);
      audio.fill(0, 0, Math.floor((first - 0.05) * SR));
      const c = client();
      const r = await c.transcribe(audio, 'ja', '違います。');
      assert.strictEqual(r.text, R.tailPrompted.text);
      assert.strictEqual(srv.seen.length, 1);
    } finally { srv.restore(); }
  });

  await test('without a prompt the result is never re-requested', async () => {
    const srv = fakeServer(() => R.tailPrompted);
    try {
      const r = await client().transcribe(slice(R.tailPrompted.requestStartSample, R.tailPrompted.requestEndSample), 'ja');
      assert.strictEqual(r.text, R.tailPrompted.text);
      assert.strictEqual(srv.seen.length, 1);
    } finally { srv.restore(); }
  });

  await test('unprompted re-check that recognizes nothing earlier keeps the prompted result', async () => {
    const srv = fakeServer(() => R.tailPrompted);
    try {
      const r = await client().transcribe(slice(R.tailPrompted.requestStartSample, R.tailPrompted.requestEndSample), 'ja', '違います。');
      assert.strictEqual(r.text, R.tailPrompted.text);
      assert.strictEqual(srv.seen.length, 2);
    } finally { srv.restore(); }
  });

  await test('failed unprompted re-check keeps the prompted result instead of failing the request', async () => {
    const srv = fakeServer(prompt => (prompt ? R.tailPrompted : { status: 500 }));
    try {
      const r = await client().transcribe(slice(R.tailPrompted.requestStartSample, R.tailPrompted.requestEndSample), 'ja', '違います。');
      assert.strictEqual(r.text, R.tailPrompted.text);
      assert.strictEqual(srv.seen.length, 2);
    } finally { srv.restore(); }
  });

  await test('empty prompted result on speech is re-checked without prompt', async () => {
    const srv = fakeServer(prompt => (prompt ? { text: '', segments: [] } : R.tailUnprompted));
    try {
      const r = await client().transcribe(slice(R.tailPrompted.requestStartSample, R.tailPrompted.requestEndSample), 'ja', '違います。');
      assert.ok(norm(r.text).startsWith('違います'), r.text);
    } finally { srv.restore(); }
  });

  await test('verifyPromptedHead=false keeps the previous single-request behaviour', async () => {
    const srv = fakeServer(prompt => (prompt ? R.tailPrompted : R.tailUnprompted));
    try {
      const r = await client({ verifyPromptedHead: false }).transcribe(slice(R.tailPrompted.requestStartSample, R.tailPrompted.requestEndSample), 'ja', '違います。');
      assert.strictEqual(r.text, R.tailPrompted.text);
      assert.strictEqual(srv.seen.length, 1);
    } finally { srv.restore(); }
  });

  /**
   * End-to-end through SpeakerStreamManager with confirmThreshold=1, as in the
   * failing replay: the first request commits '…ください。' + '違います。' and cuts
   * in the pause; the final request is prompted with '違います。'.
   */
  async function replay(verifyPromptedHead: boolean) {
    const firstLen = R.firstPrompted.requestEndSample - R.firstPrompted.requestStartSample;
    const srv = fakeServer((prompt, samples) => {
      if (samples === firstLen) return R.firstPrompted;
      if (!prompt) return R.tailUnprompted;
      return norm(prompt).endsWith('違います') ? R.tailPrompted : R.tailOtherPrompt;
    });
    let now = 1_000_000;
    const c = client({ verifyPromptedHead });
    const mgr = new SpeakerStreamManager({ sampleRate: SR, minAudioDuration: 1, submitInterval: 1, confirmThreshold: 1, maxBufferDuration: 15, idleTimeoutSec: 5, autoTimers: false, now: () => now });
    const emitted: { text: string; b: number; e: number }[] = [];
    const jobs: Promise<void>[] = [];
    mgr.onSegmentReady = (id, _n, samples, info) => {
      const job = (async () => {
        try {
          const r = await c.transcribe(samples, 'ja', mgr.getLastConfirmedText(id) || undefined);
          mgr.handleTranscriptionResult(id, r.text, r.segments.at(-1)?.end, r.segments, { requestId: info.requestId });
        } catch (e) { mgr.handleTranscriptionError(id, e, { requestId: info.requestId }); }
      })();
      jobs.push(job);
      return job;
    };
    mgr.onSegmentConfirmed = (_id, _n, text, b, e) => emitted.push({ text, b, e });
    mgr.addSpeaker('s', 'speaker');
    const startMs = now;
    const feed = (from: number, to: number) => {
      for (let off = from; off < to; off += 4096) {
        const end = Math.min(to, off + 4096);
        now = startMs + ((end - BASE) / SR) * 1000;
        mgr.feedAudio('s', slice(off, end), now);
      }
    };
    try {
      feed(R.firstPrompted.requestStartSample, R.firstPrompted.requestEndSample);
      await mgr.tick('s');
      await Promise.all(jobs);
      feed(R.firstPrompted.requestEndSample, R.tailPrompted.requestEndSample);
      const summary = await mgr.finalizeAll(5000);
      return { emitted, summary, seen: srv.seen, stats: mgr.getStats() };
    } finally { srv.restore(); }
  }

  await test('control: without the re-check the repeated 違います is lost (the reported failure)', async () => {
    const r = await replay(false);
    assert.strictEqual(norm(r.emitted.map(e => e.text).join('')), '重複を確認してください違います承認はまだです');
  });

  await test('confirmThreshold=1 replay keeps both 違います in order with monotonic times and nothing lost', async () => {
    const r = await replay(true);
    const texts = r.emitted.map(e => e.text);
    assert.strictEqual(norm(texts.join('')), '重複を確認してください違います違います承認はまだです', texts.join(' | '));
    for (let i = 1; i < r.emitted.length; i++) {
      assert.ok(r.emitted[i].b >= r.emitted[i - 1].e - 1, `segment ${i} starts before the previous one ended`);
    }
    // The second 違います is timed where it is spoken (≈29.07s in the stream), after the first (≈28.07-28.69s).
    const secondIdx = texts.findIndex((t, i) => i > 0 && norm(t).startsWith('違います') && texts.slice(0, i).some(p => norm(p).includes('違います')));
    assert.ok(secondIdx > 0, 'second 違います segment');
    const secondStartSec = (r.emitted[secondIdx].b - 1_000_000) / 1000 + BASE / SR;
    assert.ok(secondStartSec > 28.8 && secondStartSec < 29.3, `second 違います starts at ${secondStartSec}`);
    assert.strictEqual(r.stats.lostSamples, 0);
    assert.strictEqual(r.summary.timedOut, false);
    // The prompted final request was re-checked once without prompt.
    assert.deepStrictEqual(r.seen.map(s => s.prompt), [undefined, '違います。', undefined]);
  });

  console.log(`\n  ${passed} passed, ${failed} failed`);
  if (failed > 0) {
    console.log(`  Failures:\n    - ${failures.join('\n    - ')}`);
    process.exit(1);
  }
}

main().catch(err => { console.error(err); process.exit(1); });
