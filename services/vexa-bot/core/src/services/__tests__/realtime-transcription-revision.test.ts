/**
 * Regression tests for the integration findings of 2026-09-28
 * (.hw/evidence/realtime-transcription-quality/integration-findings.md):
 *
 *   1. Saved real HTTP responses (faster-whisper small) whose word segmentation
 *      changes between hypotheses ("し" → "します") must not make the manager cut
 *      continuous speech, which lost the sentence ending in the real replay.
 *      A simulated backend with varying segmentation and timestamp jitter checks
 *      the same property on other text.
 *   2. Legitimate Japanese repeats survive the whole confirmation path.
 *   3. Session close waits for onSegmentReady callbacks that are still
 *      publishing a result already handed back; session_end comes last.
 *   4. Teams-style intake: a later chunk of the same speaker cannot overtake a
 *      chunk that is awaiting the "joined" event; closing intake drains queued
 *      chunks into the final flush.
 *
 * Deterministic: fake clock, autoTimers=false, responses delivered by the test.
 * Run: cd services/vexa-bot/core && npx tsx src/services/__tests__/realtime-transcription-revision.test.ts
 */

import assert from 'node:assert';
import * as fs from 'fs';
import * as path from 'path';
import {
  SpeakerStreamManager,
  type SpeakerStreamManagerConfig,
  type SubmissionInfo,
  type WhisperSegment,
  type WhisperWord,
} from '../speaker-streams';
import { SerialAudioIntake } from '../serial-audio-intake';
import { closeTranscriptionSession } from '../transcription-session-close';
import type { TranscriptionSegment } from '../segment-publisher';

const SR = 16000;

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

const settle = async (n = 5) => { for (let i = 0; i < n; i++) await new Promise<void>(r => setImmediate(r)); };

function deferred<T = void>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>(r => { resolve = r; });
  return { promise, resolve };
}

const normalize = (t: string) => t.normalize('NFKC').replace(/[\s\p{P}]/gu, '');

interface Call { speakerId: string; audio: Float32Array; info: SubmissionInfo }

function manager(config?: SpeakerStreamManagerConfig) {
  let now = 1_000_000;
  const mgr = new SpeakerStreamManager({
    sampleRate: SR, minAudioDuration: 1, submitInterval: 1, confirmThreshold: 2,
    maxBufferDuration: 15, idleTimeoutSec: 5, autoTimers: false, now: () => now, ...config,
  });
  const calls: Call[] = [];
  const confirmed: string[] = [];
  mgr.onSegmentConfirmed = (_id, _name, text) => { confirmed.push(text); };
  return { mgr, calls, confirmed, clock: { now: () => now, set: (v: number) => { now = v; } } };
}

// ── 1a. Saved real responses ────────────────────────────────────────────────

interface FixtureResponse { info: SubmissionInfo; text: string; segments: WhisperSegment[] }
const fixture: { responses: FixtureResponse[] } = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'cut-repro-technical.json'), 'utf8'),
);
const TOTAL_SAMPLES = 126835; // technical.wav length (cut-repro.ts)
const words = (r: FixtureResponse) => r.segments.flatMap(s => s.words || []);

/**
 * Timeline of what was said, built from the saved responses: the words of the
 * longest partial up to its first pause, then the final response's words
 * (recorded from sample 47040) shifted to absolute time. The first final word
 * absorbed the audio of the clipped "します", so its start is clamped to the
 * start the partial reported for it.
 */
function truthTimeline(): WhisperWord[] {
  const partial = words(fixture.responses[2]);
  const pauseAt = partial.findIndex((w, i) => i + 1 < partial.length && partial[i + 1].start - w.end >= 0.15);
  const head = partial.slice(0, pauseAt + 1);
  const secondStart = partial[pauseAt + 1].start;
  const finalResp = fixture.responses[3];
  const shift = finalResp.info.startSample / SR;
  const tail = words(finalResp).map(w => ({ ...w, start: Math.max(secondStart, w.start + shift), end: Math.max(secondStart, w.end + shift) }));
  return [...head, ...tail];
}

/**
 * Backend model for the final request: when the audio was cut (start > 0), a
 * word is recognized only when its onset is inside the submitted audio
 * (Whisper dropped "します" when the audio started exactly at its timestamp).
 */
function finalResponse(info: SubmissionInfo): WhisperSegment[] {
  const s = info.startSample / SR;
  const e = info.endSample / SR;
  const ws = truthTimeline()
    .filter(w => (s === 0 || w.start >= s + 0.02) && w.end <= e + 1e-9)
    .map(w => ({ ...w, start: w.start - s, end: w.end - s }));
  if (ws.length === 0) return [];
  return [{ text: ws.map(w => w.word).join(''), start: ws[0].start, end: ws[ws.length - 1].end, words: ws }];
}

