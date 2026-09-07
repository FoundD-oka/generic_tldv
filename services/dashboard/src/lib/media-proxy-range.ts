// Cloud Run の HTTP/1 応答上限(32MiB)より十分小さい 1 応答あたりの最大バイト数。固定値。env で可変にしない。
export const MEDIA_PROXY_RANGE_MAX_BYTES = 8 * 1024 * 1024; // 8388608

const SINGLE_BYTE_RANGE_PATTERN = /^bytes=(\d*)-(\d*)$/;

/**
 * 単一 byte range の終端を MEDIA_PROXY_RANGE_MAX_BYTES で切り詰める。
 * 判定できない/対象外の入力は元の文字列をそのまま返し、upstream の判断に委ねる。
 */
export function boundMediaRangeHeader(rangeHeader: string | null): string | null {
  if (rangeHeader === null) return null;
  const trimmed = rangeHeader.trim();
  if (trimmed === "") return null;

  const match = SINGLE_BYTE_RANGE_PATTERN.exec(trimmed);
  if (!match) return rangeHeader;

  const [, startText, endText] = match;
  // suffix range (bytes=-N) は起点を変えると意味が変わるため触らない。
  if (startText === "") return rangeHeader;

  const start = Number(startText);
  if (!Number.isSafeInteger(start)) return rangeHeader;

  if (endText !== "") {
    const end = Number(endText);
    if (!Number.isSafeInteger(end)) return rangeHeader;
    if (end < start) return rangeHeader;
    if (end - start + 1 <= MEDIA_PROXY_RANGE_MAX_BYTES) return rangeHeader;
  }

  const newEnd = start + MEDIA_PROXY_RANGE_MAX_BYTES - 1;
  if (!Number.isSafeInteger(newEnd)) return rangeHeader;

  return `bytes=${start}-${newEnd}`;
}
