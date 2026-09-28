/**
 * Regression tests for live transcript delivery order
 * (.hw/evidence/realtime-transcription-quality/publish-order-request.md).
 *
 * SpeakerStreamManager settles a recognition in handleTranscriptionResult()
 * while the onSegmentReady callback is still publishing it, so the next
 * recognition can settle and publish first. SegmentPublisher.publishTranscript()
 * awaits several Redis commands, so a slow older update could write its pending
 * snapshot after a newer one cleared it (stale draft revived).
 *
 * These tests drive the real SpeakerStreamManager, the real
 * publishTranscriptUpdate / OrderedTranscriptPublisher used by index.ts, the
 * real SegmentPublisher (with an in-memory Redis client that applies commands
 * in issue order, like one connection) and the real closeTranscriptionSession.
 *
 * Run: cd services/vexa-bot/core && npx tsx src/services/__tests__/transcript-publish-order.test.ts
 */

import assert from 'node:assert';
import { SpeakerStreamManager, type WhisperSegment } from '../speaker-streams';
import { SegmentPublisher, type TranscriptionSegment } from '../segment-publisher';
import { LiveTranscriptDelivery, OrderedTranscriptPublisher } from '../transcript-publish-order';
import { closeTranscriptionSession, type SessionClosePublisher } from '../transcription-session-close';

const SR = 16000;
const MEETING = '42';

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

const settle = async (n = 10) => { for (let i = 0; i < n; i++) await new Promise<void>(r => setImmediate(r)); };

function deferred<T = void>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>(r => { resolve = r; });
  return { promise, resolve };
}

/**
 * In-memory Redis: every command takes effect when issued (one connection
 * processes commands in the order they were sent); the reply of a command can
 * be held to model latency.
 */
class FakeRedis {
  readonly keys = new Map<string, string>();
  readonly stream: any[] = [];
  readonly messages: any[] = [];
  readonly log: string[] = [];
  disconnected = false;
  /** Returns a promise to delay the reply of this command, or null. */
  hold: (cmd: string, arg: any) => Promise<void> | null = () => null;

  private reply<T>(cmd: string, arg: any, value: T): Promise<T> {
    const h = this.hold(cmd, arg);
    return h ? h.then(() => value) : Promise.resolve(value);
  }
  xAdd(_key: string, _id: string, fields: { payload: string }) {
    const payload = JSON.parse(fields.payload);
    this.stream.push(payload);
    this.log.push(payload.type === 'transcription' ? `xadd:${payload.segments[0].text}` : `xadd:${payload.type}`);
    return this.reply('xAdd', payload, '0-1');
  }
  set(key: string, value: string) {
    this.keys.set(key, value);
    this.log.push(`set:${JSON.parse(value).map((s: any) => s.text).join('+')}`);
    return this.reply('set', value, 'OK');
  }
  del(key: string) {
    this.keys.delete(key);
    this.log.push('del');
    return this.reply('del', key, 1);
  }
  publish(_channel: string, message: string) {
    this.messages.push(JSON.parse(message));
    this.log.push('publish');
    return this.reply('publish', message, 1);
  }
  async disconnect() { this.disconnected = true; this.log.push('disconnect'); }
  on() { return this; }
  pendingTexts(speaker: string): string[] | null {
    const v = this.keys.get(`meeting:${MEETING}:pending:${speaker}`);
    return v === undefined ? null : JSON.parse(v).map((s: any) => s.text);
  }
}

function realPublisher(redis: FakeRedis): SegmentPublisher {
  const p = new SegmentPublisher({ redisUrl: 'redis://unused', meetingId: MEETING, token: 't', sessionUid: 'sess', platform: 'google_meet' });
  (p as any).client = redis;
  (p as any).connected = true;
  return p;
}

/** Responses for three partial requests: request 2 confirms a sentence, request 3 is silence. */
const RESPONSES: WhisperSegment[][] = [
  [{ text: '最初の文です。', start: 0, end: 0.8 }, { text: '次は', start: 0.9, end: 1.0 }],
  [{ text: '最初の文です。', start: 0, end: 0.8 }, { text: '次は議題です', start: 0.9, end: 1.9 }],
  [],
];

type Response = WhisperSegment[] | 'discard';

interface PipelineOptions {
  responses?: Response[];
  maxBufferDuration?: number;
}

