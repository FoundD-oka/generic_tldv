import { existsSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const logoSource = readFileSync("src/components/ui/logo.tsx", "utf8");
const layoutSource = readFileSync("src/app/layout.tsx", "utf8");
const versionChipSource = readFileSync("src/components/version-chip.tsx", "utf8");
const callbackSource = readFileSync(
  "src/app/auth/google-calendar/callback/page.tsx",
  "utf8",
);
const sidebarSource = readFileSync("src/components/layout/sidebar.tsx", "utf8");

describe("ブランド表示", () => {
  it("既定ロゴが kabosu.svg を参照し Vexa ロゴを参照しない", () => {
    expect(logoSource).toContain('withBasePath("/icons/kabosu.svg")');
    expect(logoSource).not.toContain("vexadark");
    expect(logoSource).not.toContain("vexalight");
  });

  it("layout のファビコンフォールバックが kabosu.svg を指す", () => {
    expect(layoutSource).toContain('withBasePath("/icons/kabosu.svg")');
    expect(layoutSource).not.toContain("vexadark");
  });

  it("app/icon.svg が kabosu.svg と一致し favicon.ico が存在しない", () => {
    expect(existsSync("src/app/icon.svg")).toBe(true);
    expect(readFileSync("src/app/icon.svg", "utf8")).toBe(
      readFileSync("public/icons/kabosu.svg", "utf8"),
    );
    expect(existsSync("src/app/favicon.ico")).toBe(false);
  });

  it("バージョン表示が外部リンクと全角サフィックスを持たない", () => {
    expect(versionChipSource).not.toContain("Vexa-ai");
    expect(versionChipSource).not.toContain("href");
    expect(versionChipSource).not.toContain("ａ");
  });
});

describe("カレンダーコールバック画面", () => {
  it("固定の英語文言を含まない", () => {
    for (const phrase of [
      "Connecting Google Calendar",
      "Finalizing",
      "Calendar Connected",
      "Calendar Connection Failed",
      "Back to Meetings",
      "Please wait",
      "Redirecting",
      "Meeting Transcription",
      "Unknown error",
    ]) {
      expect(callbackSource).not.toContain(phrase);
    }
  });

  it("日本語文言を表示する", () => {
    for (const phrase of [
      "Googleカレンダーを接続しています",
      "カレンダーを接続しました",
      "カレンダー接続に失敗しました",
      "会議一覧へ戻る",
      "お待ちください",
      "会議文字起こし",
    ]) {
      expect(callbackSource).toContain(phrase);
    }
  });
});

describe("サイドバー導線", () => {
  it("プロフィール・Webhook・MCP・設定への導線を持つ", () => {
    for (const [href, label] of [
      ['href: "/profile"', "copy.nav.profile"],
      ['href: "/webhooks"', "copy.nav.webhooks"],
      ['href: "/mcp"', "copy.nav.mcpSetup"],
      ['href: "/settings"', "copy.nav.settings"],
    ]) {
      expect(sidebarSource).toContain(href);
      expect(sidebarSource).toContain(label);
    }
  });
});
