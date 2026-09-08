import { S3Config, syncBrowserDataToS3 } from './s3-sync';
import { CookieCapableContext, CookieStoreLogger, persistCookies } from './browser-cookie-store';

export interface BrowserDataSaverDeps {
  context: CookieCapableContext;
  config: S3Config;
  /** Overridable for tests. Defaults to writing Default/cdp-cookies.json. */
  saveCookies?: (context: CookieCapableContext) => Promise<number>;
  /** Overridable for tests. Defaults to the unchanged syncBrowserDataToS3. */
  syncToS3?: (config: S3Config) => void;
  log?: CookieStoreLogger;
}

/**
 * Build the single save entry point used by every browser-session path
 * (manual save_storage, stop, leave, SIGTERM/SIGINT, and the 60s auto-save).
 *
 * Calls are serialized through one promise chain, and each run writes the CDP
 * cookie file *before* uploading, so an upload never races a cookie export or
 * ships a half-written file.
 */
export function createBrowserDataSaver(deps: BrowserDataSaverDeps): () => Promise<void> {
  const saveCookies =
    deps.saveCookies ?? ((context: CookieCapableContext) => persistCookies(context, { log: deps.log }));
  const syncToS3 = deps.syncToS3 ?? syncBrowserDataToS3;

  let queue: Promise<void> = Promise.resolve();

  return () => {
    const run = queue.then(async () => {
      await saveCookies(deps.context);
      syncToS3(deps.config);
    });
    // Keep the chain alive after a failed save so later saves still run.
    queue = run.catch(() => {});
    return run;
  };
}
