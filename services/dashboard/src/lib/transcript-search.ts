import { withBasePath } from "@/lib/base-path";
import { UNTITLED_MEETING_LABEL } from "@/lib/meeting-title";

/** meeting-api 側の下限(search.py の MIN_QUERY_LENGTH)と同じ値。 */
export const MIN_TRANSCRIPT_SEARCH_QUERY_LENGTH = 2;
/** API が会議あたりに返すスニペット上限(search.py の MAX_MATCHES_PER_MEETING)。 */
export const MAX_TRANSCRIPT_SEARCH_SNIPPETS = 3;

export interface TranscriptSearchSnippet {
  speaker: string | null;
  text: string;
}

export interface TranscriptSearchHit {
  meetingId: string;
  title: string;
  matchCount: number;
  snippets: TranscriptSearchSnippet[];
}

export interface HighlightSegment {
  text: string;
  match: boolean;
}

export function normalizeTranscriptSearchQuery(value: string): string {
  return value.trim();
}

/** 2文字未満(strip 後)では検索を発行しない。 */
export function shouldSearchTranscripts(value: string): boolean {
  return (
    normalizeTranscriptSearchQuery(value).length >= MIN_TRANSCRIPT_SEARCH_QUERY_LENGTH
  );
}

export function buildTranscriptSearchUrl(query: string): string {
  const q = encodeURIComponent(normalizeTranscriptSearchQuery(query));
  return withBasePath(`/api/vexa/transcripts/search?q=${q}`);
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null
    ? (value as Record<string, unknown>)
    : null;
}

function asNonEmptyString(value: unknown): string {
  return typeof value === "string" && value.trim() ? value : "";
}

function parseSnippets(value: unknown): TranscriptSearchSnippet[] {
  if (!Array.isArray(value)) return [];
  const snippets: TranscriptSearchSnippet[] = [];
  for (const raw of value) {
    const match = asRecord(raw);
    const text = asNonEmptyString(match?.text);
    if (!text) continue;
    snippets.push({ speaker: asNonEmptyString(match?.speaker) || null, text });
    if (snippets.length >= MAX_TRANSCRIPT_SEARCH_SNIPPETS) break;
  }
  return snippets;
}

/** GET /transcripts/search のレスポンスを表示用へ整形する。 */
export function parseTranscriptSearchResponse(payload: unknown): TranscriptSearchHit[] {
  const results = asRecord(payload)?.results;
  if (!Array.isArray(results)) return [];

  const hits: TranscriptSearchHit[] = [];
  for (const raw of results) {
    const result = asRecord(raw);
    const meeting = asRecord(result?.meeting);
    const meetingId = meeting?.id;
    if (typeof meetingId !== "number" && typeof meetingId !== "string") continue;

    const snippets = parseSnippets(result?.matches);
    const matchCount = result?.match_count;
    hits.push({
      meetingId: String(meetingId),
      title:
        asNonEmptyString(meeting?.title) ||
        asNonEmptyString(meeting?.native_meeting_id) ||
        UNTITLED_MEETING_LABEL,
      matchCount:
        typeof matchCount === "number" && Number.isFinite(matchCount) && matchCount > 0
          ? matchCount
          : snippets.length,
      snippets,
    });
  }
  return hits;
}

/** 正規表現メタ文字を無効化し、クエリをリテラルとして扱えるようにする。 */
export function escapeRegExpLiteral(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * クエリのリテラル一致部分だけを `match: true` として切り出す。
 * API 側が ILIKE でマッチさせるので大文字小文字は区別しない。
 */
export function highlightLiteralMatches(text: string, query: string): HighlightSegment[] {
  if (!text) return [];
  const needle = normalizeTranscriptSearchQuery(query);
  if (!needle) return [{ text, match: false }];

  const pattern = new RegExp(escapeRegExpLiteral(needle), "gi");
  const segments: HighlightSegment[] = [];
  let cursor = 0;
  for (const found of text.matchAll(pattern)) {
    const index = found.index ?? 0;
    if (index > cursor) segments.push({ text: text.slice(cursor, index), match: false });
    segments.push({ text: found[0], match: true });
    cursor = index + found[0].length;
  }
  if (cursor < text.length) segments.push({ text: text.slice(cursor), match: false });
  return segments;
}

export async function fetchTranscriptSearch(
  query: string,
  options?: { signal?: AbortSignal }
): Promise<TranscriptSearchHit[]> {
  const response = await fetch(buildTranscriptSearchUrl(query), {
    signal: options?.signal,
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`Transcript search failed: ${response.status}`);
  }
  return parseTranscriptSearchResponse(await response.json());
}
