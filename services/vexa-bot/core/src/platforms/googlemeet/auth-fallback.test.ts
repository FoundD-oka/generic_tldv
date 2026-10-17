import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import { joinGoogleMeeting } from './join';
import { isGoogleSignInUrl, shouldSaveGoogleBrowserProfile, withGoogleAuthFallback } from './auth-fallback';
import { BotConfig } from '../../types';
import { callStatusChangeCallback } from '../../services/unified-callback';

const guestLobby = `<input jsname="name" type="text"><button id="join"><span>Ask to join</span></button>
  <script>document.querySelector('#join').onclick=()=>document.body.dataset.joined=document.querySelector('input').value;</script>`;

async function main() {
  assert.equal(isGoogleSignInUrl('https://accounts.google.com/v3/signin/confirmidentifier'), true);
  assert.equal(isGoogleSignInUrl('https://accounts.google.com.example.org/signin'), false);
  assert.equal(isGoogleSignInUrl('https://meet.google.com/abc-defg-hij'), false);
  const browser = await chromium.launch({ headless: true,
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH } : {}),
  });
  const originalFetch = globalThis.fetch;
  try {
    for (const scenario of ['expired', 'anonymous', 'late_redirect', 'guest_blocked', 'valid', 'ordinary_guest', 'missing_control', 'denied', 'network_error']) {
      const callbacks: any[] = [];
      globalThis.fetch = async (_input, init) => {
        callbacks.push(JSON.parse(init!.body as string));
        return new Response(JSON.stringify({ status: 'processed' }), { status: 200 });
      };
      const context = await browser.newContext();
      await context.addCookies([{ name: 'fixture_auth', value: 'expired', domain: '.google.com', path: '/' }]);
      const page = await context.newPage();
      let meetVisits = 0;
      let guestCookies = '';
      let guestUrl = '';
      await context.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.hostname === 'accounts.google.com') {
          if (url.pathname === '/storage-check') return route.fulfill({ contentType: 'text/html', body: '<h1>Storage check</h1>' });
          return route.fulfill({ contentType: 'text/html', body: '<h1>Sign in</h1><input type="email"><script>localStorage.setItem("expired_profile", "yes")</script>' });
        }
        if (url.hostname !== 'meet.google.com') return route.abort();
        meetVisits++;
        const cookies = route.request().headers()['cookie'] || '';
        if (scenario === 'network_error') return route.abort('failed');
        if (meetVisits === 1 && ['expired', 'guest_blocked'].includes(scenario)) {
          return route.fulfill({ status: 302, headers: { location: 'https://accounts.google.com/v3/signin/confirmidentifier' } });
        }
        let body = guestLobby;
        if (scenario === 'late_redirect' && meetVisits === 1) {
          body = `<button aria-label="Turn off microphone">Mic</button><button aria-label="Turn off camera">Camera</button>
            <script>setTimeout(()=>location.href='https://accounts.google.com/v3/signin/confirmidentifier', 100)</script>`;
        } else if (scenario === 'valid') {
          body = '<button id="join">Ask to join</button><script>document.querySelector("#join").onclick=()=>document.body.dataset.joined="signed-in";</script>';
        } else if (['missing_control', 'guest_blocked', 'denied'].includes(scenario)) {
          body = scenario === 'denied' ? '<h1>The host denied your request to join</h1>' : '<h1>No join controls</h1>';
        }
        if (meetVisits === 2) { guestCookies = cookies; guestUrl = url.toString(); }
        return route.fulfill({ contentType: 'text/html; charset=utf-8', body });
      });
      page.screenshot = async () => Buffer.alloc(0);
      page.waitForTimeout = async () => {};
      const originalWait = page.waitForSelector.bind(page);
      page.waitForSelector = ((selector: string, options: any) => originalWait(selector, { ...options, timeout: 350 })) as typeof page.waitForSelector;
      const config = {
        platform: 'google_meet', botName: 'カボス', authenticated: scenario !== 'ordinary_guest',
        uiInteractionMode: 'synthetic', userdataS3Path: 'users/fixture/browser-userdata',
        meetingApiCallbackUrl: 'http://fixture/bots/internal/callback/exited', connectionId: 'fixture',
      } as BotConfig;
      const join = joinGoogleMeeting(page, 'https://meet.google.com/abc-defg-hij?authuser=0', 'カボス', config);
      if (['guest_blocked', 'missing_control', 'denied', 'network_error'].includes(scenario)) {
        await assert.rejects(join);
      } else {
        await join;
        assert.equal(await page.getAttribute('body', 'data-joined'), scenario === 'valid' ? 'signed-in' : 'カボス', scenario);
      }
      const expectedFallback = ['expired', 'anonymous', 'late_redirect', 'guest_blocked'].includes(scenario);
      assert.equal(!!config.googleAuthFallback, expectedFallback, scenario);
      assert.equal(meetVisits, expectedFallback ? 2 : 1, `${scenario}: at most one guest retry`);
      assert.equal(shouldSaveGoogleBrowserProfile(config), !expectedFallback && scenario !== 'ordinary_guest', scenario);
      if (expectedFallback) {
        assert.equal(guestCookies, '', 'the guest retry cannot reuse expired Google cookies');
        assert.equal(guestUrl, 'https://meet.google.com/abc-defg-hij', 'guest URL strips account selectors');
        assert.equal(callbacks[1].google_auth_fallback.reason, scenario === 'anonymous' ? 'anonymous_lobby' : 'google_sign_in_required');
        assert.ok(callbacks[1].google_auth_fallback.occurred_at);
        const marker = callbacks[1].google_auth_fallback;
        await callStatusChangeCallback(config, scenario === 'guest_blocked' ? 'failed' : 'active');
        assert.deepEqual(callbacks[2].google_auth_fallback, marker, 'history survives subsequent status callbacks');
        // The real exit path (index.ts performGracefulLeave) sends a freshly built config.
        const exitConfig = withGoogleAuthFallback({
          meetingApiCallbackUrl: config.meetingApiCallbackUrl, connectionId: 'fixture', container_name: 'fixture',
        }, config);
        await callStatusChangeCallback(exitConfig, 'failed', 'join_failure', 1);
        assert.deepEqual(callbacks[3].google_auth_fallback, marker, 'exit callback keeps the history even when the guest retry fails');
        await page.goto('https://accounts.google.com/storage-check');
        assert.equal(await page.evaluate(() => localStorage.getItem('expired_profile')), null, 'expired account storage is cleared');
      } else {
        assert.ok(callbacks.every(payload => !('google_auth_fallback' in payload)), scenario);
        assert.ok(!('googleAuthFallback' in withGoogleAuthFallback({ connectionId: 'fixture' }, config)), `${scenario}: no history on exit`);
      }
      await context.close();
      console.log(`PASS Google auth fallback: ${scenario}`);
    }
  } finally {
    globalThis.fetch = originalFetch;
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
