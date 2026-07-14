import { createHash } from 'crypto';

export type ParticipantRosterSource = 'people_panel' | 'participant_tile';

export interface ParticipantRosterEntry {
  participant_id: string;
  participant_name: string;
  first_seen_at_ms: number;
  last_seen_at_ms: number;
  source: ParticipantRosterSource;
}

const MAX_ROSTER_ENTRIES = 250;
const MAX_ROSTER_INPUT_ENTRIES = 1000;
const MAX_NAME_LENGTH = 120;
const MAX_ID_LENGTH = 200;
const MAX_CLOCK_SKEW_MS = 5 * 60 * 1000;
let accumulatedRoster: ParticipantRosterEntry[] = [];

const JUNK_NAME_PATTERNS = [
  /^google participant \(/i,
  /spaces\//i,
  /devices\//i,
  /let participants/i,
  /send messages/i,
  /turn on captions/i,
];

function cleanText(value: unknown, maxLength: number): string {
  if (typeof value !== 'string') return '';
  const cleaned = value.replace(/\s+/g, ' ').trim();
  return cleaned.length <= maxLength ? cleaned : '';
}

function cleanTimestamp(value: unknown, fallback: number, maxValue: number): number {
  const numberValue = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(numberValue) || numberValue < 0 || numberValue > maxValue) return fallback;
  return Math.trunc(numberValue);
}

function isBotParticipant(name: string, botName?: string): boolean {
  if (!botName) return false;
  const normalize = (value: string) => value
    .replace(/\s+/g, ' ')
    .trim()
    .toLocaleLowerCase();
  const normalizedName = normalize(name);
  const normalizedBotName = normalize(botName);
  const withoutLocalizedSelfSuffix = normalizedName
    .replace(/\s*[（(][^()（）]{1,32}[）)]\s*$/u, '')
    .trim();
  return normalizedName === normalizedBotName || withoutLocalizedSelfSuffix === normalizedBotName;
}

function opaqueParticipantId(rawId: string, name: string, idSalt: string): string {
  if (/^participant:[a-f0-9]{16}$/.test(rawId)) return rawId;
  const identity = rawId || `name:${name.toLocaleLowerCase()}`;
  const digest = createHash('sha256')
    .update(idSalt)
    .update('\0')
    .update(identity)
    .digest('hex')
    .slice(0, 16);
  return `participant:${digest}`;
}

/**
 * Bounds and deduplicates the browser-side accumulated roster before it is
 * attached to the terminal callback. `participants` is a display-name list,
 * so duplicate DOM rows for the same normalized name collapse even when Meet
 * uses different IDs in the People panel and video grid.
 */
export function normalizeParticipantRoster(
  rawEntries: unknown,
  botName?: string,
  nowMs: number = Date.now(),
  idSalt: string = 'participant-roster',
): ParticipantRosterEntry[] {
  if (!Array.isArray(rawEntries)) return [];

  const byName = new Map<string, ParticipantRosterEntry>();
  const maxTimestamp = Math.max(0, Math.trunc(nowMs)) + MAX_CLOCK_SKEW_MS;
  for (const raw of rawEntries.slice(0, MAX_ROSTER_INPUT_ENTRIES)) {
    if (!raw || typeof raw !== 'object') continue;
    const candidate = raw as Record<string, unknown>;
    if (candidate.is_self === true) continue;
    const name = cleanText(candidate.participant_name ?? candidate.name, MAX_NAME_LENGTH);
    if (!name || JUNK_NAME_PATTERNS.some((pattern) => pattern.test(name))) continue;
    if (isBotParticipant(name, botName)) continue;

    const source: ParticipantRosterSource = candidate.source === 'people_panel'
      ? 'people_panel'
      : 'participant_tile';
    const rawParticipantId = cleanText(
      candidate.participant_id ?? candidate.id,
      MAX_ID_LENGTH,
    );
    const participantId = opaqueParticipantId(rawParticipantId, name, idSalt);
    let firstSeen = cleanTimestamp(candidate.first_seen_at_ms, nowMs, maxTimestamp);
    let lastSeen = cleanTimestamp(candidate.last_seen_at_ms, firstSeen, maxTimestamp);
    if (lastSeen < firstSeen) [firstSeen, lastSeen] = [lastSeen, firstSeen];

    const nameKey = name.toLocaleLowerCase();
    const existing = byName.get(nameKey);
    if (!existing) {
      if (byName.size >= MAX_ROSTER_ENTRIES) continue;
      byName.set(nameKey, {
        participant_id: participantId,
        participant_name: name,
        first_seen_at_ms: firstSeen,
        last_seen_at_ms: lastSeen,
        source,
      });
    } else {
      existing.first_seen_at_ms = Math.min(existing.first_seen_at_ms, firstSeen);
      existing.last_seen_at_ms = Math.max(existing.last_seen_at_ms, lastSeen);
      if (source === 'people_panel' && existing.source !== 'people_panel') {
        existing.source = source;
        existing.participant_name = name;
      }
    }
  }

  return Array.from(byName.values()).sort((a, b) =>
    a.first_seen_at_ms - b.first_seen_at_ms ||
    a.participant_name.localeCompare(b.participant_name),
  );
}

export function resetParticipantRoster(): void {
  accumulatedRoster = [];
}

/** Keep prior observations when a participant leaves or Meet recycles DOM rows. */
export function observeParticipantRoster(
  rawEntries: unknown,
  botName?: string,
  nowMs: number = Date.now(),
  idSalt: string = 'participant-roster',
): ParticipantRosterEntry[] {
  accumulatedRoster = normalizeParticipantRoster(
    [...accumulatedRoster, ...(Array.isArray(rawEntries) ? rawEntries : [])],
    botName,
    nowMs,
    idSalt,
  );
  return getParticipantRoster();
}

export function getParticipantRoster(): ParticipantRosterEntry[] {
  return accumulatedRoster.map((entry) => ({ ...entry }));
}
