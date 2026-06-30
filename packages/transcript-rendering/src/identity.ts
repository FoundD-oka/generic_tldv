import type { TranscriptSegment } from './types';

function keyPart(value: unknown): string {
  if (value === undefined || value === null || value === '') return 'unknown';
  return String(value);
}

function streamId(seg: TranscriptSegment): string {
  return keyPart(
    seg.track_id ??
      seg.speaker_track_id ??
      seg.speakerTrackId ??
      seg.speakerSessionUid ??
      seg.session_uid,
  );
}

function meetingId(seg: TranscriptSegment): string {
  return keyPart(seg.meeting_id ?? seg.meetingInstanceId);
}

/**
 * Stable transcript identity for mutable/live segments.
 *
 * Speaker names are intentionally excluded: they are display labels that can be
 * corrected after the first event. Prefer bot segment_id when available, scoped
 * by meeting + stream identity; otherwise fall back to absolute start time plus
 * the same scope.
 */
export function getSegmentIdentityKey<T extends TranscriptSegment>(seg: T): string {
  const scope = `${meetingId(seg)}|${streamId(seg)}`;
  if (seg.segment_id) return `segment|${scope}|${seg.segment_id}`;
  return `time|${scope}|${seg.absolute_start_time}`;
}

function updatedAtMs(value?: string): number | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

/**
 * Mutable transcript updates should only move forward when both versions carry
 * updated_at. If either side lacks freshness metadata, preserve event order by
 * accepting the incoming segment.
 */
export function shouldReplaceSegment<T extends TranscriptSegment>(
  existing: T | undefined,
  incoming: T,
): boolean {
  if (!existing) return true;

  const existingUpdated = updatedAtMs(existing.updated_at);
  const incomingUpdated = updatedAtMs(incoming.updated_at);
  if (existingUpdated !== null && incomingUpdated !== null) {
    return incomingUpdated >= existingUpdated;
  }

  return true;
}