/**
 * Pipeline wired like initPerSpeakerPipeline in index.ts: onSegmentConfirmed
 * collects into confirmedBatches and calls LiveTranscriptDelivery
 * .confirmedOutsideResult(); onSegmentReady hands the result back and then
 * publishes through LiveTranscriptDelivery.afterResult() (no await in
 * between); a discarded result goes through afterDiscard() like discard().
 */
function pipeline(publisher: SessionClosePublisher, opts: PipelineOptions = {}) {
  const responses = opts.responses ?? RESPONSES;
  let now = 1_000_000;
  const mgr = new SpeakerStreamManager({
    sampleRate: SR, minAudioDuration: 1, submitInterval: 1, confirmThreshold: 2,
    maxBufferDuration: opts.maxBufferDuration ?? 15, idleTimeoutSec: 5, autoTimers: false, now: () => now,
  });
  let confirmedBatches = new Map<string, TranscriptionSegment[]>();
  const settled: { request: number; pending: string[] }[] = [];
  const delivery = new LiveTranscriptDelivery({
    publisher,
    sessionStartMs: 1_000_000,
    peekConfirmedCount: (id: string) => confirmedBatches.get(id)?.length ?? 0,
    takeConfirmed: (id: string) => { const b = confirmedBatches.get(id) || []; confirmedBatches.set(id, []); return b; },
    identity: () => ({}),
    pendingDraft: id => mgr.getPendingDraft(id),
    language: () => 'ja',
  });
  mgr.onSegmentConfirmed = (id, name, text, startMs, endMs, segmentId) => {
    if (!confirmedBatches.has(id)) confirmedBatches.set(id, []);
    confirmedBatches.get(id)!.push({
      speaker: name, text, start: (startMs - 1_000_000) / 1000, end: (endMs - 1_000_000) / 1000,
      language: 'ja', completed: true, segment_id: `sess:${segmentId}`,
    });
    delivery.confirmedOutsideResult(id, name);
  };
  let calls = 0;
  mgr.onSegmentReady = async (id, name, _audio, info) => {
    const n = ++calls;
    const r = responses[Math.min(n - 1, responses.length - 1)];
    if (r === 'discard') {
      const handled = mgr.handleTranscriptionResult(id, '', undefined, undefined, { requestId: info.requestId });
      settled.push({ request: n, pending: handled.pending.map(p => p.text) });
      await delivery.afterDiscard(id, name, handled, 'ja', info.mode !== 'partial');
      return;
    }
    const text = r.map(s => s.text).join('');
    const handled = mgr.handleTranscriptionResult(id, text, r.length ? r[r.length - 1].end : undefined, r, { requestId: info.requestId });
    settled.push({ request: n, pending: handled.pending.map(p => p.text) });
    await delivery.afterResult(id, name, handled, 'ja', true);
  };
  mgr.addSpeaker('s', '検証');
  const step = async (sec = 1) => {
    mgr.feedAudio('s', new Float32Array(Math.round(SR * sec)), now);
    await mgr.tick('s');
    await settle(2);
    now += sec * 1000;
  };
  const advance = (ms: number) => { now += ms; };
  /** Feed audio without a tick (e.g. between timer ticks). */
  const feed = (sec: number) => { mgr.feedAudio('s', new Float32Array(Math.round(SR * sec)), now); now += sec * 1000; };
  const take = () => { const b = confirmedBatches; confirmedBatches = new Map(); return b; };
  const batchSize = () => confirmedBatches.get('s')?.length ?? 0;
  return { mgr, step, advance, feed, settled, take, delivery, batchSize, get calls() { return calls; } };
}

const wseg = (ws: [string, number, number][]): WhisperSegment => ({
  text: ws.map(w => w[0]).join(''), start: ws[0][1], end: ws[ws.length - 1][2],
  words: ws.map(([word, start, end]) => ({ word, start, end, probability: 0.9 })),
});
/** Two partials; the second covers all audio but is not yet confirmed (LocalAgreement-2). */
const SHORT_UTTERANCE: Response[] = [
  [wseg([['資料', 0.1, 0.5], ['を', 0.5, 0.7]])],
  [wseg([['資料', 0.1, 0.5], ['を', 0.5, 0.7], ['送り', 0.75, 1.2], ['ます', 1.2, 1.6]])],
];
const xadds = (redis: FakeRedis, text: string) => redis.log.filter(l => l === `xadd:${text}`).length;

