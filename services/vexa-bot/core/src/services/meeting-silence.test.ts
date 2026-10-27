import assert from 'node:assert/strict';
import { test } from 'node:test';
import { MeetingSilenceMonitor, MEETING_SILENCE_TIMEOUT_MS } from './meeting-silence';
import type { StreamingVad, StreamingVadResult, VadSpeakerState } from './vad';

function fixture() {
  let now = 0;
  let speech = false;
  let exits = 0;
  const errors: unknown[] = [];
  const vad: StreamingVad = {
    createSpeakerState: () => ({} as VadSpeakerState),
    processStreamingChunk: async () => ({ speech, triggered: speech, maxProb: speech ? 1 : 0 }),
  };
  const monitor = new MeetingSilenceMonitor(vad, () => exits++, e => errors.push(e), () => now);
  return {
    monitor, vad, errors,
    time: (ms: number) => { now = ms; },
    speech: (active: boolean) => { speech = active; },
    exits: () => exits,
    audio: () => monitor.observeAudio(new Float32Array(4096).fill(0.1)),
  };
}

test('silent bots remaining: exactly 10 minutes after capture starts, exit only once', async () => {
  const f = fixture();
  assert.equal(MEETING_SILENCE_TIMEOUT_MS, 600_000);
  f.monitor.start();
  // Non-zero noise (but no VAD speech) must not keep the meeting alive.
  f.time(599_999);
  await f.audio();
  f.monitor.check();
  assert.equal(f.exits(), 0);
  f.time(600_000);
  f.monitor.check();
  f.monitor.check();
  assert.equal(f.exits(), 1);
});

test('speech near the deadline resets the full 10-minute window', async () => {
  const f = fixture();
  f.monitor.start();
  f.time(599_000);
  f.speech(true);
  await f.audio();
  f.speech(false);
  f.time(600_000);
  await f.audio();
  f.monitor.check();
  assert.equal(f.exits(), 0);
  f.time(1_198_999);
  f.monitor.check();
  assert.equal(f.exits(), 0);
  f.time(1_199_000);
  f.monitor.check();
  assert.equal(f.exits(), 1);
});

test('admission waiting and unavailable capture do not count as silence', () => {
  const f = fixture();
  f.time(3_600_000);
  f.monitor.check();
  assert.equal(f.exits(), 0);
  f.monitor.start();
  f.time(4_199_999);
  f.monitor.start(); // duplicate ready notification must not reset the timer
  f.monitor.check();
  assert.equal(f.exits(), 0);
  f.time(4_200_000);
  f.monitor.check();
  assert.equal(f.exits(), 1);
});

test('stopped monitor and late audio cannot trigger another exit', async () => {
  const f = fixture();
  f.monitor.start();
  f.monitor.stop();
  f.time(4_200_000);
  f.speech(true);
  await f.audio();
  f.monitor.start();
  f.monitor.check();
  assert.equal(f.exits(), 0);
});

test('VAD failure disables silence exit instead of ending an unobserved meeting', async () => {
  const f = fixture();
  f.vad.processStreamingChunk = async () => { throw new Error('VAD unavailable'); };
  f.monitor.start();
  await f.audio();
  f.time(4_200_000);
  f.monitor.check();
  assert.equal(f.exits(), 0);
  assert.equal(f.errors.length, 1);
});

test('pending VAD speech wins over the deadline; overlapping frames do not queue', async () => {
  const f = fixture();
  let finish!: (value: StreamingVadResult) => void;
  let calls = 0;
  f.vad.processStreamingChunk = () => {
    calls++;
    return new Promise(resolve => { finish = resolve; });
  };
  f.monitor.start();
  f.time(599_999);
  const pending = f.audio();
  await f.audio();
  assert.equal(calls, 1);
  f.time(600_000);
  f.monitor.check();
  assert.equal(f.exits(), 0);
  finish({ speech: true, triggered: true, maxProb: 1 });
  await pending;
  f.monitor.check();
  assert.equal(f.exits(), 0);
});
