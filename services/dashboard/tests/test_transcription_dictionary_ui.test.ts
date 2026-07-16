import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

describe("transcription dictionary UI", () => {
  it("grays out disabled dictionary rows without hiding the toggle", () => {
    const source = fs.readFileSync(path.resolve("src/app/dictionary/page.tsx"), "utf8");
    expect(source).toContain("!item.enabled");
    expect(source).toContain("bg-muted/50 text-muted-foreground");
    expect(source).toContain("checked={item.enabled}");
  });
});
