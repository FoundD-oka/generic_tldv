import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'fs';
import { dirname, join } from 'path';
import { BROWSER_DATA_DIR, CDP_COOKIE_FILE } from './s3-sync';

// Cookie names and values are secrets: nothing in this module ever logs them,
// and error text coming from the browser context is deliberately not echoed.

export interface StoredCookie {
  name: string;
  value: string;
  domain?: string;
  path?: string;
  url?: string;
  expires?: number;
  httpOnly?: boolean;
  secure?: boolean;
  sameSite?: 'Strict' | 'Lax' | 'None';
}

/** The slice of Playwright's BrowserContext this module needs. */
export interface CookieCapableContext {
  cookies(): Promise<StoredCookie[]>;
  addCookies(cookies: StoredCookie[]): Promise<void>;
}

export type CookieStoreLogger = (message: string) => void;

export interface CookieStoreOptions {
  path?: string;
  log?: CookieStoreLogger;
}

const defaultLog: CookieStoreLogger = (message) => console.log(message);

export function cookieStorePath(dataDir: string = BROWSER_DATA_DIR): string {
  return join(dataDir, CDP_COOKIE_FILE);
}

function isValidCookie(value: unknown): value is StoredCookie {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Record<string, unknown>;
  if (typeof candidate.name !== 'string' || candidate.name.length === 0) return false;
  if (typeof candidate.value !== 'string') return false;
  const hasDomainAndPath =
    typeof candidate.domain === 'string' && candidate.domain.length > 0 &&
    typeof candidate.path === 'string' && candidate.path.length > 0;
  const hasUrl = typeof candidate.url === 'string' && candidate.url.length > 0;
  return hasDomainAndPath || hasUrl;
}

/**
 * Read the saved cookie file. Never throws: missing, empty, corrupt or malformed
 * content yields an empty list plus a warning so browser startup keeps going.
 */
export function readStoredCookies(options: CookieStoreOptions = {}): StoredCookie[] {
  const path = options.path ?? cookieStorePath();
  const log = options.log ?? defaultLog;

  if (!existsSync(path)) {
    log('[cookie-store] No saved cookie file found, skipping restore');
    return [];
  }

  let raw: string;
  try {
    raw = readFileSync(path, 'utf8');
  } catch {
    log('[cookie-store] Warning: saved cookie file could not be read, skipping restore');
    return [];
  }

  if (raw.trim().length === 0) {
    log('[cookie-store] Warning: saved cookie file is empty, skipping restore');
    return [];
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    log('[cookie-store] Warning: saved cookie file is not valid JSON, skipping restore');
    return [];
  }

  if (!Array.isArray(parsed)) {
    log('[cookie-store] Warning: saved cookie file is not an array, skipping restore');
    return [];
  }

  const valid = parsed.filter(isValidCookie);
  if (valid.length !== parsed.length) {
    log(`[cookie-store] Warning: dropped ${parsed.length - valid.length} malformed cookie entries`);
  }
  return valid;
}

/** Re-apply saved cookies to a freshly launched context. Returns how many were applied. */
export async function restoreCookies(
  context: CookieCapableContext,
  options: CookieStoreOptions = {},
): Promise<number> {
  const log = options.log ?? defaultLog;
  const cookies = readStoredCookies(options);
  if (cookies.length === 0) return 0;

  try {
    await context.addCookies(cookies);
  } catch {
    log('[cookie-store] Warning: cookie restore failed, continuing without saved cookies');
    return 0;
  }
  log(`[cookie-store] Restored ${cookies.length} cookies`);
  return cookies.length;
}

/** Export every cookie (including in-memory session cookies) to disk. */
export async function persistCookies(
  context: CookieCapableContext,
  options: CookieStoreOptions = {},
): Promise<number> {
  const path = options.path ?? cookieStorePath();
  const log = options.log ?? defaultLog;

  let cookies: StoredCookie[];
  try {
    cookies = await context.cookies();
  } catch {
    log('[cookie-store] Warning: could not read cookies from the browser context');
    return 0;
  }

  const valid = Array.isArray(cookies) ? cookies.filter(isValidCookie) : [];
  try {
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, JSON.stringify(valid), { mode: 0o600 });
  } catch {
    log('[cookie-store] Warning: failed to write the cookie file');
    return 0;
  }
  log(`[cookie-store] Saved ${valid.length} cookies`);
  return valid.length;
}
