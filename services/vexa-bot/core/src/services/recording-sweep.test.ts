/**
 * Standalone test for sweepOrphanRecordings — the SIGKILL safety net that
 * reclaims recording temp files left behind by a hard-killed prior bot run
 * (issue #1).
 *
 * Run: npx tsx services/vexa-bot/core/src/services/recording-sweep.test.ts
 */

import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { sweepOrphanRecordings } from './recording-sweep';

let passed = 0;
let failed = 0;

function expect(name: string, actual: any, expected: any) {
  if (JSON.stringify(actual) === JSON.stringify(expected)) {
    console.log(`  \x1b[32mPASS\x1b[0m  ${name}`);
    passed++;
  } else {
    console.log(`  \x1b[31mFAIL\x1b[0m  ${name}`);
    console.log(`        expected: ${JSON.stringify(expected)}`);
    console.log(`        actual:   ${JSON.stringify(actual)}`);
    failed++;
  }
}

function mkTmpDir(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'sweep-test-'));
}

function touch(dir: string, name: string, ageMs: number, now: number) {
  const full = path.join(dir, name);
  fs.writeFileSync(full, 'x');
  const t = (now - ageMs) / 1000;
  fs.utimesSync(full, t, t);
  return full;
}

async function run() {
  const now = 1_000_000_000_000;
  const maxAgeMs = 6 * 60 * 60 * 1000; // 6h

  // Case 1: old orphans of every recording-temp shape are removed.
  {
    const dir = mkTmpDir();
    touch(dir, 'recording_42_sessA.wav', maxAgeMs + 1000, now);
    touch(dir, 'video_recording_42_sessA.webm', maxAgeMs + 1000, now);
    touch(dir, 'video_recording_42_sessA_muxed.webm', maxAgeMs + 1000, now);
    const deleted = await sweepOrphanRecordings({ tmpDir: dir, now, maxAgeMs });
    expect('removes all 3 old orphan shapes', deleted.length, 3);
    expect('dir empty after sweep', fs.readdirSync(dir).length, 0);
    fs.rmSync(dir, { recursive: true, force: true });
  }

  // Case 2: fresh files (younger than threshold) are kept — never race active recordings.
  {
    const dir = mkTmpDir();
    touch(dir, 'recording_99_sessFresh.wav', 1000, now); // 1s old
    const deleted = await sweepOrphanRecordings({ tmpDir: dir, now, maxAgeMs });
    expect('keeps fresh recording', deleted.length, 0);
    expect('fresh file still present', fs.existsSync(path.join(dir, 'recording_99_sessFresh.wav')), true);
    fs.rmSync(dir, { recursive: true, force: true });
  }

  // Case 3: non-recording files are never touched.
  {
    const dir = mkTmpDir();
    touch(dir, 'some-other-file.txt', maxAgeMs + 1000, now);
    const deleted = await sweepOrphanRecordings({ tmpDir: dir, now, maxAgeMs });
    expect('ignores non-recording files', deleted.length, 0);
    fs.rmSync(dir, { recursive: true, force: true });
  }

  // Case 4: the current session's files are excluded even when old.
  {
    const dir = mkTmpDir();
    touch(dir, 'recording_7_sessCURRENT.wav', maxAgeMs + 1000, now);
    const deleted = await sweepOrphanRecordings({ tmpDir: dir, now, maxAgeMs, excludeSessionUid: 'sessCURRENT' });
    expect('excludes current session files', deleted.length, 0);
    fs.rmSync(dir, { recursive: true, force: true });
  }

  // Case 5: a missing tmp dir is handled gracefully (no throw).
  {
    const deleted = await sweepOrphanRecordings({ tmpDir: '/nonexistent/sweep/dir', now, maxAgeMs });
    expect('missing dir returns empty', deleted.length, 0);
  }

  console.log(`\n${passed} passed, ${failed} failed`);
  if (failed > 0) process.exit(1);
}

run();
