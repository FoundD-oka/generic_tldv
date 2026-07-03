import { describe, expect, it } from "vitest";
import { normalizeDashboardLocale, resolveDashboardBrand } from "@/lib/dashboard-brand";

describe("resolveDashboardBrand", () => {
  it("uses Japanese Kabosu defaults when no brand env is set", () => {
    expect(resolveDashboardBrand({})).toEqual({
      name: "カボス",
      shortName: "カボス",
      slug: "kabosu",
      locale: "ja",
      botName: "カボス",
      issueUrl: "https://github.com/FoundD-oka/generic_tldv/issues/new?template=bug_report.md",
      logoDark: undefined,
      logoLight: undefined,
    });
  });

  it("builds a Japanese Kabosu brand from runtime environment", () => {
    expect(
      resolveDashboardBrand({
        DASHBOARD_BRAND_NAME: "カボス",
        DASHBOARD_BRAND_SHORT_NAME: "カボス",
        DASHBOARD_BRAND_SLUG: "kabosu",
        DASHBOARD_LOCALE: "ja-JP",
        DEFAULT_BOT_NAME: "カボス",
      })
    ).toMatchObject({
      name: "カボス",
      shortName: "カボス",
      slug: "kabosu",
      locale: "ja",
      botName: "カボス",
      issueUrl: "https://github.com/FoundD-oka/generic_tldv/issues/new?template=bug_report.md",
    });
  });

  it("uses the repository issue form for Kabosu even when only the name is configured", () => {
    expect(
      resolveDashboardBrand({
        DASHBOARD_BRAND_NAME: "カボス",
        DASHBOARD_LOCALE: "ja-JP",
        DEFAULT_BOT_NAME: "カボス",
      })
    ).toMatchObject({
      slug: "kabosu",
      issueUrl: "https://github.com/FoundD-oka/generic_tldv/issues/new?template=bug_report.md",
    });
  });
});

describe("normalizeDashboardLocale", () => {
  it("maps Japanese locales to ja and everything else to en", () => {
    expect(normalizeDashboardLocale("ja-JP")).toBe("ja");
    expect(normalizeDashboardLocale("en-US")).toBe("en");
    expect(normalizeDashboardLocale(undefined)).toBe("en");
  });
});
