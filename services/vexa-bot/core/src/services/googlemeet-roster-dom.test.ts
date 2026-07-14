import { existsSync } from 'fs';
import { chromium } from 'playwright';
import type { Browser, Page } from 'playwright';
import { startGoogleRecording } from '../platforms/googlemeet/recording';
import { getParticipantRoster } from './participant-roster';
import type { BotConfig } from '../types';

let passed = 0;
let failed = 0;

function expect(name: string, actual: unknown, expected: unknown): void {
  if (JSON.stringify(actual) === JSON.stringify(expected)) {
    console.log(`PASS ${name}`);
    passed++;
  } else {
    console.log(`FAIL ${name}`);
    console.log(`  expected: ${JSON.stringify(expected)}`);
    console.log(`  actual:   ${JSON.stringify(actual)}`);
    failed++;
  }
}

async function waitForRosterNames(expected: string[], timeoutMs = 2500): Promise<string[]> {
  const deadline = Date.now() + timeoutMs;
  let names: string[] = [];
  while (Date.now() < deadline) {
    names = getParticipantRoster().map((entry) => entry.participant_name).sort();
    if (expected.every((name) => names.includes(name))) return names;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  return names;
}

async function closeRecording(page: Page, browser: Browser, recordingRun: Promise<void>): Promise<void> {
  await page.close();
  await browser.close();
  await Promise.race([
    recordingRun.catch(() => undefined),
    new Promise<void>((resolve) => setTimeout(resolve, 1000)),
  ]);
}

async function main(): Promise<void> {
  const executableCandidates = [
    process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
    chromium.executablePath(),
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
  ].filter((candidate): candidate is string => !!candidate);
  const executablePath = executableCandidates.find((candidate) => existsSync(candidate));
  if (!executablePath) {
    console.log('SKIP Google Meet roster DOM fixture: Chromium executable is unavailable');
    return;
  }

  const browser = await chromium.launch({ executablePath, headless: true, args: ['--no-sandbox'] });
  const page = await browser.newPage();
  await page.exposeFunction('logBot', () => undefined);
  await page.setContent(`
    <style>
      button, section, [data-participant-id], [data-self-name] {
        display: block; width: 160px; height: 24px;
      }
    </style>
    <button aria-label="People" aria-controls="people-panel" aria-expanded="true">People</button>
    <button aria-label="Leave call">Leave</button>
    <section id="people-panel" role="region">
      <div data-self-name="Kabosu">
        <div data-participant-id="bot" class="speaking"><span class="notranslate">Kabosu (Du)</span></div>
      </div>
      <div data-participant-id="silent" class="speaking"><span class="notranslate">Silent Person</span></div>
    </section>
    <div id="tiles">
      <div id="migrating" data-participant-id="migrating" class="speaking">
        <span class="notranslate">Migrating Person</span>
      </div>
    </div>
  `);
  await page.evaluate(() => {
    (window as any).VexaBrowserUtils = {};
    (window as any).__vexaAudioService = {
      getSessionAudioStartTime: () => Date.now() - 1000,
      disconnect: () => undefined,
    };
  });

  const botConfig: BotConfig = {
    platform: 'google_meet',
    meetingUrl: 'https://meet.google.com/test-fixture',
    botName: 'Kabosu',
    token: 'fixture-token',
    connectionId: 'fixture-connection',
    nativeMeetingId: 'test-fixture',
    redisUrl: 'redis://fixture.invalid',
    automaticLeave: {
      waitingRoomTimeout: 60_000,
      noOneJoinedTimeout: 60_000,
      everyoneLeftTimeout: 60_000,
    },
    meeting_id: 1,
    recordingEnabled: false,
  };

  const recordingRun = startGoogleRecording(page, botConfig);
  const names = await waitForRosterNames(['Migrating Person', 'Silent Person']);

  await page.evaluate(() => {
    const panel = document.getElementById('people-panel');
    const migrating = document.getElementById('migrating');
    if (panel && migrating) {
      panel.appendChild(migrating);
      migrating.classList.remove('speaking');
    }
  });
  await page.waitForTimeout(100);

  await page.evaluate(() => {
    const oldButton = document.querySelector('button[aria-controls="people-panel"]');
    const oldPanel = document.getElementById('people-panel');
    const newButton = document.createElement('button');
    newButton.setAttribute('aria-label', 'People');
    newButton.setAttribute('aria-controls', 'replacement-people-panel');
    newButton.setAttribute('aria-expanded', 'true');
    newButton.textContent = 'People';
    oldButton?.replaceWith(newButton);
    oldPanel?.remove();

    const replacementPanel = document.createElement('section');
    replacementPanel.id = 'replacement-people-panel';
    replacementPanel.setAttribute('role', 'region');
    replacementPanel.innerHTML = `
      <div data-participant-id="replacement-silent" class="speaking">
        <span class="notranslate">Replacement Silent Person</span>
      </div>
    `;
    document.body.appendChild(replacementPanel);
  });
  const namesAfterButtonReplacement = await waitForRosterNames(
    ['Replacement Silent Person'],
    3000,
  );

  const speakerEvents = await page.evaluate(() =>
    ((window as any).__vexaSpeakerEvents || []).map((event: any) => ({
      event_type: event.event_type,
      participant_name: event.participant_name,
    })),
  );

  expect('silent People-panel participant is accumulated', names.includes('Silent Person'), true);
  expect('participant tile is accumulated', names.includes('Migrating Person'), true);
  expect('nested localized self participant is excluded', names.includes('Kabosu (Du)'), false);
  expect(
    'replacement People button and controlled panel are reacquired',
    namesAfterButtonReplacement.includes('Replacement Silent Person'),
    true,
  );
  expect(
    'pre-opened People-panel rows never emit speaker events',
    speakerEvents.some((event: any) => event.participant_name === 'Silent Person'),
    false,
  );
  expect(
    'replacement People-panel rows never emit speaker events',
    speakerEvents.some((event: any) => event.participant_name === 'Replacement Silent Person'),
    false,
  );
  expect(
    'stale tile observer cannot emit END after the tile moves into People panel',
    speakerEvents.filter((event: any) => event.participant_name === 'Migrating Person').map((event: any) => event.event_type),
    ['SPEAKER_START'],
  );

  await closeRecording(page, browser, recordingRun);
  console.log(`\n${passed} passed, ${failed} failed`);
  if (failed > 0) process.exit(1);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
