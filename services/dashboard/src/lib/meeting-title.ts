import type { MeetingData } from "@/types/vexa";

export const UNTITLED_MEETING_LABEL = "無題の会議";

/** 手動編集名・カレンダー由来タイトルの解決。編集フォームの初期値にも使う。無ければ "" */
export function getCustomMeetingTitle(data?: MeetingData | null): string {
  return (
    data?.name || data?.title || data?.calendar_title || data?.calendar_event?.title || ""
  );
}

/** 一覧・詳細共通の表示タイトル解決 */
export function resolveMeetingTitle(
  data: MeetingData | null | undefined,
  platformSpecificId?: string | null,
): string {
  return getCustomMeetingTitle(data) || platformSpecificId || UNTITLED_MEETING_LABEL;
}
