import { describe, expect, it } from "vitest";
import {
  buildKabosuTranscriptSystemPrompt,
  KABOSU_PERSONA_PROMPT,
} from "@/lib/kabosu-persona";

describe("buildKabosuTranscriptSystemPrompt", () => {
  it("puts the Kabosu persona at the very beginning", () => {
    const prompt = buildKabosuTranscriptSystemPrompt("文字起こし:\n岡田: 次回までに確認します。");

    expect(prompt.startsWith(KABOSU_PERSONA_PROMPT)).toBe(true);
    expect(prompt.indexOf("会議録AIのルール")).toBeGreaterThan(prompt.indexOf(KABOSU_PERSONA_PROMPT));
    expect(prompt.indexOf("Available transcript context")).toBeGreaterThan(
      prompt.indexOf("会議録AIのルール")
    );
    expect(prompt).toContain("岡田: 次回までに確認します。");
  });

  it("still keeps Kabosu first when transcript context is empty", () => {
    const prompt = buildKabosuTranscriptSystemPrompt("");

    expect(prompt.startsWith(KABOSU_PERSONA_PROMPT)).toBe(true);
    expect(prompt).toContain("No transcript context available.");
  });
});
