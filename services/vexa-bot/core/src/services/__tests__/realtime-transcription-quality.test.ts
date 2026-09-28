/**
 * Regression tests for the Whisper live path (Japanese quality repair).
 *
 * Run: cd services/vexa-bot/core && npx tsx src/services/__tests__/realtime-transcription-quality.test.ts
 *
 * Deterministic: fake clock, no interval timers (autoTimers=false, tick() driven),
 * transcription responses are delivered by the test. Audio samples carry their
 * absolute index as value, so every submitted snapshot can be checked for
 * gaps/duplication.
 */

import assert from 'node:assert';
import {
  SpeakerStreamManager,
  type SpeakerStreamManagerConfig,
  type SubmissionInfo,
  type WhisperSegment,
  type TranscriptionHandleResult,
} from '../speaker-streams';
import { isHallucination } from '../hallucination-filter';

const SR = 16000;
const CHUNK = 4096; // 256 ms, same as the browser ScriptProcessor

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

const settle = () => new Promise<void>(resolve => setImmediate(resolve));

interface Call {
  speakerId: string;
  audio: Float32Array;
  info: SubmissionInfo;
  finish: () => void;
}

interface Confirmed {
  speakerId: string;
  text: string;
  startMs: number;
  endMs: number;
  segmentId: string;
}

function harness(config?: SpeakerStreamManagerConfig) {
  let now = 1_000_000;
  const clock = { now: () => now, advance: (ms: number) => { now += ms; } };
  const mgr = new SpeakerStreamManager({
    sampleRate: SR,
    minAudioDuration: 1,
    submitInterval: 1,
    confirmThreshold: 2,
    maxBufferDuration: 15,
    idleTimeoutSec: 5,
    autoTimers: false,
    now: clock.now,
    ...config,
  });
  const calls: Call[] = [];
  const confirmed: Confirmed[] = [];
  const fedEndSample = new Map<string, number>();
  mgr.onSegmentReady = (speakerId, _name, audio, info) => new Promise<void>(finish => {
    calls.push({ speakerId, audio, info, finish });
  });
  mgr.onSegmentConfirmed = (speakerId, _name, text, startMs, endMs, segmentId) => {
    confirmed.push({ speakerId, text, startMs, endMs, segmentId });
  };

  /** Feed `sec` seconds in 256 ms chunks; sample value = absolute sample index. */
  function feed(speakerId: string, sec: number): void {
    let remaining = Math.round(sec * SR);
    while (remaining > 0) {
      const n = Math.min(CHUNK, remaining);
      const start = fedEndSample.get(speakerId) ?? 0;
      const chunk = new Float32Array(n);
      for (let i = 0; i < n; i++) chunk[i] = start + i;
      fedEndSample.set(speakerId, start + n);
      clock.advance((n / SR) * 1000);
      mgr.feedAudio(speakerId, chunk, clock.now());
      remaining -= n;
    }
  }

  function respond(call: Call, text: string, segments?: WhisperSegment[]): TranscriptionHandleResult {
    const r = mgr.handleTranscriptionResult(call.speakerId, text, undefined, segments, { requestId: call.info.requestId });
    call.finish();
    return r;
  }

  function fail(call: Call, message = 'HTTP 503'): void {
    mgr.handleTranscriptionError(call.speakerId, new Error(message), { requestId: call.info.requestId });
    call.finish();
  }

  /** Assert the snapshot is exactly the contiguous audio [startSample, endSample). */
  function assertSnapshot(call: Call): void {
    const { startSample, endSample } = call.info;
    assert.strictEqual(call.audio.length, endSample - startSample, 'snapshot length');
    for (let i = 0; i < call.audio.length; i += 997) {
      assert.strictEqual(call.audio[i], startSample + i, `snapshot sample ${i}`);
    }
    assert.strictEqual(call.audio[call.audio.length - 1], endSample - 1, 'snapshot last sample');
  }

  return { mgr, clock, calls, confirmed, feed, respond, fail, assertSnapshot, fedEnd: (id: string) => fedEndSample.get(id) ?? 0 };
}

/** Build one Whisper segment from [word, start, end] triples (seconds, snapshot-relative). */
function seg(words: Array<[string, number, number]>): WhisperSegment {
  return {
    text: words.map(w => w[0]).join(''),
    start: words[0][1],
    end: words[words.length - 1][2],
    words: words.map(([word, start, end]) => ({ word, start, end, probability: 0.9 })),
  };
}

