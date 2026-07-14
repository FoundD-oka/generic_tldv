import {
  getParticipantRoster,
  normalizeParticipantRoster,
  observeParticipantRoster,
  resetParticipantRoster,
} from './participant-roster';
import { readFileSync } from 'fs';
import { join } from 'path';
import { googlePeoplePanelRootSelectors } from '../platforms/googlemeet/selectors';

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

function testAccumulatesAndDeduplicatesRoster() {
  const roster = normalizeParticipantRoster([
    {
      participant_id: 'alice',
      participant_name: ' Alice ',
      first_seen_at_ms: 2000,
      last_seen_at_ms: 3000,
      source: 'participant_tile',
    },
    {
      participant_id: 'alice',
      participant_name: 'Alice',
      first_seen_at_ms: 1000,
      last_seen_at_ms: 5000,
      source: 'people_panel',
    },
    {
      participant_id: 'panel-bob',
      participant_name: 'Bob',
      first_seen_at_ms: 1500,
      last_seen_at_ms: 2500,
      source: 'people_panel',
    },
  ], 'Kabosu', 9000);

  expect('silent and departed participants remain accumulated', roster.map((entry) => ({
    participant_name: entry.participant_name,
    first_seen_at_ms: entry.first_seen_at_ms,
    last_seen_at_ms: entry.last_seen_at_ms,
    source: entry.source,
    opaque_id: /^participant:[a-f0-9]{16}$/.test(entry.participant_id),
  })), [
    { participant_name: 'Alice', first_seen_at_ms: 1000, last_seen_at_ms: 5000, source: 'people_panel', opaque_id: true },
    { participant_name: 'Bob', first_seen_at_ms: 1500, last_seen_at_ms: 2500, source: 'people_panel', opaque_id: true },
  ]);
}

function testFiltersBotAndJunkNames() {
  const roster = normalizeParticipantRoster([
    { participant_name: 'Kabosu (You)', source: 'participant_tile' },
    { participant_name: 'Kabosu (Du)', source: 'participant_tile' },
    { participant_name: 'Kabosu（你）', source: 'participant_tile' },
    { participant_name: 'Kabosu Tanaka', source: 'people_panel' },
    { participant_name: 'Localized self alias', source: 'participant_tile', is_self: true },
    { participant_name: 'Google Participant (spaces/foo/devices/1)', source: 'participant_tile' },
    { participant_name: 'Let participants send messages', source: 'people_panel' },
    { participant_name: 'Carol', source: 'people_panel' },
  ], 'Kabosu', 7000);

  expect('bot and UI labels are excluded without prefix false positives', roster.map((entry) => entry.participant_name), ['Carol', 'Kabosu Tanaka']);
}

function testAccumulatorKeepsDepartedParticipants() {
  resetParticipantRoster();
  observeParticipantRoster([
    { participant_id: 'alice', participant_name: 'Alice', first_seen_at_ms: 1000, last_seen_at_ms: 1000, source: 'participant_tile' },
  ], 'Kabosu', 1000);
  observeParticipantRoster([
    { participant_id: 'bob', participant_name: 'Bob', first_seen_at_ms: 2000, last_seen_at_ms: 2000, source: 'people_panel' },
  ], 'Kabosu', 2000);

  expect(
    'participant remains after disappearing from later snapshots',
    getParticipantRoster().map((entry) => entry.participant_name),
    ['Alice', 'Bob'],
  );
}

function testAccumulatorKeepsCanonicalIdentityWhenDomIdChanges() {
  resetParticipantRoster();
  observeParticipantRoster([
    { participant_id: 'tile-alice', participant_name: 'Alice', first_seen_at_ms: 1000, last_seen_at_ms: 1000, source: 'participant_tile' },
  ], 'Kabosu', 1000, 'meeting-1');
  const firstId = getParticipantRoster()[0].participant_id;
  observeParticipantRoster([
    { participant_id: 'panel-alice', participant_name: 'Alice', first_seen_at_ms: 1000, last_seen_at_ms: 2000, source: 'people_panel' },
  ], 'Kabosu', 2000, 'meeting-1');

  const roster = getParticipantRoster();
  expect('DOM id changes do not duplicate a participant', roster.length, 1);
  expect('canonical participant id remains stable', roster[0].participant_id, firstId);
  expect('People panel observation has priority', roster[0].source, 'people_panel');
}

function testSpecialNamesAndTimestampsAreBounded() {
  const roster = normalizeParticipantRoster([
    {
      participant_id: 'special-name',
      participant_name: '__proto__',
      first_seen_at_ms: Number.MAX_SAFE_INTEGER,
      last_seen_at_ms: Number.MAX_SAFE_INTEGER,
      source: 'people_panel',
    },
  ], 'Kabosu', 5000, 'meeting-1');

  expect('special object-key names remain safe', roster.map((entry) => entry.participant_name), ['__proto__']);
  expect('future timestamps fall back to observation time', [roster[0].first_seen_at_ms, roster[0].last_seen_at_ms], [5000, 5000]);
}

function testPeoplePanelIsReacquiredWithoutStealingChat() {
  const source = readFileSync(
    join(__dirname, '../platforms/googlemeet/recording.ts'),
    'utf8',
  );

  expect('People panel is periodically reacquired', source.includes('ensurePeoplePanelSnapshot()'), true);
  expect('chat panel state is restored after roster snapshot', source.includes('restoreChatPanel'), true);
  expect('hidden People panels do not block reacquisition', source.includes('isVisible(element)'), true);
  expect(
    'participant tile regions are never accepted as People panel roots',
    googlePeoplePanelRootSelectors.some((selector) => selector.includes('[role="region"]')),
    false,
  );
  expect(
    'People panel roots come from the shared safe selector contract',
    source.includes('selectorsTyped.peoplePanelRootSelectors || []'),
    true,
  );
  expect(
    'stale speaker observers re-check People panel membership',
    /function logGoogleSpeakerEvent[\s\S]{0,500}if \(isInsidePeoplePanel\(participantElement\)\) return;/.test(source),
    true,
  );
  expect(
    'participant scans exclude People panel rows',
    /function scanForAllGoogleParticipants[\s\S]{0,2500}if \(isInsidePeoplePanel\(elh\)\) return;/.test(source),
    true,
  );
  expect(
    'roster snapshots yield while chat send holds the panel lock',
    source.includes('browserState.__vexaChatSendInFlight'),
    true,
  );
  expect(
    'nested self DOM markers are excluded through their ancestor',
    source.includes("element.closest('[data-self-name]')"),
    true,
  );
  expect(
    'People panel ownership is established before the initial speaker scan',
    source.indexOf('ensurePeoplePanelSnapshot(true);') < source.indexOf('scanForAllGoogleParticipants(true);'),
    true,
  );
}

function main() {
  testAccumulatesAndDeduplicatesRoster();
  testFiltersBotAndJunkNames();
  testAccumulatorKeepsDepartedParticipants();
  testAccumulatorKeepsCanonicalIdentityWhenDomIdChanges();
  testSpecialNamesAndTimestampsAreBounded();
  testPeoplePanelIsReacquiredWithoutStealingChat();
  console.log(`\n${passed} passed, ${failed} failed`);
  if (failed > 0) process.exit(1);
}

main();
