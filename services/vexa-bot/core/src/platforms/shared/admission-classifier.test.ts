/**
 * Unit + structural tests for admission outcome classification.
 *
 * classifyAdmissionError turns a waitForAdmission rejection into an
 * AdmissionDecision, preferring the AdmissionError.outcome field and falling
 * back to the legacy message match for platforms that throw plain Errors.
 */

import * as fs from 'fs';
import * as path from 'path';
import { classifyAdmissionError } from './admission-classifier';
import { mapExitReasonToStatus } from '../../services/unified-callback';

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

function expectFileContains(name: string, filePath: string, needle: string) {
  const body = fs.readFileSync(filePath, 'utf-8');
  expect(name, body.includes(needle), true);
}

function expectFileNotContains(name: string, filePath: string, needle: string) {
  const body = fs.readFileSync(filePath, 'utf-8');
  expect(name, body.includes(needle), false);
}

function expectFileOccurrences(name: string, filePath: string, needle: string, count: number) {
  const body = fs.readFileSync(filePath, 'utf-8');
  expect(name, body.split(needle).length - 1, count);
}

const MEETING_FLOW_TS = path.join(__dirname, 'meetingFlow.ts');

// --- AT-002: classifyAdmissionError -----------------------------------------
const denial = classifyAdmissionError({ outcome: 'denial', message: 'Bot admission was rejected by meeting admin' });
expect('denial outcome is rejected', denial.rejected, true);
expect('denial outcome reason', denial.reason, 'admission_rejected_by_admin');

const lobbyTimeout = classifyAdmissionError({ outcome: 'lobby_timeout', message: 'Bot is still in the Google Meet waiting room after timeout - not admitted to the meeting' });
expect('lobby_timeout outcome is not rejected', lobbyTimeout.rejected, false);
expect('lobby_timeout outcome reason', lobbyTimeout.reason, 'admission_timeout');

const joinFailure = classifyAdmissionError({ outcome: 'join_failure', message: 'Bot failed to join the Google Meet meeting' });
expect('join_failure outcome is not rejected', joinFailure.rejected, false);
expect('join_failure outcome reason', joinFailure.reason, 'join_failure');

const plainRejected = classifyAdmissionError(new Error('Bot admission was rejected by meeting admin'));
expect('plain rejection Error is rejected', plainRejected.rejected, true);
expect('plain rejection Error reason', plainRejected.reason, 'admission_rejected_by_admin');

const plainTimeout = classifyAdmissionError(new Error('Bot is still in the Teams waiting room after timeout'));
expect('plain timeout Error is not rejected', plainTimeout.rejected, false);
expect('plain timeout Error reason', plainTimeout.reason, 'admission_timeout');

// --- AT-003: mapExitReasonToStatus ------------------------------------------
const exitJoinFailure = mapExitReasonToStatus('join_failure', 0);
expect('exit 0 join_failure status', exitJoinFailure.status, 'completed');
expect('exit 0 join_failure completionReason', exitJoinFailure.completionReason, 'join_failure');

const exitAdmissionTimeout = mapExitReasonToStatus('admission_timeout', 0);
expect('exit 0 admission_timeout status', exitAdmissionTimeout.status, 'completed');
expect('exit 0 admission_timeout completionReason', exitAdmissionTimeout.completionReason, 'awaiting_admission_timeout');

const exitRejected = mapExitReasonToStatus('admission_rejected_by_admin', 0);
expect('exit 0 admission_rejected_by_admin status', exitRejected.status, 'completed');
expect('exit 0 admission_rejected_by_admin completionReason', exitRejected.completionReason, 'awaiting_admission_rejected');

const exitJoinMeetingError = mapExitReasonToStatus('join_meeting_error', 1);
expect('exit 1 join_meeting_error status', exitJoinMeetingError.status, 'failed');
expect('exit 1 join_meeting_error completionReason', exitJoinMeetingError.completionReason, 'join_failure');

// --- AT-004: meetingFlow.ts uses the classifier -----------------------------
expectFileContains(
  'meetingFlow.ts uses classifyAdmissionError',
  MEETING_FLOW_TS,
  'classifyAdmissionError',
);

expectFileNotContains(
  'meetingFlow.ts has no inline admission message match',
  MEETING_FLOW_TS,
  'msg.includes("rejected by meeting admin")',
);

// --- FP-002: non-admitted graceful leave keeps exit code 0 ------------------
expectFileOccurrences(
  'meetingFlow.ts keeps both non-admitted graceful leaves at exit code 0',
  MEETING_FLOW_TS,
  'gracefulLeaveFunction(page, 0, decision.reason',
  2,
);

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
