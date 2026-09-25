/**
 * Structural tests for the bot-side voice agent guard (AT-004).
 *
 * index.ts boots Playwright/Redis at import time, so it cannot be imported
 * here. Instead we read the source and assert that the `speak` and
 * `speak_audio` Redis command branches both start with the same
 * voiceAgentEnabled===false guard: a log line naming the command and an
 * early return that makes only that command a no-op.
 */

import * as fs from 'fs';
import * as path from 'path';

let passed = 0;
let failed = 0;

function expect(name: string, actual: unknown, expected: unknown) {
  if (actual === expected) {
    console.log(`PASS ${name}`);
    passed++;
  } else {
    console.log(`FAIL ${name}`);
    console.log(`  expected: ${JSON.stringify(expected)}`);
    console.log(`  actual:   ${JSON.stringify(actual)}`);
    failed++;
  }
}

// Resolve the source whether this test runs from src/ (tsx) or dist/ (node).
const INDEX_TS_CANDIDATES = [
  path.join(__dirname, 'index.ts'),
  path.join(__dirname, '..', 'src', 'index.ts'),
];
const INDEX_TS = INDEX_TS_CANDIDATES.find((p) => fs.existsSync(p)) ?? INDEX_TS_CANDIDATES[0];
const source = fs.readFileSync(INDEX_TS, 'utf-8');

const GUARD = "currentBotConfig?.voiceAgentEnabled === false";

/** Body of the `command.action === '<action>'` dispatch branch. */
function branchBody(action: string): string {
  const start = source.indexOf(`} else if (command.action === '${action}') {`);
  if (start < 0) return '';
  const rest = source.slice(start + 1);
  const end = rest.indexOf('} else if (command.action ===');
  return end < 0 ? rest : rest.slice(0, end);
}

for (const action of ['speak', 'speak_audio']) {
  const body = branchBody(action);
  expect(`${action} branch exists in the dispatch`, body.length > 0, true);

  const guardIndex = body.indexOf(GUARD);
  expect(`${action} branch has the voiceAgentEnabled=false guard`, guardIndex >= 0, true);

  const logIndex = body.indexOf(`ignoring ${action} command`);
  expect(`${action} branch logs the disabled command by name`, logIndex >= 0, true);
  expect(`${action} disabled log sits inside the guard`, logIndex > guardIndex, true);

  const returnIndex = body.indexOf('return;');
  expect(`${action} branch returns early when disabled`, returnIndex >= 0, true);
  expect(`${action} early return follows the log`, returnIndex > logIndex, true);

  // The guard must run before any playback work is dispatched.
  const handlerIndex = body.indexOf(action === 'speak' ? 'handleSpeakCommand(' : 'handleSpeakAudioCommand(');
  expect(`${action} branch still calls its handler`, handlerIndex >= 0, true);
  expect(`${action} guard precedes the handler call`, guardIndex < handlerIndex, true);
}

// The guard must be exact-false so a missing/undefined flag stays enabled.
expect(
  'guard uses strict === false so undefined keeps speaking',
  source.includes('currentBotConfig?.voiceAgentEnabled === false'),
  true,
);
expect(
  'guard does not use a truthiness check that would mute legacy bots',
  source.includes('if (!currentBotConfig?.voiceAgentEnabled) {'),
  false,
);

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
