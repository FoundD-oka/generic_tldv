import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { test } from 'node:test';
import { chromium } from 'playwright';
import { startGoogleRecording, stopGoogleRecording } from '../platforms/googlemeet/recording';
import { RecordingService } from './recording';
import { SileroVAD } from './vad';
import type { BotConfig } from '../types';

test('silent AI participants: browser audio resets deadline, normal completion flushes final chunk', { timeout: 20_000 }, async (t) => {
  const executablePath = [
    process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
    chromium.executablePath(),
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/usr/bin/chromium',
  ].find(p => p && existsSync(p));
  assert.ok(executablePath, 'Chromium is required for the integration test');
  let now = 0;
  t.mock.method(performance, 'now', () => now);
  t.mock.method(SileroVAD, 'create', async () => ({
    createSpeakerState: () => ({}),
    processStreamingChunk: async (audio: Float32Array) => ({ speech: audio[0] > 0.5 }),
  }));
  const uploads: { final: boolean; size: number }[] = [];
  t.mock.method(RecordingService.prototype, 'uploadChunk', async (...args: any[]) => {
    uploads.push({ final: args[4], size: args[2].length });
  });
  const browser = await chromium.launch({ executablePath, headless: true, args: ['--no-sandbox'] });
  const page = await browser.newPage();
  await page.exposeFunction('logBot', () => undefined);
  await page.setContent(`
    <button aria-label="Leave call">Leave</button>
    <div data-participant-id="self"><span class="notranslate">Kabosu</span></div>
    <div data-participant-id="other-bot"><span class="notranslate">Other AI Bot</span></div>
  `);
  // Exercise real browser bindings and the real upload/stop pipeline. Only
  // the microphone/encoder and VAD inference are replaced with deterministic
  // fixtures: no real meeting or network uploads are involved.
  await page.evaluate(() => {
    (window as any).VexaBrowserUtils = {
      BrowserAudioService: class {
        async findMediaElements() { return [document.createElement('audio')]; }
        async createCombinedAudioStream() { return {}; }
        async initializeAudioProcessor() { return {}; }
        getSessionAudioStartTime() { return Date.now(); }
        disconnect() {}
        setupAudioDataProcessor(callback: (data: Float32Array) => void) {
          (window as any).__testSendAudio = (value: number) => callback(new Float32Array(4096).fill(value));
        }
      },
      BrowserMediaRecorderPipeline: class {
        constructor(private options: any) {}
        async start() {
          await this.options.chunkCallback({ base64: 'YXVkaW8=', chunkSeq: 0, isFinal: false });
        }
        getMediaRecorder() { return null; }
        async stop() {
          await this.options.chunkCallback({ base64: 'dGFpbA==', chunkSeq: 1, isFinal: true });
        }
      },
    };
  });
  const config: BotConfig = {
    platform: 'google_meet', meetingUrl: 'https://meet.google.com/fixture',
    nativeMeetingId: 'fixture', meeting_id: 1, botName: 'Kabosu',
    token: 'fixture-token', connectionId: 'silence-fixture', redisUrl: 'redis://fixture.invalid',
    recordingEnabled: true, captureModes: ['audio'], transcribeEnabled: false,
    recordingUploadUrl: 'https://fixture.invalid/upload',
    automaticLeave: { waitingRoomTimeout: 60_000, noOneJoinedTimeout: 9_999_999, everyoneLeftTimeout: 9_999_999 },
  };
  let completed = false;
  const recording = startGoogleRecording(page, config).then(() => { completed = true; });
  // Keep rejection handled during assertion failures and browser teardown.
  recording.catch(() => {});
  try {
    await page.waitForFunction(() => typeof (window as any).__testSendAudio === 'function');
    // Keep multiple bots present regardless of DOM-count heuristics.
    await page.evaluate(() => { (window as any).getGoogleMeetActiveParticipantsCount = () => 2; });
    now = 599_000;
    await page.evaluate(() => (window as any).__testSendAudio(1));
    // Wait for the actual exposed async callback (a following binding call
    // observes the previous one having completed in this fixture).
    await page.evaluate(() => (window as any).__vexaMeetingSpeechAudio([1]));
    now = 600_000;
    await page.evaluate(() => (window as any).__testSendAudio(0.1));
    await new Promise(resolve => setTimeout(resolve, 1100));
    assert.equal(completed, false, 'speech reset must prevent premature termination');
    now = 1_199_000;
    await recording;
    assert.equal(completed, true, 'silence must finish recording normally with bots still present');
    await stopGoogleRecording();
    assert.deepEqual(uploads, [{ final: false, size: 5 }, { final: true, size: 4 }]);
    assert.equal(await page.evaluate(() => (window as any).getGoogleMeetActiveParticipantsCount()), 2,
      'termination must not depend on the other bot leaving');
  } finally {
    await stopGoogleRecording();
    await page.close();
    await browser.close();
  }
});
