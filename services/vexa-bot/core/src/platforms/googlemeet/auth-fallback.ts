import { Frame, Page } from 'playwright';
import { BotConfig, GoogleAuthFallback } from '../../types';
import { googleNameInputSelectors } from './selectors';

export class GoogleAuthenticationRequired extends Error {
  constructor(readonly reason: GoogleAuthFallback['reason']) {
    super('Google sign-in required; retrying as a guest');
  }
}

export function isGoogleSignInUrl(url: string): boolean {
  try {
    return new URL(url).hostname === 'accounts.google.com';
  } catch {
    return false;
  }
}

export async function detectGoogleAuthFailure(page: Page): Promise<void> {
  if (isGoogleSignInUrl(page.url())) {
    throw new GoogleAuthenticationRequired('google_sign_in_required');
  }
  if (new URL(page.url()).hostname !== 'meet.google.com') return;
  for (const selector of googleNameInputSelectors) {
    if (await page.locator(selector).first().isVisible()) {
      throw new GoogleAuthenticationRequired('anonymous_lobby');
    }
  }
}

// Catch late redirects while the SPA is loading, rather than waiting the entire
// join-button timeout. Only the main frame can establish a sign-in failure.
export async function watchGoogleSignIn<T>(page: Page, operation: () => Promise<T>): Promise<T> {
  let onNavigate: (frame: Frame) => void = () => {};
  const redirected = new Promise<never>((_, reject) => {
    onNavigate = (frame) => {
      if (frame === page.mainFrame() && isGoogleSignInUrl(frame.url())) {
        reject(new GoogleAuthenticationRequired('google_sign_in_required'));
      }
    };
    page.on('framenavigated', onNavigate);
  });
  try {
    return await Promise.race([redirected, (async () => {
      await detectGoogleAuthFailure(page);
      return operation();
    })()]);
  } finally {
    page.off('framenavigated', onNavigate);
  }
}

export async function resetGoogleMeetForGuest(
  page: Page, config: BotConfig, reason: GoogleAuthFallback['reason'],
): Promise<void> {
  // Mark before any fallible cleanup: terminal callbacks must retain the history
  // and graceful shutdown must not upload this cleared profile to shared storage.
  config.googleAuthFallback = { reason, occurred_at: new Date().toISOString() };
  await page.goto('about:blank');
  const context = page.context();
  await context.clearCookies();
  const cdp = await context.newCDPSession(page);
  try {
    for (const origin of ['https://accounts.google.com', 'https://meet.google.com', 'https://google.com', 'https://www.google.com']) {
      await cdp.send('Storage.clearDataForOrigin', { origin, storageTypes: 'all' });
    }
  } finally {
    await cdp.detach();
  }
}

export function shouldSaveGoogleBrowserProfile(config: BotConfig): boolean {
  return !!config.authenticated && !!config.userdataS3Path && !config.googleAuthFallback;
}

// The exit callback is built from a fresh object; carry the runtime history so
// a failed guest retry still reports the Google sign-in failure.
export function withGoogleAuthFallback<T extends object>(
  callbackConfig: T, config: BotConfig | null | undefined,
): T & { googleAuthFallback?: GoogleAuthFallback } {
  return config?.googleAuthFallback
    ? { ...callbackConfig, googleAuthFallback: config.googleAuthFallback }
    : callbackConfig;
}
