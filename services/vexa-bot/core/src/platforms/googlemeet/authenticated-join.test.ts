import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { chromium } from 'playwright';
import { joinGoogleMeeting, waitForAnySelector } from './join';
import { BotConfig } from '../../types';

async function main() {
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
    await page.close();
  } finally {
    await browser.close();
  }
  console.log('Authenticated Google Meet join regressions passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
