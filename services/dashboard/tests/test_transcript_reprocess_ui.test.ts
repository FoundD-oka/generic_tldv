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
  });
});
