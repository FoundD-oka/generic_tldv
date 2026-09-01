/**
 * 「会議に参加」モーダルで任意入力される会議タイトルの正規化。
 *
 * 入力は切り詰めず、前後の空白だけを落として扱う。空文字は「未入力」と
 * みなし、リクエストへキー自体を載せない(= Drive/Discord 側でも使わない)。
 */

export const MANUAL_MEETING_TITLE_MAX = 200;

export function normalizeManualMeetingTitle(raw: string): string | undefined {
  const trimmed = (raw ?? "").trim();
  return trimmed ? trimmed : undefined;
}

export function isManualMeetingTitleTooLong(raw: string): boolean {
  return (raw ?? "").trim().length > MANUAL_MEETING_TITLE_MAX;
}
