import type { Meeting } from "@/types/vexa";

type UpdateMeetingData = (
  platform: Meeting["platform"],
  nativeId: string,
  data: { name: string }
) => Promise<void>;

export type SaveMeetingTitleOptions = {
  platform: Meeting["platform"];
  nativeId: string;
  title: string;
  updateMeetingData: UpdateMeetingData;
  setSaving: (saving: boolean) => void;
  onSaved: () => void;
  notifySuccess: (message: string) => void;
  notifyError: (message: string) => void;
};

export async function saveMeetingTitle({
  platform,
  nativeId,
  title,
  updateMeetingData,
  setSaving,
  onSaved,
  notifySuccess,
  notifyError,
}: SaveMeetingTitleOptions): Promise<boolean> {
  setSaving(true);
  try {
    await updateMeetingData(platform, nativeId, { name: title.trim() });
    onSaved();
    notifySuccess("タイトルを更新しました");
    return true;
  } catch {
    notifyError("タイトルの更新に失敗しました");
    return false;
  } finally {
    setSaving(false);
  }
}