/** Holds the reply of the first confirmed XADD (request 2) until released. */
function holdFirstConfirmedXAdd(redis: FakeRedis) {
  const gate = deferred();
  let held = false;
  redis.hold = (cmd, arg) => {
    if (!held && cmd === 'xAdd' && arg.type === 'transcription') { held = true; return gate.promise; }
    return null;
  };
  return gate;
}

async function main() {
  console.log('\n=== Live transcript delivery order ===\n');

  await test('control: unordered SegmentPublisher lets a slow older update revive a cleared draft', async () => {
    const redis = new FakeRedis();
    const gate = holdFirstConfirmedXAdd(redis);
    const p = pipeline(realPublisher(redis));
    await p.step(); await p.step(); await p.step();
    gate.resolve();
    await p.mgr.finalizeAll(1000);
    assert.deepStrictEqual(p.settled.map(s => s.pending), [['最初の文です。', '次は'], ['次は議題です'], [], []]);
    assert.deepStrictEqual(redis.pendingTexts('検証'), ['次は議題です'], 'the race exists without the ordered publisher');
  });

  await test('ordered: slow confirmed publish does not revive the draft cleared by a later result', async () => {
    const redis = new FakeRedis();
    const gate = holdFirstConfirmedXAdd(redis);
    const ordered = new OrderedTranscriptPublisher(realPublisher(redis), { log: () => {} });
    const p = pipeline(ordered);
    await p.step(); await p.step(); await p.step();
    // request 3 already settled while request 2 is still being delivered
    assert.strictEqual(p.settled.length, 3);
    assert.deepStrictEqual(redis.log, ['set:最初の文です。+次は', 'publish', 'xadd:最初の文です。'], 'request 3 waits behind request 2');
    gate.resolve();
    await p.mgr.finalizeAll(1000);
    assert.strictEqual(redis.pendingTexts('検証'), null, 'no stale pending after the latest (empty) result');
    assert.deepStrictEqual(redis.log, [
      'set:最初の文です。+次は', 'publish',
      'xadd:最初の文です。', 'set:次は議題です', 'publish',
      'del', 'publish',
      'del', 'publish',
    ]);
    // WS bundles arrive in settle order; the last one clears the draft
    assert.deepStrictEqual(redis.messages.map(m => m.pending.map((s: any) => s.text)), p.settled.map(s => s.pending));
    assert.deepStrictEqual(redis.messages.map(m => m.confirmed.map((s: any) => s.text)), [[], ['最初の文です。'], [], []]);
  });

  await test('session close: final flush, session_end and close follow an update still being delivered', async () => {
    const redis = new FakeRedis();
    const gate = holdFirstConfirmedXAdd(redis);
    const logs: string[] = [];
    const ordered = new OrderedTranscriptPublisher(realPublisher(redis), { log: m => logs.push(m) });
    const p = pipeline(ordered);
    await p.step(); await p.step();
    let closed = false;
    const done = closeTranscriptionSession({ manager: p.mgr, publisher: ordered, finalFlushTimeoutMs: 5000, takeConfirmedBatches: p.take, log: () => {} })
      .then(s => { closed = true; return s; });
    await settle(20);
    assert.strictEqual(closed, false);
    assert.ok(!redis.log.includes('xadd:session_end'), 'no session_end while an update is being delivered');
    gate.resolve();
    const summary = await done;
    assert.strictEqual(summary!.timedOut, false);
    assert.strictEqual(summary!.pendingCallbacks, 0);
    assert.strictEqual(redis.pendingTexts('検証'), null);
    assert.deepStrictEqual(redis.log.slice(-2), ['xadd:session_end', 'disconnect']);
    assert.ok(redis.log.indexOf('xadd:最初の文です。') < redis.log.indexOf('xadd:session_end'));
    // A late update (e.g. a callback that outlived the final-flush timeout) is reported, not written after close
    const before = redis.log.length;
    await ordered.publishTranscript('検証', [], [{ speaker: '検証', text: '遅延', start: 0, end: 1, language: 'ja', completed: false }]);
    assert.strictEqual(redis.log.length, before, 'nothing written after session_end/close');
    assert.strictEqual(redis.disconnected, true);
    assert.strictEqual(ordered.droppedAfterSessionEnd, 1);
    assert.ok(logs.some(l => l.includes('after session_end') && l.includes('dropped')), 'drop is reported');
  });

  // Changed from the previous (uncommitted) version of this test, which let
  // the queue continue after abandoning the stuck update and delivered the
  // queued final result afterwards (one 30 ms bound per operation, so N queued
  // updates could take N x bound). The repair request
  // (.hw/evidence/realtime-transcription-quality/qa-repair-request.md, item 3)
  // rejects that design: shutdown uses one budget for all remaining updates,
  // and when it expires, updates that have not started are reported and never
  // started. The stuck update is abandoned (outcome unknown), the queued final
  // result is not started, and session_end / close still run, bounded.
  await test('an update whose delivery never finishes: close stays bounded, the rest is reported as not delivered', async () => {
    const redis = new FakeRedis();
    let stuck = false;
    // Only the first confirmed XADD never replies; later commands would be answered.
    redis.hold = (cmd, arg) => {
      if (!stuck && cmd === 'xAdd' && arg.type === 'transcription') { stuck = true; return new Promise<void>(() => {}); }
      return null;
    };
    const logs: string[] = [];
    const ordered = new OrderedTranscriptPublisher(realPublisher(redis), { log: m => logs.push(m), endDrainTimeoutMs: 30 });
    const p = pipeline(ordered);
    await p.step(); await p.step();
    const summary = await closeTranscriptionSession({ manager: p.mgr, publisher: ordered, finalFlushTimeoutMs: 50, takeConfirmedBatches: p.take, log: () => {} });
    assert.strictEqual(summary!.timedOut, true);
    assert.ok(logs.some(l => l.includes('shutdown delivery budget of 30ms expired') && l.includes('abandoned in progress')), 'expiry is reported');
    assert.strictEqual(ordered.stalledDuringShutdown, 1, 'the stuck update is abandoned, not counted as delivered');
    assert.strictEqual(ordered.notStartedAtShutdown, 1, 'the queued final result is reported as not started');
    assert.deepStrictEqual(redis.log, [
      'set:最初の文です。+次は', 'publish',
      'xadd:最初の文です。',
      'xadd:session_end', 'disconnect',
    ], 'nothing queued before the expiry is started after it; session_end and close still run');
  });

  await test('a failing publish is reported and does not block later updates', async () => {
    const events: string[] = [];
    const logs: string[] = [];
    let first = true;
    const inner: SessionClosePublisher = {
      publishTranscript: async (_s, _c, pending) => {
        if (first) { first = false; throw new Error('boom'); }
        events.push(`publish:${pending.map(x => x.text).join('+')}`);
      },
      publishSessionEnd: async () => { events.push('session_end'); },
      close: async () => { events.push('close'); },
    };
    const ordered = new OrderedTranscriptPublisher(inner, { log: m => logs.push(m) });
    const seg = (text: string): TranscriptionSegment => ({ speaker: 'A', text, start: 0, end: 1, language: 'ja', completed: false });
    await Promise.all([ordered.publishTranscript('A', [], [seg('一')]), ordered.publishTranscript('A', [], [seg('二')])]);
    await ordered.publishSessionEnd();
    await ordered.close();
    assert.deepStrictEqual(events, ['publish:二', 'session_end', 'close']);
    assert.ok(logs.some(l => l.includes('failed: boom')));
  });


  // ── QA F1: segments confirmed outside onSegmentReady are delivered promptly ──

  await test('F1 idle finalize: cached-hypothesis commit is delivered at once, draft cleared, no new request', async () => {
    const redis = new FakeRedis();
    const ordered = new OrderedTranscriptPublisher(realPublisher(redis), { log: () => {} });
    const p = pipeline(ordered, { responses: SHORT_UTTERANCE });
    await p.step(); await p.step();
    assert.deepStrictEqual(redis.log, ['set:資料を', 'publish', 'set:資料を送ります', 'publish']);
    p.advance(6000);
    await p.mgr.tick('s');
    await settle();
    assert.strictEqual(p.calls, 2, 'idle commit uses the cached hypothesis (no onSegmentReady)');
    assert.deepStrictEqual(redis.log.slice(4), ['xadd:資料を送ります', 'del', 'publish'], 'confirmed and draft clear are delivered without waiting for more speech');
    assert.strictEqual(redis.pendingTexts('検証'), null);
    assert.strictEqual(p.batchSize(), 0, 'nothing left waiting in confirmedBatches');
    const last = redis.messages[redis.messages.length - 1];
    assert.deepStrictEqual(last.confirmed.map((x: any) => x.text), ['資料を送ります']);
    assert.deepStrictEqual(last.pending, []);
    await closeTranscriptionSession({ manager: p.mgr, publisher: ordered, finalFlushTimeoutMs: 1000, takeConfirmedBatches: p.take, log: () => {} });
    assert.strictEqual(xadds(redis, '資料を送ります'), 1, 'not sent again at close');
    assert.deepStrictEqual(redis.log.slice(-2), ['xadd:session_end', 'disconnect']);
  });

  await test('F1 speaker-change flush: cached-hypothesis commit is delivered at once', async () => {
    const redis = new FakeRedis();
    const ordered = new OrderedTranscriptPublisher(realPublisher(redis), { log: () => {} });
    const p = pipeline(ordered, { responses: SHORT_UTTERANCE });
    await p.step(); await p.step();
    await p.mgr.flushSpeaker('s');
    await settle();
    assert.strictEqual(p.calls, 2);
    assert.deepStrictEqual(redis.log, ['set:資料を', 'publish', 'set:資料を送ります', 'publish', 'xadd:資料を送ります', 'del', 'publish']);
    assert.strictEqual(redis.pendingTexts('検証'), null);
  });

  await test('F1 hard cap: forced commit outside a result is delivered with the draft left after the commit', async () => {
    const redis = new FakeRedis();
    const ordered = new OrderedTranscriptPublisher(realPublisher(redis), { log: () => {} });
    const p = pipeline(ordered, {
      maxBufferDuration: 3,
      responses: [[wseg([['資料', 0.1, 0.3], ['を', 0.3, 0.5], ['送り', 0.6, 0.95]])]],
    });
    await p.step();
    assert.deepStrictEqual(redis.log, ['set:資料を送り', 'publish']);
    p.feed(3);
    await p.mgr.tick('s'); // pending 4 s > 3 s cap, hypothesis cached: forced commit without a request
    await settle();
    assert.strictEqual(p.calls, 1);
    assert.deepStrictEqual(redis.log.slice(2), ['xadd:資料を', 'set:送り', 'publish']);
    assert.deepStrictEqual(redis.pendingTexts('検証'), p.mgr.getPendingDraft('s').map(d => d.text), 'draft as it is after the commit, not the older snapshot');
    assert.strictEqual(p.batchSize(), 0);
    p.mgr.removeAll();
  });

  await test('F1 a confirmation made inside a result is sent once (no second publish from the confirmed hook)', async () => {
    const redis = new FakeRedis();
    const ordered = new OrderedTranscriptPublisher(realPublisher(redis), { log: () => {} });
    const p = pipeline(ordered, { responses: [SHORT_UTTERANCE[0], SHORT_UTTERANCE[1]] });
    await p.step();
    p.feed(0.6); // audio the cached hypothesis does not cover: flush needs a final request
    await p.mgr.flushSpeaker('s');
    await settle();
    assert.strictEqual(p.calls, 2);
    assert.deepStrictEqual(redis.log, ['set:資料を', 'publish', 'xadd:資料を送ります', 'del', 'publish'], 'exactly one publish for the final result');
    await closeTranscriptionSession({ manager: p.mgr, publisher: ordered, finalFlushTimeoutMs: 1000, takeConfirmedBatches: p.take, log: () => {} });
    assert.strictEqual(xadds(redis, '資料を送ります'), 1);
  });

  await test('F1 ordering: a confirmed-only publish waits behind a slower earlier update and leaves no stale draft', async () => {
    const redis = new FakeRedis();
    const gate = deferred();
    let sets = 0;
    redis.hold = cmd => (cmd === 'set' && ++sets === 2 ? gate.promise : null);
    const ordered = new OrderedTranscriptPublisher(realPublisher(redis), { log: () => {} });
    const p = pipeline(ordered, { responses: SHORT_UTTERANCE });
    await p.step(); await p.step();
    p.advance(6000);
    await p.mgr.tick('s');
    await settle();
    assert.deepStrictEqual(redis.log, ['set:資料を', 'publish', 'set:資料を送ります'], 'idle publish queued behind the held draft update');
    gate.resolve();
    await settle();
    assert.deepStrictEqual(redis.log.slice(3), ['publish', 'xadd:資料を送ります', 'del', 'publish']);
    assert.strictEqual(redis.pendingTexts('検証'), null);
    p.mgr.removeAll();
  });

  await test('F1 session close: cached-hypothesis commit during finalizeAll is delivered once, before session_end', async () => {
    const redis = new FakeRedis();
    const ordered = new OrderedTranscriptPublisher(realPublisher(redis), { log: () => {} });
    const p = pipeline(ordered, { responses: SHORT_UTTERANCE });
    await p.step(); await p.step();
    await closeTranscriptionSession({ manager: p.mgr, publisher: ordered, finalFlushTimeoutMs: 1000, takeConfirmedBatches: p.take, log: () => {} });
    assert.strictEqual(p.calls, 2);
    assert.deepStrictEqual(redis.log.slice(4), ['xadd:資料を送ります', 'del', 'publish', 'xadd:session_end', 'disconnect']);
    assert.strictEqual(ordered.droppedAfterSessionEnd, 0);
  });

  // ── QA F2: discarded partial result clears a draft that is still shown ──

  await test('F2 discarded partial after a draft: the draft is cleared', async () => {
    const redis = new FakeRedis();
    const ordered = new OrderedTranscriptPublisher(realPublisher(redis), { log: () => {} });
    const p = pipeline(ordered, { responses: [SHORT_UTTERANCE[0], 'discard'] });
    await p.step(); await p.step();
    assert.deepStrictEqual(p.settled.map(x => x.pending), [['資料を'], []]);
    assert.deepStrictEqual(redis.log, ['set:資料を', 'publish', 'del', 'publish']);
    assert.strictEqual(redis.pendingTexts('検証'), null);
    // Nothing shown any more: a further discard sends nothing
    await p.step();
    assert.strictEqual(redis.log.length, 4);
    p.mgr.removeAll();
  });

  await test('F2 discarded partial without a shown draft sends nothing; stale results are never delivered', async () => {
    const redis = new FakeRedis();
    const ordered = new OrderedTranscriptPublisher(realPublisher(redis), { log: () => {} });
    const p = pipeline(ordered, { responses: ['discard'] });
    await p.step();
    assert.deepStrictEqual(redis.log, [], 'no draft was shown: no extra Redis traffic');
    const q = pipeline(ordered, { responses: [SHORT_UTTERANCE[0]] });
    await q.step();
    const before = redis.log.length;
    await q.delivery.afterDiscard('s', '検証', { status: 'stale', confirmed: [], pending: [] }, 'ja', false);
    await q.delivery.afterDiscard('s', '検証', { status: 'stale', confirmed: [], pending: [] }, 'ja', true);
    assert.strictEqual(redis.log.length, before, 'stale result publishes nothing, even final');
    assert.deepStrictEqual(redis.pendingTexts('検証'), ['資料を']);
    p.mgr.removeAll(); q.mgr.removeAll();
  });

  // ── Shutdown: one delivery budget for all remaining updates ──

  const segA = (text: string): TranscriptionSegment => ({ speaker: 'A', text, start: 0, end: 1, language: 'ja', completed: false });

  await test('shutdown budget does not grow with the number of queued updates when Redis is stuck', async () => {
    for (const n of [2, 40]) {
      const logs: string[] = [];
      const events: string[] = [];
      let started = 0;
      const inner: SessionClosePublisher = {
        publishTranscript: () => { started++; return new Promise<void>(() => {}); }, // Redis stuck
        publishSessionEnd: async () => { events.push('session_end'); },
        close: async () => { events.push('close'); },
      };
      const ordered = new OrderedTranscriptPublisher(inner, { log: m => logs.push(m), endDrainTimeoutMs: 60, endOpTimeoutMs: 40 });
      for (let i = 0; i < n; i++) void ordered.publishTranscript('A', [], [segA(`${i}`)]);
      const t0 = Date.now();
      const ok = await ordered.drain();
      await ordered.publishSessionEnd();
      await ordered.close();
      const elapsed = Date.now() - t0;
      assert.strictEqual(ok, false, 'drain reports that not everything was delivered');
      assert.ok(elapsed < 400, `n=${n}: shutdown took ${elapsed}ms (budget 60 ms, not n x bound)`);
      assert.strictEqual(started, 1, 'queued updates are never started after expiry');
      assert.strictEqual(ordered.stalledDuringShutdown, 1);
      assert.strictEqual(ordered.notStartedAtShutdown, n - 1);
      assert.deepStrictEqual(events, ['session_end', 'close']);
      assert.ok(logs.some(l => l.includes(`${n - 1} queued transcript update(s) not started`)), 'not-started count is logged');
      // Requested after session_end: dropped and counted
      await ordered.publishTranscript('A', [], [segA('late')]);
      assert.strictEqual(started, 1);
      assert.strictEqual(ordered.droppedAfterSessionEnd, 1);
    }
  });

  await test('shutdown within budget: every queued update is delivered in order before session_end', async () => {
    const events: string[] = [];
    const inner: SessionClosePublisher = {
      publishTranscript: async (_s, _c, pending) => { await new Promise(r => setTimeout(r, 2)); events.push(pending[0].text); },
      publishSessionEnd: async () => { events.push('session_end'); },
      close: async () => { events.push('close'); },
    };
    const ordered = new OrderedTranscriptPublisher(inner, { log: () => {}, endDrainTimeoutMs: 2000 });
    const expected: string[] = [];
    for (let i = 0; i < 30; i++) { expected.push(`${i}`); void ordered.publishTranscript('A', [], [segA(`${i}`)]); }
    assert.strictEqual(await ordered.drain(), true);
    void ordered.publishTranscript('A', [], [segA('flush')]); // final flush requested after drain, within the budget
    await ordered.publishSessionEnd();
    await ordered.close();
    assert.deepStrictEqual(events, [...expected, 'flush', 'session_end', 'close']);
    assert.strictEqual(ordered.notStartedAtShutdown, 0);
    assert.strictEqual(ordered.stalledDuringShutdown, 0);
  });

  await test('session_end and close that never finish are each bounded and reported', async () => {
    const logs: string[] = [];
    const inner: SessionClosePublisher = {
      publishTranscript: async () => {},
      publishSessionEnd: () => new Promise<void>(() => {}),
      close: () => new Promise<void>(() => {}),
    };
    const ordered = new OrderedTranscriptPublisher(inner, { log: m => logs.push(m), endDrainTimeoutMs: 50, endOpTimeoutMs: 30 });
    const t0 = Date.now();
    await closeTranscriptionSession({ manager: null, publisher: ordered, finalFlushTimeoutMs: 10, takeConfirmedBatches: () => new Map(), log: () => {} });
    const elapsed = Date.now() - t0;
    assert.ok(elapsed < 300, `close took ${elapsed}ms`);
    assert.strictEqual(ordered.stalledDuringShutdown, 2);
    assert.ok(logs.some(l => l.includes('publishSessionEnd did not finish within 30ms')));
    assert.ok(logs.some(l => l.includes('close did not finish within 30ms')));
  });

  await test('after the budget expired, the final flush of leftover confirmed segments is reported, not started', async () => {
    const logs: string[] = [];
    const started: string[] = [];
    const inner: SessionClosePublisher = {
      publishTranscript: (_s, confirmed) => { started.push(confirmed.map(c => c.text).join('+') || '-'); return new Promise<void>(() => {}); },
      publishSessionEnd: async () => { started.push('session_end'); },
      close: async () => {},
    };
    const ordered = new OrderedTranscriptPublisher(inner, { log: m => logs.push(m), endDrainTimeoutMs: 30 });
    void ordered.publishTranscript('A', [], [segA('stuck')]);
    const leftover = new Map([['a', [{ ...segA('残り'), completed: true }]]]);
    await closeTranscriptionSession({ manager: null, publisher: ordered, finalFlushTimeoutMs: 10, takeConfirmedBatches: () => leftover, log: () => {} });
    assert.deepStrictEqual(started, ['-', 'session_end']);
    assert.strictEqual(ordered.notStartedAtShutdown, 1);
    assert.ok(logs.some(l => l.includes('requested after the shutdown delivery budget expired')));
  });

  console.log(`\n=== Results: ${passed} passed, ${failed} failed ===\n`);
  if (failed > 0) {
    console.log(`Failed: ${failures.join(', ')}`);
    process.exit(1);
  }
}

let finished = false;
process.on('exit', code => {
  if (!finished && code === 0) {
    console.log('FAIL  main() did not finish (a test never settled)');
    process.exitCode = 1;
  }
});

main().then(() => { finished = true; }, err => {
  console.error(err);
  process.exit(1);
});
