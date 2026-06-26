import * as fs from 'fs';
import * as path from 'path';
import { logJSON } from '../utils/log';

const TMP_DIR = '/tmp';

// Orphan = a recording temp file left behind by a prior bot run that was
// SIGKILLed before its finally-block cleanup ran. We only sweep files OLDER
// than this threshold — comfortably above max meeting duration — so we never
// race a concurrent/active recording in the same container.
const DEFAULT_MAX_AGE_MS = 6 * 60 * 60 * 1000; // 6h

// Covers /tmp/recording_<id>_<session>.wav and
// /tmp/video_recording_<id>_<session>.{webm,mp4} plus the muxed variant
// (video_recording_..._muxed.<fmt>), which shares the video prefix.
const RECORDING_PREFIXES = ['recording_', 'video_recording_'];

function isRecordingTempFile(name: string): boolean {
  return RECORDING_PREFIXES.some((p) => name.startsWith(p));
}

export interface SweepOptions {
  maxAgeMs?: number;
  excludeSessionUid?: string;
  now?: number;
  tmpDir?: string;
}

/**
 * Best-effort sweep of orphaned recording temp files in /tmp.
 *
 * The bot already deletes its own temp files in a `finally` block after upload
 * (on success OR failure — see index.ts graceful-leave). This sweep is purely a
 * SIGKILL safety net: if the process is hard-killed before that `finally` runs,
 * the next bot start reclaims the disk. Issue #1 — bot-side local temp files
 * must not accumulate.
 *
 * Returns the list of deleted paths (also for testability).
 */
export async function sweepOrphanRecordings(opts: SweepOptions = {}): Promise<string[]> {
  const maxAgeMs = opts.maxAgeMs ?? DEFAULT_MAX_AGE_MS;
  const now = opts.now ?? Date.now();
  const tmpDir = opts.tmpDir ?? TMP_DIR;
  const deleted: string[] = [];

  let entries: string[];
  try {
    entries = await fs.promises.readdir(tmpDir);
  } catch (err: any) {
    logJSON({ level: 'warn', msg: '[RecordingSweep] readdir failed', tmp_dir: tmpDir, error_message: err?.message });
    return deleted;
  }

  for (const name of entries) {
    if (!isRecordingTempFile(name)) continue;
    // Never touch the current session's files (defensive — they normally don't
    // exist yet at startup, but a same-container retry could re-use the dir).
    if (opts.excludeSessionUid && name.includes(opts.excludeSessionUid)) continue;
    const full = path.join(tmpDir, name);
    try {
      const stat = await fs.promises.stat(full);
      if (!stat.isFile()) continue;
      const ageMs = now - stat.mtimeMs;
      if (ageMs < maxAgeMs) continue;
      await fs.promises.unlink(full);
      deleted.push(full);
      logJSON({
        level: 'info',
        msg: '[RecordingSweep] removed orphan recording temp file',
        path: full,
        age_ms: Math.round(ageMs),
      });
    } catch (err: any) {
      logJSON({ level: 'warn', msg: '[RecordingSweep] failed to remove orphan', path: full, error_message: err?.message });
    }
  }

  if (deleted.length) {
    logJSON({ level: 'info', msg: '[RecordingSweep] sweep complete', removed_count: deleted.length });
  }
  return deleted;
}
