/**
 * Streaming VAD regression tests (no ONNX model needed: fake session).
 *
 * Run: cd services/vexa-bot/core && npx tsx src/services/__tests__/vad-streaming.test.ts
 *
 * The fake session returns a speech probability from the window content
 * (|sample| >= 0.5 → speech), and can delay responses to reproduce
 * interleaving between speakers.
 */

import assert from 'node:assert';
import { SileroVAD, VadGate, type StreamingVad, type StreamingVadResult, type VadSpeakerState } from '../vad';

let passed = 0;
let failed = 0;

async function test(name: string, fn: () => Promise<void> | void): Promise<void> {
  try {
    await fn();
    passed++;
    console.log(`  PASS  ${name}`);
  } catch (err: any) {
    failed++;
    console.log(`  FAIL  ${name}`);
    console.log(`        ${err?.stack?.split('\n').slice(0, 8).join('\n        ') ?? err}`);
  }
}

const SPEECH = 0.8;

function fakeVad(delays?: number[]): SileroVAD {
  let call = 0;
  const session = {
    async run(feeds: any) {
      const data = feeds.input.data as Float32Array;
      const window = data.subarray(64);
      let max = 0;
      for (const v of window) max = Math.max(max, Math.abs(v));
      const d = delays ? delays[call++ % delays.length] : 0;
      if (d > 0) await new Promise(r => setTimeout(r, d));
      else await Promise.resolve();
      return {
        output: { data: [max >= 0.5 ? 0.9 : 0.05] },
        stateN: { data: new Float32Array(256) },
      };
    },
  };
  const vad = Object.create(SileroVAD.prototype) as any;
  vad.session = session;
  vad.threshold = 0.5;
  vad.negThreshold = 0.35;
  vad.minSilenceSamples = 4000; // 250 ms
  vad.srTensor = null;
  return vad as SileroVAD;
}

function audio(pattern: Array<[number, number]>): Float32Array {
  const total = pattern.reduce((a, [n]) => a + n, 0);
  const out = new Float32Array(total);
  let o = 0;
  for (const [n, v] of pattern) { out.fill(v, o, o + n); o += n; }
  return out;
}

