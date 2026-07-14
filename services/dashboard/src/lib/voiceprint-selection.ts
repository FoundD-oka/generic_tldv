export interface VoiceprintSelectionSegmentTiming {
  start_time?: number | null;
  end_time?: number | null;
}

export interface NormalizedVoiceprintSelectionTiming {
  ranges: Array<{ start: number; end: number }>;
  durationSeconds: number;
  hasInvalidTiming: boolean;
  hasOverlap: boolean;
}

/**
 * Normalize transcript timing to the finalized audio master's timeline.
 * Vexa may report a slightly negative start for the first segment, but the
 * master begins at 0 seconds. Keep this identical to meeting-api extraction.
 */
export function normalizeVoiceprintSelectionTiming(
  segments: VoiceprintSelectionSegmentTiming[]
): NormalizedVoiceprintSelectionTiming {
  const normalized = segments.map((segment) => {
    const rawStart = segment.start_time ?? 0;
    const end = segment.end_time ?? 0;
    return {
      rawStart,
      start: Math.max(0, rawStart),
      end,
    };
  });
  const ranges = normalized.map(({ start, end }) => ({ start, end }));
  const hasInvalidTiming = normalized.some(
    ({ rawStart, start, end }) => (
      !Number.isFinite(rawStart) || !Number.isFinite(end) || end <= start
    )
  );
  const durationSeconds = ranges.reduce(
    (total, { start, end }) => (
      Number.isFinite(start) && Number.isFinite(end) && end > start
        ? total + (end - start)
        : total
    ),
    0
  );
  const sortedRanges = [...ranges].sort((a, b) => a.start - b.start || a.end - b.end);
  const hasOverlap = !hasInvalidTiming && sortedRanges.some(
    (range, index) => index > 0 && range.start < sortedRanges[index - 1].end - 1e-6
  );

  return { ranges, durationSeconds, hasInvalidTiming, hasOverlap };
}
