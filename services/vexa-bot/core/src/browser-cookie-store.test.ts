import { mkdtempSync, mkdirSync, writeFileSync, existsSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import {
  CookieCapableContext,
  StoredCookie,
  cookieStorePath,
  persistCookies,
  readStoredCookies,
  restoreCookies,
} from './browser-cookie-store';
import { createBrowserDataSaver } from './browser-data-saver';
import { AUTH_ESSENTIAL_FILES, CDP_COOKIE_FILE } from './s3-sync';

let passed = 0;
let failed = 0;

function expect(name: string, actual: unknown, expected: unknown) {
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

const SECRET_VALUE = 'super-secret-session-token';

class FakeContext implements CookieCapableContext {
  added: StoredCookie[][] = [];
  constructor(private readonly stored: StoredCookie[] = []) {}
  async cookies(): Promise<StoredCookie[]> {
    return this.stored;
  }
  async addCookies(cookies: StoredCookie[]): Promise<void> {
    this.added.push(cookies);
  }
}

function tempStore(): string {
  return join(mkdtempSync(join(tmpdir(), 'cookie-store-test-')), 'Default', 'cdp-cookies.json');
}

async function main() {
  // AT-007 — the exported cookie file is uploaded with the rest of the profile.
  expect('cdp-cookies.json is in AUTH_ESSENTIAL_FILES', AUTH_ESSENTIAL_FILES.includes(CDP_COOKIE_FILE), true);
  expect('cookie store path lives under the browser data dir', cookieStorePath('/data'), '/data/Default/cdp-cookies.json');

  // AT-004 — export then import restores the same cookie set.
  const cookies: StoredCookie[] = [
    { name: 'SID', value: SECRET_VALUE, domain: '.google.com', path: '/', httpOnly: true, secure: true },
    { name: 'HSID', value: 'another-secret', domain: '.google.com', path: '/' },
  ];
  const roundTripPath = tempStore();
  const exportLogs: string[] = [];
  const exported = await persistCookies(new FakeContext(cookies), {
    path: roundTripPath,
    log: (m) => exportLogs.push(m),
  });
  expect('export writes every cookie', exported, 2);

  const importContext = new FakeContext();
  const importLogs: string[] = [];
  const restored = await restoreCookies(importContext, {
    path: roundTripPath,
    log: (m) => importLogs.push(m),
  });
  expect('import applies every cookie', restored, 2);
  expect('imported cookie set equals the exported one', importContext.added[0], cookies);

  // FP-003 — no cookie value ever reaches the log.
  const allLogs = [...exportLogs, ...importLogs].join('\n');
  expect('export/import logs contain no cookie values', allLogs.includes(SECRET_VALUE), false);

  const throwingContext: CookieCapableContext = {
    async cookies() {
      throw new Error(`boom ${SECRET_VALUE}`);
    },
    async addCookies() {
      throw new Error(`boom ${SECRET_VALUE}`);
    },
  };
  const failLogs: string[] = [];
  const restoreFailed = await restoreCookies(throwingContext, {
    path: roundTripPath,
    log: (m) => failLogs.push(m),
  });
  const persistFailed = await persistCookies(throwingContext, {
    path: tempStore(),
    log: (m) => failLogs.push(m),
  });
  expect('failed restore reports zero cookies', restoreFailed, 0);
  expect('failed export reports zero cookies', persistFailed, 0);
  expect('failure logs contain no cookie values', failLogs.join('\n').includes(SECRET_VALUE), false);

  // AT-005 — missing / empty / corrupt / malformed files only warn.
  const missingLogs: string[] = [];
  expect('missing file yields no cookies', readStoredCookies({ path: tempStore(), log: (m) => missingLogs.push(m) }), []);
  expect('missing file warns once', missingLogs.length, 1);

  const emptyPath = tempStore();
  mkdirSync(join(emptyPath, '..'), { recursive: true });
  writeFileSync(emptyPath, '   ');
  const emptyLogs: string[] = [];
  expect('empty file yields no cookies', readStoredCookies({ path: emptyPath, log: (m) => emptyLogs.push(m) }), []);
  expect('empty file warns once', emptyLogs.length, 1);

  const corruptPath = tempStore();
  mkdirSync(join(corruptPath, '..'), { recursive: true });
  writeFileSync(corruptPath, '{not json');
  const corruptLogs: string[] = [];
  expect('corrupt file yields no cookies', readStoredCookies({ path: corruptPath, log: (m) => corruptLogs.push(m) }), []);
  expect('corrupt file warns once', corruptLogs.length, 1);

  const malformedPath = tempStore();
  mkdirSync(join(malformedPath, '..'), { recursive: true });
  writeFileSync(
    malformedPath,
    JSON.stringify([{ name: 'SID', value: SECRET_VALUE, domain: '.google.com', path: '/' }, { nope: 1 }, 'string']),
  );
  const malformedLogs: string[] = [];
  const malformedCookies = readStoredCookies({ path: malformedPath, log: (m) => malformedLogs.push(m) });
  expect('malformed entries are dropped, valid ones kept', malformedCookies.length, 1);
  expect('malformed warning contains no cookie values', malformedLogs.join('\n').includes(SECRET_VALUE), false);

  const notArrayPath = tempStore();
  mkdirSync(join(notArrayPath, '..'), { recursive: true });
  writeFileSync(notArrayPath, JSON.stringify({ cookies: [] }));
  expect('non-array file yields no cookies', readStoredCookies({ path: notArrayPath, log: () => {} }), []);

  // AT-006 — concurrent saves are serialized, cookies always written before the S3 sync.
  const order: string[] = [];
  let inFlight = 0;
  let overlapped = false;
  const saver = createBrowserDataSaver({
    context: new FakeContext(cookies),
    config: {},
    saveCookies: async () => {
      if (inFlight > 0) overlapped = true;
      inFlight++;
      order.push('cookies');
      await new Promise((resolve) => setTimeout(resolve, 5));
      return 2;
    },
    syncToS3: () => {
      order.push('s3');
      inFlight--;
    },
  });
  await Promise.all([saver(), saver(), saver()]);
  expect('saves never overlap', overlapped, false);
  expect('each save writes cookies before syncing to S3', order, [
    'cookies', 's3', 'cookies', 's3', 'cookies', 's3',
  ]);

  // A failed save must not wedge the queue for later saves.
  let attempts = 0;
  const flakySaver = createBrowserDataSaver({
    context: new FakeContext(cookies),
    config: {},
    saveCookies: async () => {
      attempts++;
      if (attempts === 1) throw new Error('transient');
      return 0;
    },
    syncToS3: () => {},
  });
  await flakySaver().then(() => 'ok', () => 'rejected');
  const second = await flakySaver().then(() => 'ok', () => 'rejected');
  expect('a failed save does not block the next one', second, 'ok');

  expect('round-trip file was actually written', existsSync(roundTripPath), true);

  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed > 0 ? 1 : 0);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