async function main(): Promise<void> {
  console.log('\n=== SileroVAD streaming ===\n');

  await test('chunk whose speech ends mid-chunk is reported as speech', async () => {
    const vad = fakeVad();
    const st = vad.createSpeakerState();
    // 1024 samples of speech then 7168 samples (448 ms) silence: hysteresis ends inside the chunk
    const r = await vad.processStreamingChunk(audio([[1024, SPEECH], [7168, 0]]), st);
    assert.strictEqual(r.triggered, false, 'speech has ended by the end of the chunk');
    assert.strictEqual(r.speech, true, 'but the chunk contained speech');
    const st2 = vad.createSpeakerState();
    assert.strictEqual(await vad.isSpeechStreaming(audio([[1024, SPEECH], [7168, 0]]), st2), true);
  });

  await test('pure silence is not speech', async () => {
    const vad = fakeVad();
    const st = vad.createSpeakerState();
    const r = await vad.processStreamingChunk(audio([[4096, 0.01]]), st);
    assert.strictEqual(r.speech, false);
  });

  await test('samples that do not fill a window are carried, not skipped', async () => {
    const vad = fakeVad();
    const st = vad.createSpeakerState();
    const results: boolean[] = [];
    // 300-sample chunks: no single chunk holds a 512-sample window
    for (let k = 0; k < 4; k++) {
      results.push((await vad.processStreamingChunk(audio([[300, SPEECH]]), st)).speech);
    }
    assert.ok(results.some(Boolean), 'speech detected across small chunks');
    assert.strictEqual(st.currentSample, 1024, 'two full windows evaluated from 1200 samples');
    assert.strictEqual(st.remainder?.length, 1200 - 1024);
  });

  await test('concurrent speakers do not share input/context buffers', async () => {
    const vad = fakeVad([5, 1, 3, 0, 4, 2]);
    const a = vad.createSpeakerState();
    const b = vad.createSpeakerState();
    const aAudio = audio([[1024, 0.11]]);
    const bAudio = audio([[1024, 0.77]]);
    await Promise.all([vad.processStreamingChunk(aAudio, a), vad.processStreamingChunk(bAudio, b)]);
    assert.ok(a.context.every(v => Math.abs(v - 0.11) < 1e-6), 'speaker A context contains only A audio');
    assert.ok(b.context.every(v => Math.abs(v - 0.77) < 1e-6), 'speaker B context contains only B audio');
  });

  await test('calls on the same state are processed in order', async () => {
    const vad = fakeVad([6, 0, 0, 0]);
    const st = vad.createSpeakerState();
    const first = audio([[512, 0.21]]);
    const second = audio([[512, 0.42]]);
    await Promise.all([vad.processStreamingChunk(first, st), vad.processStreamingChunk(second, st)]);
    assert.strictEqual(st.currentSample, 1024);
    assert.ok(st.context.every(v => Math.abs(v - 0.42) < 1e-6), 'context comes from the later chunk');
  });

  console.log('\n=== VadGate pre/post roll ===\n');

  class ScriptedVad implements StreamingVad {
    constructor(private script: boolean[]) {}
    private i = 0;
    createSpeakerState(): VadSpeakerState {
      return { lstmState: new Float32Array(1), context: new Float32Array(1), triggered: false, tempEnd: 0, currentSample: 0 };
    }
    async processStreamingChunk(): Promise<StreamingVadResult> {
      const speech = this.script[this.i++] ?? false;
      return { speech, triggered: speech, maxProb: speech ? 0.9 : 0 };
    }
  }

  await test('onset pre-roll and ending post-roll are forwarded in order with capture times', async () => {
    //            s1     s2     s3     sp    t1     t2     t3
    const gate = new VadGate(new ScriptedVad([false, false, false, true, false, false, false]), { preRollMs: 500, postRollMs: 500, sampleRate: 16000 });
    const chunks = Array.from({ length: 7 }, (_, k) => audio([[4096, k + 1]]));
    const out: Array<{ value: number; len: number; end: number }[]> = [];
    for (let k = 0; k < chunks.length; k++) {
      const r = await gate.process(chunks[k], 1000 + k * 256);
      out.push(r.chunks.map(c => ({ value: c.data[0], len: c.data.length, end: c.captureEndMs })));
    }
    assert.deepStrictEqual(out[0], []);
    assert.deepStrictEqual(out[1], []);
    assert.deepStrictEqual(out[2], []);
    // pre-roll = 8000 samples: tail of s2 (3904) + s3 (4096), then the speech chunk
    assert.deepStrictEqual(out[3], [
      { value: 2, len: 3904, end: 1256 },
      { value: 3, len: 4096, end: 1512 },
      { value: 4, len: 4096, end: 1768 },
    ]);
    // post-roll = 8000 samples: t1 and t2 forwarded, t3 dropped
    assert.deepStrictEqual(out[4], [{ value: 5, len: 4096, end: 2024 }]);
    assert.deepStrictEqual(out[5], [{ value: 6, len: 4096, end: 2280 }]);
    assert.deepStrictEqual(out[6], []);
  });

  await test('speech resuming during post-roll keeps flowing without duplication', async () => {
    const gate = new VadGate(new ScriptedVad([true, false, true, false, false, false]), { preRollMs: 500, postRollMs: 300, sampleRate: 16000 });
    const lens: number[] = [];
    for (let k = 0; k < 6; k++) {
      const r = await gate.process(audio([[4096, k + 1]]), k * 256);
      lens.push(r.chunks.length);
    }
    // sp, post, sp, post(4800 remaining → 1 chunk), post exhausted, silence
    assert.deepStrictEqual(lens, [1, 1, 1, 1, 1, 0]);
  });

  console.log(`\n=== Results: ${passed} passed, ${failed} failed ===\n`);
  if (failed > 0) process.exit(1);
}

main().catch(err => {
  console.error(err);
  process.exit(1);
});
