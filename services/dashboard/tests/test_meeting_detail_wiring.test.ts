import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const pageSource = () =>
  fs.readFileSync(path.resolve("src/hooks/use-meeting-actions.tsx"), "utf8");

const countOf = (source: string, needle: string) => source.split(needle).length - 1;

describe("meeting detail wiring contract", () => {
  it("AT-001: exposes SRT/VTT download items in both export dropdowns", () => {
    const source = pageSource();
    expect(countOf(source, 'handleExport("srt")')).toBeGreaterThanOrEqual(2);
    expect(countOf(source, 'handleExport("vtt")')).toBeGreaterThanOrEqual(2);
    expect(source).toContain(".srtをダウンロード");
    expect(source).toContain(".vttをダウンロード");
  });

  it("AT-002: wires BotFailedIndicator onRetry to a createBot handler with defaults", () => {
    const source = pageSource();
    expect(source).toContain("onRetry={handleRetryBot}");
    expect(source).toContain("vexaAPI.createBot");
    expect(source).toContain("applyBotCreationDefaults(withPostMeetingAutoStop(");
    expect(source).toContain("router.push(`/meetings/${meeting.id}`)");
    expect(source).toContain("新しいボットをリクエストしました");
    expect(source).toContain("ボットの再リクエストに失敗しました");
  });

  it("AT-003: carries meeting identity and optional bot options into the retry request", () => {
    const source = pageSource();
    expect(source).toContain("platform: currentMeeting.platform");
    expect(source).toContain("native_meeting_id: currentMeeting.platform_specific_id");
    expect(source).toContain("request.passcode");
    expect(source).toContain("request.meeting_url");
    expect(source).toContain("data.transcribe_enabled === false");
    expect(source).toContain("request.transcribe_enabled = false");
  });

  it("AT-004: guards the retry handler against double firing", () => {
    const source = pageSource();
    expect(source).toContain("const [isRetryingBot, setIsRetryingBot] = useState(false)");
    expect(source).toContain("if (!currentMeeting || isRetryingBot) return;");
    expect(source).toContain("setIsRetryingBot(true)");
    expect(source).toContain("setIsRetryingBot(false)");
  });

  it("keeps the retry button available in the failed-bot indicator", () => {
    const source = fs.readFileSync(
      path.resolve("src/components/meetings/bot-status-indicator.tsx"),
      "utf8"
    );
    expect(source).toContain("新しいボットでもう一度試す");
  });
});
