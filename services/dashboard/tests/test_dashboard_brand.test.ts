import { describe, expect, it } from "vitest";
import { normalizeDashboardLocale, resolveDashboardBrand } from "@/lib/dashboard-brand";

describe("resolveDashboardBrand", () => {
  it("keeps the existing Vexa defaults when no brand env is set", () => {
    expect(resolveDashboardBrand({})).toEqual({
      name: "Vexa",
      shortName: "vexa",
      slug: "vexa",
      locale: "en",
      botName: "Vexa - Open Source Bot",
      issueUrl:
        "https://github.com/Vexa-ai/vexa/issues/new?labels=bug,hosted&title=[Hosted]%20&body=%23%23%20Environment%0AHosted%20service%20(dashboard.vexa.ai)%0A%0A%23%23%20Description%0A%0A%23%23%20Steps%20to%20reproduce%0A1.%20%0A%0A%23%23%20Expected%20behavior%0A%0A%23%23%20Actual%20behavior%0A",
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

  it("uses the repository issue form for Kabosu even when no slug is configured", () => {
    expect(
      resolveDashboardBrand({
        DASHBOARD_BRAND_NAME: "カボス",
        DASHBOARD_LOCALE: "ja-JP",
        DEFAULT_BOT_NAME: "カボス",
      })
    ).toMatchObject({
      slug: "custom-dashboard",
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
