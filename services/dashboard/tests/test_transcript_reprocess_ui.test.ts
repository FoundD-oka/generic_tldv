import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

describe("transcript reprocess UI contract", () => {
  it("offers confirmed replace and status polling for existing transcripts", () => {
    const source = fs.readFileSync(path.resolve("src/components/transcript/transcript-viewer.tsx"), "utf8");
    expect(source).toContain("辞書を反映して再文字起こし");
    expect(source).toContain("window.confirm");
    expect(source).toContain('"replace"');
    expect(source).toContain("getTranscriptionStatus");
    expect(source).toContain("onTranscribeStatusChange?.(started.status)");
    expect(source).toContain("onTranscribeStatusChange?.(status.status)");
  });

  it("updates the detail badge immediately from the committed POST status", () => {
    const source = fs.readFileSync(path.resolve("src/app/meetings/[id]/page.tsx"), "utf8");
    expect(source).toContain("onTranscribeStatusChange={(status)");
    expect(source).toContain("useMeetingsStore.getState().currentMeeting");
    expect(source).toContain("setCurrentMeeting({");
    expect(source).toContain("final_transcription:");
    expect(source).toContain("shouldPollRetranscription");
    expect(source).toContain("startRetranscriptionStatusPolling(() => refreshMeeting(meetingId), 2500)");
    expect(source).toContain("normalizeRetranscriptionStatus(status)");
  });

  it("silently refreshes the list only while reprocessing is queued or running", () => {
    const pageSource = fs.readFileSync(path.resolve("src/app/meetings/page.tsx"), "utf8");
    const storeSource = fs.readFileSync(path.resolve("src/stores/meetings-store.ts"), "utf8");
    expect(pageSource).toContain("hasRetranscriptionInProgress");
    expect(pageSource).toContain("isRetranscriptionInProgress(meeting.data)");
    expect(pageSource).toContain("startSingleFlightPolling");
    expect(pageSource).toContain("fetchMeetings(undefined, { silent: true })");
    expect(storeSource).toContain("options?: { silent?: boolean }");
    expect(storeSource).toContain("Failed to silently refresh meetings");
    expect(storeSource).toContain("requestGeneration !== meetingsRequestGeneration");
  });
});