async function replaySaved(config?: SpeakerStreamManagerConfig) {
  const h = manager(config);
  h.mgr.onSegmentReady = (speakerId, _n, audio, info) => { h.calls.push({ speakerId, audio, info }); };
  h.mgr.addSpeaker('s', '検証');
  let last = 0;
  const partialEnds = [16384, 49152, 98304];
  for (let i = 0; i < partialEnds.length; i++) {
    const end = partialEnds[i];
    h.clock.set(1_000_000 + end / 16);
    h.mgr.feedAudio('s', new Float32Array(end - last), h.clock.now());
    last = end;
    await h.mgr.tick('s');
    const call = h.calls[h.calls.length - 1];
    const saved = fixture.responses[i];
    assert.strictEqual(call.info.startSample, saved.info.startSample, 'same request identity as the saved run');
    assert.strictEqual(call.info.endSample, saved.info.endSample, 'same request identity as the saved run');
    h.mgr.handleTranscriptionResult('s', saved.text, undefined, saved.segments, { requestId: call.info.requestId });
  }
  const b = (h.mgr as any).buffers.get('s');
  const cutAfterPartials: number = b.confirmedAbs;
  h.clock.set(1_000_000 + TOTAL_SAMPLES / 16);
  h.mgr.feedAudio('s', new Float32Array(TOTAL_SAMPLES - last), h.clock.now());
  h.mgr.onSegmentReady = (speakerId, _n, _audio, info) => {
    const segs = finalResponse(info);
    h.mgr.handleTranscriptionResult(speakerId, segs.map(x => x.text).join(''), undefined, segs, { requestId: info.requestId });
  };
  await h.mgr.flushSpeaker('s', true);
  return { confirmed: h.confirmed, cutAfterPartials, stats: h.mgr.getStats() };
}

// ── 1b. Simulated backend with varying segmentation ─────────────────────────

function prng(seed: number) {
  let x = seed >>> 0 || 1;
  return () => { x ^= x << 13; x >>>= 0; x ^= x >>> 17; x ^= x << 5; x >>>= 0; return x / 0x100000000; };
}

interface TimedChar { ch: string; start: number; end: number }

/** Utterances of continuous characters separated by pauses. */
function script(seed: number): TimedChar[] {
  const rnd = prng(seed);
  const alphabet = 'あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをん会議資料予算確認共有来週担当';
  const out: TimedChar[] = [];
  let t = 0.3;
  for (let u = 0; u < 6; u++) {
    const len = 8 + Math.floor(rnd() * 14);
    for (let i = 0; i < len; i++) {
      const d = 0.1 + rnd() * 0.12;
      out.push({ ch: alphabet[Math.floor(rnd() * alphabet.length)], start: t, end: t + d });
      t += d;
    }
    t += 0.35 + rnd() * 0.5; // pause between utterances
  }
  return out;
}

/**
 * Backend: recognizes characters whose onset is inside the audio (≥ 20 ms
 * after the snapshot start) and that end before the snapshot end; groups them
 * into 1–3 character words differently per request; jitters word timestamps
 * by up to ±40 ms (never across a pause).
 */
function simulatedResponse(chars: TimedChar[], info: SubmissionInfo): WhisperSegment[] {
  const s = info.startSample / SR;
  const e = info.endSample / SR;
  const rnd = prng(info.requestId * 7919 + info.startSample);
  const inside = chars.filter(c => c.start >= s + 0.02 && c.end <= e);
  const ws: WhisperWord[] = [];
  let i = 0;
  while (i < inside.length) {
    let n = 1 + Math.floor(rnd() * 3);
    let j = i;
    const group: TimedChar[] = [];
    while (j < inside.length && n > 0) {
      if (group.length > 0 && inside[j].start - group[group.length - 1].end > 0.05) break; // pause ends a word
      group.push(inside[j]); j++; n--;
    }
    const jit = () => (rnd() - 0.5) * 0.08;
    ws.push({ word: group.map(c => c.ch).join(''), start: group[0].start - s + jit(), end: group[group.length - 1].end - s + jit(), probability: 0.9 });
    i = j;
  }
  // Keep timestamps monotonic and inside the snapshot, as Whisper does
  const snap = e - s;
  for (let k = 0; k < ws.length; k++) {
    ws[k].start = Math.min(snap, Math.max(k > 0 ? ws[k - 1].end : 0, ws[k].start));
    ws[k].end = Math.min(snap, Math.max(ws[k].start, ws[k].end));
    if (k > 0 && ws[k].start - ws[k - 1].end < 0.05) ws[k].start = ws[k - 1].end; // continuous speech touches
  }
  if (ws.length === 0) return [];
  return [{ text: ws.map(w => w.word).join(''), start: ws[0].start, end: ws[ws.length - 1].end, words: ws }];
}

