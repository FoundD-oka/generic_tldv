import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { chromium } from 'playwright';
import { joinGoogleMeeting, waitForAnySelector } from './join';
import { BotConfig } from '../../types';
import { checkForWaitingRoomIndicators, checkForGoogleRejection, waitForGoogleMeetingAdmission, AdmissionError } from './admission';
import { googleWaitingRoomIndicators, googleRejectionIndicators } from './selectors';

async function main() {
  const callbacks = require('../../utils');
  const originalAwaitingCallback = callbacks.callAwaitingAdmissionCallback;
  let awaitingCallbacks = 0;
  callbacks.callAwaitingAdmissionCallback = async () => { awaitingCallbacks++; };
  // Startup wiring matters: testing the cookie helper alone missed this regression.
  const source = readFileSync(resolve('src/index.ts'), 'utf8');
  const launch = source.indexOf('await chromium.launchPersistentContext(BROWSER_DATA_DIR');
  const restore = source.indexOf('await restoreCookies(context', launch);
  const firstPage = source.indexOf('const pages = context.pages()', launch);
  assert.ok(launch >= 0 && restore > launch && restore < firstPage,
    'meeting Bot must restore cookies before selecting/navigating its page');
  const save = source.indexOf('await persistCookies(page.context()');
  assert.ok(save > 0 && save < source.indexOf('syncBrowserDataToS3(currentBotConfig)', save),
    'meeting Bot must export in-memory cookies before S3 sync');

  const browser = await chromium.launch({
    headless: true,
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH } : {}),
  });
  try {
    for (const label of ['Join now', 'Ask to join', 'Switch here', '今すぐ参加', '参加をリクエスト', '参加を申請', 'ここに切り替え']) {
      for (const ariaOnly of [false, true]) {
        const page = await browser.newPage();
        await page.route('https://meet.test/**', route => route.fulfill({
          contentType: 'text/html; charset=utf-8',
          body: `<button jsname="unrelated"><span>その他のオプション</span></button>
            <button id="join" ${ariaOnly ? `aria-label="${label}"` : ''}>${ariaOnly ? '<svg></svg>' : `<span>${label}</span>`}</button>
            <script>document.addEventListener('click', e => document.body.dataset.clicked = e.target.closest('button')?.id || 'wrong');</script>`,
        }));
        // Keep the production DOM/selector/click behavior; omit screenshot I/O and fixed sleeps.
        page.screenshot = async () => Buffer.alloc(0);
        page.waitForTimeout = async () => {};
        const originalWait = page.waitForSelector.bind(page);
        page.waitForSelector = ((selector: string, options: any) => originalWait(selector, {
          ...options, timeout: 500,
        })) as typeof page.waitForSelector;
        await joinGoogleMeeting(page, 'https://meet.test/example', 'カボス', {
          platform: 'google_meet', authenticated: true, uiInteractionMode: 'synthetic',
        } as BotConfig);
        assert.equal(await page.getAttribute('body', 'data-clicked'), 'join', `${label} ariaOnly=${ariaOnly}`);
        await page.close();
      }
    }
    const page = await browser.newPage();
    await page.setContent('<button id="late">参加をリクエスト</button>');
    const result = await waitForAnySelector(page, ['invalid[', '#late'], 500, 'join-test');
    assert.equal(result.selector, '#late', 'one rejected selector must not defeat a matching selector');
    await assert.rejects(waitForAnySelector(page, ['#missing'], 30, 'missing-test'));
    // Exercise every selector so invalid selector engines cannot silently hide failures.
    for (const selector of [...googleWaitingRoomIndicators, ...googleRejectionIndicators]) {
      await page.locator(selector).count();
    }
    page.screenshot = async () => Buffer.alloc(0);
    for (const text of ['参加をリクエストしています...', '主催者が参加を承認するまでお待ちください', 'Asking to be let in...']) {
      awaitingCallbacks = 0;
      await page.setContent(`<p>${text}</p>`);
      assert.equal(await checkForWaitingRoomIndicators(page), true, text);
      assert.equal(await checkForGoogleRejection(page), false, text);
      await assert.rejects(
        waitForGoogleMeetingAdmission(page, 0, {} as BotConfig),
        (error: unknown) => error instanceof AdmissionError && error.outcome === 'lobby_timeout',
        'a waiting room timeout must not be classified as a connection failure',
      );
      assert.equal(awaitingCallbacks, 1, 'notify awaiting_admission before waiting for the host');
    }
    awaitingCallbacks = 0;
    await page.setContent('<p>参加をリクエストしています...</p>');
    page.waitForTimeout = async () => { await page.setContent('<div data-participant-id="fixture">参加者</div>'); };
    assert.equal(await waitForGoogleMeetingAdmission(page, 5000, {} as BotConfig), true);
    assert.equal(awaitingCallbacks, 1, 'waiting → admitted preserves the awaiting_admission callback');
    for (const text of ['参加リクエストが拒否されました', 'The host denied your request to join']) {
      await page.setContent(`<p>${text}</p>`);
      assert.equal(await checkForGoogleRejection(page), true, text);
      await assert.rejects(
        waitForGoogleMeetingAdmission(page, 0, {} as BotConfig),
        (error: unknown) => error instanceof AdmissionError && error.outcome === 'denial',
      );
    }
    await page.setContent('<p>会議に参加しました</p><div data-participant-id="fixture"></div>');
    assert.equal(await waitForGoogleMeetingAdmission(page, 0, {} as BotConfig), true);
    await page.close();
  } finally {
    callbacks.callAwaitingAdmissionCallback = originalAwaitingCallback;
    await browser.close();
  }
  console.log('Authenticated Google Meet join regressions passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