function unconfirmedSec(mgr: SpeakerStreamManager, id: string): number {
  const b = (mgr as any).buffers.get(id);
  return b ? (b.bufferStartAbs + b.totalSamples - b.confirmedAbs) / SR : 0;
}

async function main(): Promise<void> {
  console.log('\n=== Hallucination filter: Japanese short utterances ===\n');

  await test('Japanese short affirmative/negative/number/date are kept', () => {
    for (const text of ['はい', 'いいえ', 'ええ', '違います', 'そうです', '来週の月曜です', '予算は五万円です', '3時', '2026年9月28日', '15', '１５', '3.5', 'はいはい', 'そうそうそう']) {
      assert.strictEqual(isHallucination(text), false, `"${text}" must not be filtered`);
    }
  });

  await test('existing known hallucinations and silence artifacts are still filtered', () => {
    for (const text of ['', '   ', '。', '…', 'Thank you.', 'you']) {
      assert.strictEqual(isHallucination(text), true, `"${text}" must be filtered`);
    }
  });

  // Revision 2026-09-28: a Japanese phrase blacklist and a text-only character
  // repetition filter were rejected (the requirement is to keep legitimate
  // speech; meeting participants really say these). Text alone is not evidence
  // of a hallucination; silence/noise is handled by VAD and the quality gate.
  await test('Japanese phrases and repeats are kept on text alone (no Japanese blacklist)', () => {
    for (const text of [
      '確認してください。確認してください。確認してください。',
      'ありがとうございます。ありがとうございます。ありがとうございます。ありがとうございます。',
      '次回もお楽しみに。',
      'ご視聴ありがとうございました。',
      'はい、はい、はい',
      'そうですね、そうですね。では次の議題に移りましょう、資料は共有済みです',
    ]) {
      assert.strictEqual(isHallucination(text), false, `"${text}" must not be filtered`);
    }
  });

  await test('English repetition loop filter still works', () => {
    assert.strictEqual(isHallucination('thank you so thank you so thank you so thank you so'), true);
    assert.strictEqual(isHallucination('Hello everyone, welcome to the meeting today.'), false);
  });

  console.log('\n=== SpeakerStreamManager: Japanese confirmation ===\n');

  await test('Japanese: stable head confirmed via word timestamps, tail kept as draft, times follow audio', async () => {
    const h = harness();
    h.mgr.addSpeaker('s1', 'Alice');
    h.feed('s1', 2);
    await h.mgr.tick('s1');
    assert.strictEqual(h.calls.length, 1);
    h.assertSnapshot(h.calls[0]);
    h.respond(h.calls[0], '来週の月曜に', [seg([['来週', 0, 0.5], ['の', 0.5, 0.7], ['月曜', 0.7, 1.3], ['に', 1.3, 1.8]])]);
    assert.strictEqual(h.confirmed.length, 0, 'single hypothesis must not confirm');

    h.feed('s1', 1);
    await h.mgr.tick('s1');
    assert.strictEqual(h.calls.length, 2);
    h.assertSnapshot(h.calls[1]);
    assert.strictEqual(h.calls[1].info.startSample, 0);
    h.respond(h.calls[1], '来週の月曜に会議をします。', [seg([['来週', 0, 0.5], ['の', 0.5, 0.7], ['月曜', 0.7, 1.3], ['に', 1.3, 1.5], ['会議', 1.5, 2.0], ['を', 2.0, 2.1], ['します。', 2.1, 2.8]])]);
    assert.strictEqual(h.confirmed.length, 0, 'mid-sentence cut on a short window is deferred (readability)');

    // Revision: the speaker pauses 0.2 s after the sentence (audio is only cut in pauses)
    h.feed('s1', 1);
    await h.mgr.tick('s1');
    const r = h.respond(h.calls[2], '来週の月曜に会議をします。資料は', [
      seg([['来週', 0, 0.5], ['の', 0.5, 0.7], ['月曜', 0.7, 1.3], ['に', 1.3, 1.5], ['会議', 1.5, 2.0], ['を', 2.0, 2.1], ['します。', 2.1, 2.8]]),
      seg([['資料', 3.0, 3.5], ['は', 3.5, 3.7]]),
    ]);
    assert.strictEqual(h.confirmed.length, 1);
    assert.strictEqual(h.confirmed[0].text, '来週の月曜に会議をします。');
    assert.deepStrictEqual(r.pending.map(p => p.text), ['資料は']);

    // Audio time mapping: the first chunk ended at t0+256ms, so sample 0 is at t0.
    const t0 = 1_000_000;
    assert.strictEqual(Math.round(h.confirmed[0].startMs), t0);
    assert.strictEqual(Math.round(h.confirmed[0].endMs), t0 + 2800);
    assert.strictEqual(Math.round(r.pending[0].startMs), t0 + 3000);
    assert.strictEqual(Math.round(r.pending[0].endMs), t0 + 3700);

    // Revision: next snapshot starts inside the pause (2.8 s + half of the 0.2 s
    // gap), not exactly at the word timestamp; nothing lost or duplicated
    h.feed('s1', 1);
    await h.mgr.tick('s1');
    assert.strictEqual(h.calls[3].info.startSample, Math.round(2.9 * SR));
    h.assertSnapshot(h.calls[3]);
    assert.strictEqual(h.calls[3].info.endSample, h.fedEnd('s1'));
  });

  await test('Japanese without word timestamps: falls back to agreed segment boundaries', async () => {
    const h = harness();
    h.mgr.addSpeaker('s1', 'Alice');
    h.feed('s1', 2);
    await h.mgr.tick('s1');
    h.respond(h.calls[0], 'はい、承知しました。 資料', [
      { text: 'はい、承知しました。', start: 0, end: 1.2 },
      { text: '資料', start: 1.4, end: 1.9 },
    ]);
    h.feed('s1', 1);
    await h.mgr.tick('s1');
    h.respond(h.calls[1], 'はい承知しました。 資料を送ります', [
      { text: 'はい承知しました。', start: 0, end: 1.2 },
      { text: '資料を送ります', start: 1.4, end: 2.6 },
    ]);
    assert.deepStrictEqual(h.confirmed.map(c => c.text), ['はい承知しました。'], 'punctuation variance does not block agreement');
    h.feed('s1', 1);
    await h.mgr.tick('s1');
    // Revision: cut in the middle of the 1.2–1.4 s gap between the segments
    assert.strictEqual(h.calls[2].info.startSample, Math.round(1.3 * SR));
  });

  await test('Japanese short utterance "はい" is confirmed at idle finalize', async () => {
    const h = harness();
    h.mgr.addSpeaker('s1', 'Bob');
    h.feed('s1', 0.8);
    await h.mgr.tick('s1');
    assert.strictEqual(h.calls.length, 0, 'below minAudioDuration: no partial submit');
    h.clock.advance(6000);
    const p = h.mgr.tick('s1');
    assert.strictEqual(h.calls.length, 1);
    assert.strictEqual(h.calls[0].info.mode, 'final');
    h.respond(h.calls[0], 'はい', [seg([['はい', 0.1, 0.5]])]);
    await p;
    assert.deepStrictEqual(h.confirmed.map(c => c.text), ['はい']);
    assert.strictEqual(unconfirmedSec(h.mgr, 's1'), 0);
  });

  await test('legitimate repetition: two separate "はい" utterances are both confirmed', async () => {
    const h = harness();
    h.mgr.addSpeaker('s1', 'Bob');
    for (let k = 0; k < 2; k++) {
      h.feed('s1', 0.6);
      const p = h.mgr.flushSpeaker('s1');
      h.respond(h.calls[h.calls.length - 1], 'はい', [seg([['はい', 0.1, 0.5]])]);
      await p;
    }
    assert.deepStrictEqual(h.confirmed.map(c => c.text), ['はい', 'はい']);
    assert.notStrictEqual(h.confirmed[0].segmentId, h.confirmed[1].segmentId);
  });

  await test('re-recognizing identical audio is not counted as agreement', async () => {
    const h = harness();
    h.mgr.addSpeaker('s1', 'Alice');
    h.feed('s1', 2);
    await h.mgr.tick('s1');
    h.respond(h.calls[0], 'テストです。', [seg([['テスト', 0, 0.6], ['です。', 0.6, 1.0]])]);
    await h.mgr.tick('s1');
    assert.strictEqual(h.calls.length, 1, 'no new audio → no resubmission');
    // A duplicate delivery of the same response is stale, not a second vote
    const dup = h.mgr.handleTranscriptionResult('s1', 'テストです。', undefined, [seg([['テスト', 0, 0.6], ['です。', 0.6, 1.0]])], { requestId: h.calls[0].info.requestId });
    assert.strictEqual(dup.status, 'stale');
    assert.strictEqual(h.confirmed.length, 0);
  });

  await test('English words still confirm by agreement (spaced script regression)', async () => {
    const h = harness();
    h.mgr.addSpeaker('s1', 'Carol');
    h.feed('s1', 2);
    await h.mgr.tick('s1');
    h.respond(h.calls[0], 'Hello world. This', [seg([[' Hello', 0, 0.4], [' world.', 0.4, 0.9]]), seg([[' This', 1.2, 1.5]])]);
    h.feed('s1', 1);
    await h.mgr.tick('s1');
    h.respond(h.calls[1], 'Hello world. This is', [seg([[' Hello', 0, 0.4], [' world.', 0.4, 0.9]]), seg([[' This', 1.2, 1.5], [' is', 1.5, 1.7]])]);
    assert.deepStrictEqual(h.confirmed.map(c => c.text), ['Hello world.']);
  });

  console.log('\n=== SpeakerStreamManager: pending audio lifetimes and races ===\n');

  await test('flush during in-flight request: waits, keeps late audio, confirms everything', async () => {
    const h = harness();
    h.mgr.addSpeaker('s1', 'Alice');
    h.feed('s1', 2);
    await h.mgr.tick('s1');
    const first = h.calls[0];
    // Audio keeps arriving while the request is in flight
    h.feed('s1', 1);
    const flushed = h.mgr.flushSpeaker('s1');
    assert.strictEqual(h.calls.length, 1, 'flush must wait for the in-flight request');
    // Audio fed after flush started stays pending for later
    h.feed('s1', 0.5);
    h.respond(first, '今日は', [seg([['今日', 0, 0.5], ['は', 0.5, 0.8]])]);
    await settle();
    assert.strictEqual(h.calls.length, 2, 'final request for the remaining audio');
    const fin = h.calls[1];
    assert.strictEqual(fin.info.mode, 'final');
    assert.strictEqual(fin.info.startSample, 0);
    assert.strictEqual(fin.info.endSample, 3 * SR, 'final covers audio present at flush time only');
    h.assertSnapshot(fin);
    h.respond(fin, '今日はいい天気です', [seg([['今日', 0, 0.5], ['は', 0.5, 0.8], ['いい', 0.9, 1.3], ['天気', 1.3, 2.0], ['です', 2.0, 2.7]])]);
    await flushed;
    assert.deepStrictEqual(h.confirmed.map(c => c.text), ['今日はいい天気です']);
    assert.strictEqual(unconfirmedSec(h.mgr, 's1'), 0.5, 'audio fed during flush is kept');
    assert.strictEqual(h.mgr.getStats().lostSamples, 0);
    await h.mgr.tick('s1');
    assert.strictEqual(h.calls.length, 2, 'below minAudioDuration: kept, not submitted yet');
    h.feed('s1', 0.6);
    await h.mgr.tick('s1');
    assert.strictEqual(h.calls[2].info.startSample, 3 * SR);
    h.assertSnapshot(h.calls[2]);
  });

  await test('response for audio already confirmed / older request is ignored', async () => {
    const h = harness({ requestTimeoutSec: 10 });
    h.mgr.addSpeaker('s1', 'Alice');
    h.feed('s1', 2);
    await h.mgr.tick('s1');
    const slow = h.calls[0];
    h.clock.advance(11_000);
    h.feed('s1', 0.5); // keep speaker active (not idle)
    await h.mgr.tick('s1'); // expires the slow request
    await h.mgr.tick('s1'); // resubmits
    assert.strictEqual(h.calls.length, 2);
    assert.strictEqual(h.calls[1].info.startSample, 0, 'resubmission covers the same audio');
    assert.strictEqual(h.mgr.getStats().timedOutRequests, 1);
    const late = h.respond(slow, '遅い応答', [seg([['遅い', 0, 0.5], ['応答', 0.5, 1.0]])]);
    assert.strictEqual(late.status, 'stale');
    const ok = h.respond(h.calls[1], '正しい応答', [seg([['正しい', 0, 0.5], ['応答', 0.5, 1.0]])]);
    assert.strictEqual(ok.status, 'applied');
    assert.deepStrictEqual(ok.pending.map(p => p.text), ['正しい応答']);
  });

  await test('HTTP failure is not an empty result: audio kept and retried after backoff', async () => {
    const h = harness();
    h.mgr.addSpeaker('s1', 'Alice');
    h.feed('s1', 2);
    await h.mgr.tick('s1');
    h.fail(h.calls[0]);
    assert.strictEqual(unconfirmedSec(h.mgr, 's1'), 2);
    await h.mgr.tick('s1');
    assert.strictEqual(h.calls.length, 1, 'backoff respected');
    h.clock.advance(600);
    await h.mgr.tick('s1');
    assert.strictEqual(h.calls.length, 2, 'retried without requiring new audio');
    assert.strictEqual(h.calls[1].info.startSample, 0);
    h.assertSnapshot(h.calls[1]);
    assert.strictEqual(h.mgr.getStats().requestFailures, 1);
  });

  await test('empty recognition result is success: final with empty text releases audio', async () => {
    const h = harness();
    h.mgr.addSpeaker('s1', 'Alice');
    h.feed('s1', 1);
    const p = h.mgr.flushSpeaker('s1');
    h.respond(h.calls[0], '', []);
    await p;
    assert.strictEqual(unconfirmedSec(h.mgr, 's1'), 0);
    assert.strictEqual(h.confirmed.length, 0);
    assert.strictEqual(h.mgr.getStats().lostSamples, 0);
  });

  await test('callback that settles without answering does not leave the request stuck', async () => {
    const h = harness();
    h.mgr.addSpeaker('s1', 'Alice');
    h.feed('s1', 2);
    await h.mgr.tick('s1');
    h.calls[0].finish(); // resolves without handleTranscriptionResult
    await settle();
    assert.strictEqual(h.mgr.getStats().requestFailures, 1);
    h.clock.advance(600);
    await h.mgr.tick('s1');
    assert.strictEqual(h.calls.length, 2);
  });

  await test('final failure: retried, then loss is reported explicitly (not silent success)', async () => {
    const h = harness({ maxFinalAttempts: 2 });
    h.mgr.addSpeaker('s1', 'Alice');
    h.feed('s1', 1.5);
    const p = h.mgr.flushSpeaker('s1');
    h.fail(h.calls[0]);
    await settle();
    assert.strictEqual(h.calls.length, 2, 'final retried');
    h.fail(h.calls[1]);
    await p;
    assert.strictEqual(h.mgr.getStats().lostSamples, 1.5 * SR);
  });

  await test('hard cap: forced commit keeps the tail and the audio after the cap', async () => {
    const h = harness({ maxBufferDuration: 4 });
    h.mgr.addSpeaker('s1', 'Alice');
    h.feed('s1', 5);
    await h.mgr.tick('s1');
    assert.strictEqual(h.calls[0].info.mode, 'forced');
    assert.strictEqual(h.calls[0].info.endSample, 4 * SR, 'snapshot limited to the cap');
    h.assertSnapshot(h.calls[0]);
    const r = h.respond(h.calls[0], '長い発言が続いていて途中', [seg([['長い', 0, 0.5], ['発言', 0.5, 1.2], ['が', 1.2, 1.4], ['続いて', 1.4, 2.5], ['いて', 2.5, 3.0], ['途中', 3.5, 3.9]])]);
    assert.deepStrictEqual(h.confirmed.map(c => c.text), ['長い発言が続いていて']);
    assert.deepStrictEqual(r.pending.map(p => p.text), ['途中']);
    // Revision: cut inside the 3.0–3.5 s pause (3.0 + min(0.25, 0.3) = 3.25 s)
    assert.strictEqual(unconfirmedSec(h.mgr, 's1'), 1.75, 'cut at 3.25s: 1.75s (tail + post-cap audio) kept');
    await h.mgr.tick('s1');
    assert.strictEqual(h.calls[1].info.startSample, 3.25 * SR);
    assert.strictEqual(h.calls[1].info.endSample, 5 * SR);
    h.assertSnapshot(h.calls[1]);
  });

  await test('backlog bound: pending audio is capped and the drop is counted', async () => {
    const h = harness({ maxBufferDuration: 2, maxPendingDurationSec: 10 });
    h.mgr.addSpeaker('s1', 'Alice');
    h.feed('s1', 12);
    assert.ok(unconfirmedSec(h.mgr, 's1') <= 10.0001);
    assert.strictEqual(h.mgr.getStats().lostSamples, 2 * SR);
  });

  await test('speakers are isolated: request ids do not cross speakers', async () => {
    const h = harness();
    h.mgr.addSpeaker('a', 'Alice');
    h.mgr.addSpeaker('b', 'Bob');
    h.feed('a', 2);
    h.feed('b', 2);
    await h.mgr.tick();
    const ca = h.calls.find(c => c.speakerId === 'a')!;
    const cb = h.calls.find(c => c.speakerId === 'b')!;
    const cross = h.mgr.handleTranscriptionResult('b', 'Aliceの発言', undefined, [seg([['Alice', 0, 0.4], ['の', 0.4, 0.5], ['発言', 0.5, 1.0]])], { requestId: ca.info.requestId });
    assert.strictEqual(cross.status, 'stale');
    assert.strictEqual(h.respond(cb, 'ボブです', [seg([['ボブ', 0, 0.4], ['です', 0.4, 0.8]])]).status, 'applied');
    assert.strictEqual(h.respond(ca, 'アリスです', [seg([['アリス', 0, 0.4], ['です', 0.4, 0.8]])]).status, 'applied');
    assert.deepStrictEqual(h.mgr.getPendingDraft('a').map(d => d.text), ['アリスです']);
    assert.deepStrictEqual(h.mgr.getPendingDraft('b').map(d => d.text), ['ボブです']);
  });

  await test('finalizeAll: waits for in-flight + remaining audio, reports timeout loss', async () => {
    const h = harness();
    h.mgr.addSpeaker('a', 'Alice');
    h.mgr.addSpeaker('b', 'Bob');
    h.feed('a', 2);
    h.feed('b', 0.8); // below minAudioDuration: no partial request for b
    await h.mgr.tick();
    assert.strictEqual(h.calls.length, 1);
    const inflightA = h.calls.find(c => c.speakerId === 'a')!;
    h.feed('a', 0.5);
    const done = h.mgr.finalizeAll(200);
    await settle();
    // b had no request in flight: final request immediately
    const finB = h.calls.find(c => c.speakerId === 'b' && c.info.mode === 'final')!;
    h.respond(finB, 'ボブの最後', [seg([['ボブ', 0, 0.4], ['の', 0.4, 0.5], ['最後', 0.5, 1.0]])]);
    h.respond(inflightA, 'アリス', [seg([['アリス', 0, 0.8]])]);
    await settle();
    const finA = h.calls.find(c => c.speakerId === 'a' && c.info.mode === 'final')!;
    assert.strictEqual(finA.info.endSample, 2.5 * SR);
    // finA is never answered → deadline → reported as lost
    const summary = await done;
    // At the deadline, A's latest draft (covering 0-2s) is committed; the
    // 0.5s fed afterwards was never transcribed and is reported, not hidden.
    assert.deepStrictEqual(h.confirmed.map(c => c.text).sort(), ['アリス', 'ボブの最後']);
    assert.strictEqual(summary.timedOut, true);
    assert.strictEqual(summary.lostSec, 0.5, 'unanswered final audio is reported as lost');
    assert.deepStrictEqual(h.mgr.getActiveSpeakers(), []);
  });

  // Reproduction saved in .hw/evidence/realtime-transcription-quality/finalize-active-flush-repro.ts:
  // finalizeAll joined the running flush and inherited its target, so audio fed
  // between the flush and the close was never submitted and was discarded.
  await test('finalizeAll during an active flush also transcribes audio fed after the flush (saved repro)', async () => {
    const h = harness();
    h.mgr.addSpeaker('s1', 'Alice');
    h.feed('s1', 2);
    const first = h.mgr.flushSpeaker('s1');
    assert.strictEqual(h.calls.length, 1);
    assert.strictEqual(h.calls[0].info.endSample, 2 * SR);
    h.feed('s1', 2);
    const closing = h.mgr.finalizeAll(1000);
    h.respond(h.calls[0], '最初の発話です', [seg([['最初', 0, 0.5], ['の', 0.5, 0.6], ['発話', 0.6, 1.2], ['です', 1.2, 1.8]])]);
    await settle();
    assert.strictEqual(h.calls.length, 2, 'audio fed before close gets its own final request');
    const fin = h.calls[1];
    assert.strictEqual(fin.info.mode, 'final');
    assert.strictEqual(fin.info.reason, 'close');
    assert.strictEqual(fin.info.startSample, 2 * SR);
    assert.strictEqual(fin.info.endSample, 4 * SR);
    h.assertSnapshot(fin);
    h.respond(fin, '追加の発話です', [seg([['追加', 0, 0.5], ['の', 0.5, 0.6], ['発話', 0.6, 1.2], ['です', 1.2, 1.8]])]);
    const summary = await closing;
    await first;
    assert.deepStrictEqual(h.confirmed.map(c => c.text), ['最初の発話です', '追加の発話です']);
    assert.deepStrictEqual(summary, { speakers: 1, lostSec: 0, timedOut: false, pendingCallbacks: 0 });
    assert.strictEqual(h.mgr.getStats().lostSamples, 0);
    assert.strictEqual(h.calls.length, 2, 'no duplicate submission');
  });

  await test('overlapping flushes: the later flush covers audio up to its own call, later audio stays pending', async () => {
    const h = harness();
    h.mgr.addSpeaker('s1', 'Alice');
    h.feed('s1', 1);
    const older = h.mgr.flushSpeaker('s1');
    h.feed('s1', 1);
    const newer = h.mgr.flushSpeaker('s1');
    h.feed('s1', 0.5); // after both flushes: must stay pending
    assert.strictEqual(h.calls.length, 1, 'one request at a time');
    assert.strictEqual(h.calls[0].info.endSample, 1 * SR, 'older flush target unchanged at submission');
    h.respond(h.calls[0], 'はい', [seg([['はい', 0.1, 0.5]])]);
    await settle();
    assert.strictEqual(h.calls.length, 2);
    assert.strictEqual(h.calls[1].info.mode, 'final');
    assert.strictEqual(h.calls[1].info.startSample, 1 * SR);
    assert.strictEqual(h.calls[1].info.endSample, 2 * SR, 'newer flush covers audio present at its call only');
    h.assertSnapshot(h.calls[1]);
    h.respond(h.calls[1], 'いいえ', [seg([['いいえ', 0.1, 0.6]])]);
    await Promise.all([older, newer]);
    assert.deepStrictEqual(h.confirmed.map(c => c.text), ['はい', 'いいえ']);
    assert.strictEqual(unconfirmedSec(h.mgr, 's1'), 0.5, 'audio fed after the newer flush is kept');
    assert.strictEqual(h.calls.length, 2);
    assert.strictEqual(h.mgr.getStats().lostSamples, 0);
    // A later flush alone still finalizes the kept audio
    const again = h.mgr.flushSpeaker('s1');
    assert.strictEqual(h.calls[2].info.startSample, 2 * SR);
    assert.strictEqual(h.calls[2].info.endSample, 2.5 * SR);
    h.respond(h.calls[2], '', []);
    await again;
    assert.strictEqual(unconfirmedSec(h.mgr, 's1'), 0);
  });

  await test('finalizeAll joining a flush without new audio submits nothing extra', async () => {
    const h = harness();
    h.mgr.addSpeaker('s1', 'Alice');
    h.feed('s1', 1);
    const first = h.mgr.flushSpeaker('s1');
    const closing = h.mgr.finalizeAll(1000);
    h.respond(h.calls[0], 'はい', [seg([['はい', 0.1, 0.5]])]);
    const summary = await closing;
    await first;
    assert.strictEqual(h.calls.length, 1);
    assert.deepStrictEqual(summary, { speakers: 1, lostSec: 0, timedOut: false, pendingCallbacks: 0 });
  });

  await test('finalizeAll joining a flush: unanswered extra audio hits the deadline and is reported, nothing hangs', async () => {
    const h = harness();
    h.mgr.addSpeaker('s1', 'Alice');
    h.feed('s1', 2);
    const first = h.mgr.flushSpeaker('s1');
    h.feed('s1', 1);
    const closing = h.mgr.finalizeAll(100);
    h.respond(h.calls[0], '最初です', [seg([['最初', 0, 0.5], ['です', 0.5, 1.0]])]);
    await settle();
    assert.strictEqual(h.calls.length, 2);
    // Second final request never answered
    const summary = await closing;
    await first; // the joined flush must settle too (cancelled on removal)
    assert.strictEqual(summary.timedOut, true);
    assert.strictEqual(summary.lostSec, 1, 'unanswered extra audio is reported as lost, not hidden');
    assert.deepStrictEqual(h.confirmed.map(c => c.text), ['最初です']);
    assert.deepStrictEqual(h.mgr.getActiveSpeakers(), []);
    h.calls[1].finish();
    await settle();
  });

  await test('finalizeAll joining a flush: failing final requests are retried then reported, no infinite loop', async () => {
    const h = harness({ maxFinalAttempts: 2 });
    h.mgr.addSpeaker('s1', 'Alice');
    h.feed('s1', 1);
    const first = h.mgr.flushSpeaker('s1');
    h.feed('s1', 1.5);
    const closing = h.mgr.finalizeAll(1000);
    h.respond(h.calls[0], 'はい', [seg([['はい', 0.1, 0.5]])]);
    await settle();
    assert.strictEqual(h.calls.length, 2);
    h.fail(h.calls[1]);
    await settle();
    assert.strictEqual(h.calls.length, 3, 'final for the extra audio retried');
    assert.strictEqual(h.calls[2].info.startSample, 1 * SR);
    h.fail(h.calls[2]);
    const summary = await closing;
    await first;
    assert.strictEqual(h.calls.length, 3, 'gives up after maxFinalAttempts');
    assert.strictEqual(summary.timedOut, false);
    assert.strictEqual(summary.lostSec, 1.5);
    assert.deepStrictEqual(h.confirmed.map(c => c.text), ['はい']);
  });

  await test('removeSpeaker (sync) commits latest draft and reports untranscribed audio', () => {
    const h = harness();
    h.mgr.addSpeaker('s1', 'Alice');
    h.feed('s1', 2);
    void h.mgr.tick('s1');
    h.respond(h.calls[0], 'お疲れさまです', [seg([['お疲れ', 0, 0.6], ['さま', 0.6, 0.9], ['です', 0.9, 1.3]])]);
    h.feed('s1', 1);
    h.mgr.removeSpeaker('s1');
    assert.deepStrictEqual(h.confirmed.map(c => c.text), ['お疲れさまです']);
    assert.strictEqual(h.mgr.getStats().lostSamples, 1 * SR);
  });

  await test('degenerate word timestamps (all zero) never confirm text without advancing audio', async () => {
    const h = harness();
    h.mgr.addSpeaker('s1', 'Alice');
    const zeroWords = seg([['え', 0, 0], ['っと', 0, 0], ['。', 0, 0]]);
    h.feed('s1', 2);
    await h.mgr.tick('s1');
    h.respond(h.calls[0], 'えっと。', [zeroWords]);
    h.feed('s1', 1);
    await h.mgr.tick('s1');
    h.respond(h.calls[1], 'えっと。', [zeroWords]);
    assert.strictEqual(h.confirmed.length, 0, 'no zero-length cut');
    // Final pass still confirms it exactly once and releases the audio
    const p = h.mgr.flushSpeaker('s1');
    await p;
    assert.deepStrictEqual(h.confirmed.map(c => c.text), ['えっと。']);
    assert.strictEqual(unconfirmedSec(h.mgr, 's1'), 0);
  });

  await test('forced commit with degenerate timestamps still makes progress', async () => {
    const h = harness({ maxBufferDuration: 2 });
    h.mgr.addSpeaker('s1', 'Alice');
    h.feed('s1', 3);
    await h.mgr.tick('s1');
    assert.strictEqual(h.calls[0].info.mode, 'forced');
    h.respond(h.calls[0], 'あのその', [seg([['あの', 0, 0], ['その', 0, 0]])]);
    assert.deepStrictEqual(h.confirmed.map(c => c.text), ['あのその']);
    assert.strictEqual(unconfirmedSec(h.mgr, 's1'), 1);
  });

  await test('audio time: VAD gaps are preserved, bursty delivery does not overlap', async () => {
    const h = harness();
    h.mgr.addSpeaker('s1', 'Alice');
    const t0 = h.clock.now();
    h.mgr.feedAudio('s1', new Float32Array(16000), t0 + 1000);   // 0-1 s audio  -> [t0, t0+1000]
    h.mgr.feedAudio('s1', new Float32Array(16000), t0 + 4000);   // after a 2 s gap -> [t0+3000, t0+4000]
    h.mgr.feedAudio('s1', new Float32Array(8000), t0 + 4000);    // burst, same arrival -> [t0+4000, t0+4500]
    const b = (h.mgr as any).buffers.get('s1');
    assert.deepStrictEqual(b.chunks.map((c: any) => Math.round(c.startMs - t0)), [0, 3000, 4000]);
    assert.strictEqual(Math.round(h.mgr.getBufferStartMs('s1') - t0), 0);
  });

  await test('confirmThreshold=1 still keeps the unstable tail', async () => {
    const h = harness({ confirmThreshold: 1 });
    h.mgr.addSpeaker('s1', 'Alice');
    h.feed('s1', 2);
    await h.mgr.tick('s1');
    const r = h.respond(h.calls[0], 'はい。そう', [seg([['はい。', 0, 0.5]]), seg([['そう', 1.8, 1.95]])]);
    assert.deepStrictEqual(h.confirmed.map(c => c.text), ['はい。']);
    assert.deepStrictEqual(r.pending.map(p => p.text), ['そう']);
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
