import type { Meeting } from "@/types/vexa";
import { getCustomMeetingTitle, resolveMeetingTitle } from "@/lib/meeting-title";

export const desktopMeetingTitle = (currentMeeting: Meeting): string => resolveMeetingTitle(currentMeeting.data, currentMeeting.platform_specific_id);
export const mobileMeetingTitle = (currentMeeting: Meeting): string => resolveMeetingTitle(currentMeeting.data, currentMeeting.platform_specific_id);
export const browserMeetingTitle = (currentMeeting: Meeting): string => resolveMeetingTitle(currentMeeting.data, currentMeeting.platform_specific_id);
export const recordingMeetingTitle = (currentMeeting: Meeting): string => resolveMeetingTitle(currentMeeting.data, currentMeeting.platform_specific_id);
export const customDesktopTitle = (currentMeeting: Meeting): string => getCustomMeetingTitle(currentMeeting.data);
export const customMobileTitle = (currentMeeting: Meeting): string => getCustomMeetingTitle(currentMeeting.data);
export function beginMeetingTitleEdit(currentMeeting: Meeting, setEditedTitle: (value: string) => void, resolveCustomTitle?: (meeting: Meeting) => string): void {
  if (resolveCustomTitle) setEditedTitle(resolveCustomTitle(currentMeeting));
  else setEditedTitle(getCustomMeetingTitle(currentMeeting.data));
}