async function runSimulated(seed: number, config?: SpeakerStreamManagerConfig): Promise<{ expected: string; got: string }> {
  const chars = script(seed);
  const total = Math.ceil((chars[chars.length - 1].end + 0.6) * SR);
  const h = manager(config);
  h.mgr.onSegmentReady = (speakerId, _n, _audio, info) => {
    const segs = simulatedResponse(chars, info);
    h.mgr.handleTranscriptionResult(speakerId, segs.map(x => x.text).join(''), undefined, segs, { requestId: info.requestId });
  };
  h.mgr.addSpeaker('s', 'Sim');
  const chunk = 4096;
  let fed = 0;
  let sinceTick = 0;
  while (fed < total) {
    const n = Math.min(chunk, total - fed);
    fed += n;
    h.clock.set(1_000_000 + (fed / SR) * 1000);
    h.mgr.feedAudio('s', new Float32Array(n), h.clock.now());
    sinceTick += n;
    if (sinceTick >= SR) { sinceTick = 0; await h.mgr.tick('s'); }
  }
  await h.mgr.flushSpeaker('s', true);
  return { expected: chars.map(c => c.ch).join(''), got: normalize(h.confirmed.join('')) };
}

async function main(): Promise<void> {
  console.log('\n=== Confirmation boundary: saved real responses (segmentation "し" → "します") ===\n');

  await test('saved responses: continuous speech is not cut, sentence ending is kept, nothing duplicated', async () => {
    const r = await replaySaved();
    const partial = words(fixture.responses[2]);
    const cut = r.cutAfterPartials;
    if (cut > 0) {
      for (let i = 0; i + 1 < partial.length; i++) {
        const a = partial[i];
        const b = partial[i + 1];
        if (b.start - a.end < 0.15) {
          assert.ok(!(cut > a.start * SR && cut < b.end * SR), `cut ${cut} lies inside continuous speech "${a.word}${b.word}"`);
        }
      }
    }
    const got = normalize(r.confirmed.join(''));
    assert.strictEqual(got, normalize(truthTimeline().map(w => w.word).join('')));
    assert.ok(got.includes('改善します'), `sentence ending lost: ${r.confirmed.join(' | ')}`);
    assert.strictEqual(r.stats.lostSamples, 0);
  });

  await test('control: the previous cut rule (no pause, no padding) reproduces the observed loss', async () => {
    const r = await replaySaved({ minCutGapSec: 0, cutPadSec: 0 });
    assert.strictEqual(r.cutAfterPartials, fixture.responses[3].info.startSample, 'same cut as the saved run (47040)');
    assert.ok(!normalize(r.confirmed.join('')).includes('改善します'), 'fixture must still reproduce the defect');
  });

  await test('simulated backend: varying segmentation and jitter never lose or duplicate text', async () => {
    for (let seed = 1; seed <= 20; seed++) {
      const r = await runSimulated(seed);
      assert.strictEqual(r.got, r.expected, `seed ${seed}`);
    }
  });

  console.log('\n=== Legitimate Japanese repeats ===\n');

  await test('three identical Japanese sentences are all confirmed', async () => {
    const h = manager();
    h.mgr.onSegmentReady = (speakerId, _n, _a, info) => {
      const text = '確認してください。確認してください。確認してください。';
      h.mgr.handleTranscriptionResult(speakerId, text, undefined, [
        { text: '確認してください。', start: 0.1, end: 1.0 },
        { text: '確認してください。', start: 1.2, end: 2.1 },
        { text: '確認してください。', start: 2.3, end: 3.2 },
      ], { requestId: info.requestId });
    };
    h.mgr.addSpeaker('s', 'A');
    h.mgr.feedAudio('s', new Float32Array(3.5 * SR), h.clock.now());
    await h.mgr.flushSpeaker('s', true);
    assert.deepStrictEqual(h.confirmed, ['確認してください。', '確認してください。', '確認してください。']);
  });

  console.log('\n=== Session close vs deferred publish ===\n');

  function closeHarness(config?: SpeakerStreamManagerConfig) {
    const h = manager(config);
    const events: string[] = [];
    const batches = new Map<string, TranscriptionSegment[]>();
    h.mgr.onSegmentConfirmed = (id, name, text) => {
      if (!batches.has(id)) batches.set(id, []);
      batches.get(id)!.push({ speaker: name, text, start: 0, end: 0, language: 'ja', completed: true } as TranscriptionSegment);
    };
    const publisher = {
      publishTranscript: async (_s: string, confirmed: TranscriptionSegment[], _pending: TranscriptionSegment[]) => { events.push(`publish:${confirmed.map(c => c.text).join('+')}`); },
      publishSessionEnd: async () => { events.push('session_end'); },
      close: async () => { events.push('close'); },
    };
    /** Mirrors index.ts onSegmentReady: hand the result back, then publish the batch. */
    const publishAfter = async (id: string, gate: Promise<void>) => {
      const batch = batches.get(id) || [];
      batches.set(id, []);
      await gate;
      if (batch.length) await publisher.publishTranscript(id, batch, []);
    };
    const take = () => { const copy = new Map(batches); batches.clear(); return copy; };
    return { h, events, publisher, publishAfter, take };
  }

  await test('session_end waits for a final result that is still being published', async () => {
    const c = closeHarness();
    const gate = deferred();
    c.h.mgr.onSegmentReady = async (id, _n, _a, info) => {
      c.h.mgr.handleTranscriptionResult(id, '終了時の重要な発言', 1, [{ text: '終了時の重要な発言', start: 0, end: 1 }], { requestId: info.requestId });
      await c.publishAfter(id, gate.promise);
    };
    c.h.mgr.addSpeaker('s', '検証');
    c.h.mgr.feedAudio('s', new Float32Array(2 * SR), c.h.clock.now());
    let closed = false;
    const done = closeTranscriptionSession({ manager: c.h.mgr, publisher: c.publisher, finalFlushTimeoutMs: 5000, takeConfirmedBatches: c.take, log: () => {} })
      .then(s => { closed = true; return s; });
    await settle(20);
    assert.strictEqual(closed, false, 'close must not finish while the publish is pending');
    assert.deepStrictEqual(c.events, [], 'no session_end before the pending publish');
    gate.resolve();
    const summary = await done;
    assert.deepStrictEqual(c.events, ['publish:終了時の重要な発言', 'session_end', 'close']);
    assert.strictEqual(summary!.timedOut, false);
    assert.strictEqual(summary!.pendingCallbacks, 0);
  });

  await test('partial result still publishing while the final flush runs: both published before session_end, no cyclic wait', async () => {
    const c = closeHarness({ confirmThreshold: 1 });
    const answerPartial = deferred();
    const partialGate = deferred();
    const modes: string[] = [];
    c.h.mgr.onSegmentReady = async (id, _n, _a, info) => {
      modes.push(info.mode);
      if (info.mode === 'partial') {
        await answerPartial.promise;
        c.h.mgr.handleTranscriptionResult(id, '前半', undefined, [{ text: '前半', start: 0, end: 0.5 }], { requestId: info.requestId });
        await c.publishAfter(id, partialGate.promise); // slow publish after the result was handed back
        return;
      }
      c.h.mgr.handleTranscriptionResult(id, '後半です', undefined, [{ text: '後半です', start: 0.1, end: 0.9 }], { requestId: info.requestId });
      await c.publishAfter(id, Promise.resolve());
    };
    c.h.mgr.addSpeaker('s', '検証');
    c.h.mgr.feedAudio('s', new Float32Array(2 * SR), c.h.clock.now());
    await c.h.mgr.tick('s');
    const done = closeTranscriptionSession({ manager: c.h.mgr, publisher: c.publisher, finalFlushTimeoutMs: 5000, takeConfirmedBatches: c.take, log: () => {} });
    await settle();
    assert.deepStrictEqual(modes, ['partial'], 'final waits for the in-flight partial result');
    answerPartial.resolve();
    await settle(20);
    // The partial's result let the final flush proceed although its publish is still pending
    assert.deepStrictEqual(modes, ['partial', 'final']);
    assert.deepStrictEqual(c.events, ['publish:後半です'], 'no session_end while the partial is still publishing');
    partialGate.resolve();
    const summary = await done;
    assert.deepStrictEqual(c.events, ['publish:後半です', 'publish:前半', 'session_end', 'close']);
    assert.strictEqual(summary!.timedOut, false);
    assert.strictEqual(summary!.lostSec, 0);
  });

  await test('a publish that never finishes is bounded by the final-flush timeout and reported', async () => {
    const c = closeHarness();
    c.h.mgr.onSegmentReady = async (id, _n, _a, info) => {
      c.h.mgr.handleTranscriptionResult(id, '届かない配信', 1, [{ text: '届かない配信', start: 0, end: 1 }], { requestId: info.requestId });
      await new Promise<void>(() => {});
    };
    c.h.mgr.addSpeaker('s', '検証');
    c.h.mgr.feedAudio('s', new Float32Array(SR), c.h.clock.now());
    const summary = await closeTranscriptionSession({ manager: c.h.mgr, publisher: c.publisher, finalFlushTimeoutMs: 50, takeConfirmedBatches: c.take, log: () => {} });
    assert.strictEqual(summary!.timedOut, true);
    assert.strictEqual(summary!.pendingCallbacks, 1);
    assert.deepStrictEqual(c.events.slice(-2), ['session_end', 'close']);
  });

  console.log('\n=== Teams-style audio intake ordering ===\n');

  /** Mirrors index.ts processTeamsAudioData: add speaker, await "joined", then feed. */
  function teamsIntake(useIntake: boolean) {
    const h = manager();
    const intake = new SerialAudioIntake();
    const fedFirstSample: number[] = [];
    const joined = deferred();
    const origFeed = h.mgr.feedAudio.bind(h.mgr);
    h.mgr.feedAudio = (id: string, data: Float32Array, t?: number) => { fedFirstSample.push(data[0]); origFeed(id, data, t); };
    const process = async (id: string, data: Float32Array) => {
      if (!h.mgr.hasSpeaker(id)) {
        h.mgr.addSpeaker(id, id);
        await joined.promise;
      }
      h.mgr.feedAudio(id, data);
    };
    const handle = (id: string, data: Float32Array) => useIntake ? intake.enqueue(id, () => process(id, data)) : process(id, data);
    return { h, intake, fedFirstSample, joined, handle };
  }
  const chunkOf = (v: number) => new Float32Array(4096).fill(v);

  await test('same-speaker chunks keep arrival order while the first awaits the joined event', async () => {
    const t = teamsIntake(true);
    const p1 = t.handle('teams-A', chunkOf(1));
    const p2 = t.handle('teams-A', chunkOf(2));
    await settle();
    assert.deepStrictEqual(t.fedFirstSample, [], 'nothing fed before the joined event');
    t.joined.resolve();
    await Promise.all([p1, p2]);
    assert.deepStrictEqual(t.fedFirstSample, [1, 2]);
  });

  await test('control: without serialization the second chunk overtakes the first', async () => {
    const t = teamsIntake(false);
    const p1 = t.handle('teams-A', chunkOf(1));
    const p2 = t.handle('teams-A', chunkOf(2));
    t.joined.resolve();
    await Promise.all([p1, p2]);
    assert.deepStrictEqual(t.fedFirstSample, [2, 1], 'the race exists without the intake queue');
  });

  await test('close stops intake, queued chunks drain into the final flush before session_end', async () => {
    const t = teamsIntake(true);
    const events: string[] = [];
    t.h.mgr.onSegmentReady = (id, _n, audio, info) => {
      events.push(`final:${audio.length}`);
      t.h.mgr.handleTranscriptionResult(id, '最後の発言', undefined, [{ text: '最後の発言', start: 0, end: 0.2 }], { requestId: info.requestId });
    };
    t.h.mgr.onSegmentConfirmed = (_id, _n, text) => { events.push(`confirmed:${text}`); };
    void t.handle('teams-A', chunkOf(1));
    void t.handle('teams-A', chunkOf(2));
    // cleanup order in index.ts: close intake → drain → close session
    t.intake.close();
    void t.handle('teams-A', chunkOf(3)); // arrives after close: rejected
    const drained = t.intake.drain(1000);
    await settle();
    t.joined.resolve();
    assert.strictEqual(await drained, true);
    await closeTranscriptionSession({
      manager: t.h.mgr,
      publisher: {
        publishTranscript: async () => { events.push('publish'); },
        publishSessionEnd: async () => { events.push('session_end'); },
        close: async () => { events.push('close'); },
      },
      finalFlushTimeoutMs: 1000,
      takeConfirmedBatches: () => new Map(),
      log: () => {},
    });
    assert.deepStrictEqual(t.fedFirstSample, [1, 2], 'queued chunks fed, post-close chunk rejected');
    assert.deepStrictEqual(events, ['final:8192', 'confirmed:最後の発言', 'session_end', 'close']);
  });

  console.log(`\n=== Results: ${passed} passed, ${failed} failed ===\n`);
  if (failed > 0) {
    console.log(`Failed: ${failures.join(', ')}`);
    process.exit(1);
  }
}

main().catch(err => {
  console.error(err);
  process.exit(1);
});
