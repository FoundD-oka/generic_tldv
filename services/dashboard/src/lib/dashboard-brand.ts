export type DashboardLocale = "en" | "ja";

export interface DashboardBrand {
  name: string;
  shortName: string;
  slug: string;
  locale: DashboardLocale;
  botName: string;
  logoDark?: string;
  logoLight?: string;
  issueUrl: string;
}

export const DEFAULT_DASHBOARD_BRAND: DashboardBrand = {
  name: "カボス",
  shortName: "カボス",
  slug: "kabosu",
  locale: "ja",
  botName: "カボス",
  issueUrl: "https://github.com/FoundD-oka/generic_tldv/issues/new?template=bug_report.md",
};

const KABOSU_DASHBOARD_ISSUE_URL =
  "https://github.com/FoundD-oka/generic_tldv/issues/new?template=bug_report.md";

type Env = Record<string, string | undefined>;

function firstNonEmpty(...values: Array<string | undefined>): string | undefined {
  return values.map((value) => value?.trim()).find(Boolean);
}

function slugify(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

export function normalizeDashboardLocale(value?: string | null): DashboardLocale {
  return value?.toLowerCase().startsWith("ja") ? "ja" : "en";
}

export function resolveDashboardBrand(env: Env = {}): DashboardBrand {
  const name =
    firstNonEmpty(env.DASHBOARD_BRAND_NAME, env.NEXT_PUBLIC_DASHBOARD_BRAND_NAME) ||
    DEFAULT_DASHBOARD_BRAND.name;
  const defaultSlug =
    name === DEFAULT_DASHBOARD_BRAND.name
      ? DEFAULT_DASHBOARD_BRAND.slug
      : slugify(name) || "custom-dashboard";
  const slug =
    firstNonEmpty(env.DASHBOARD_BRAND_SLUG, env.NEXT_PUBLIC_DASHBOARD_BRAND_SLUG) ||
    defaultSlug;
  const shortName =
    firstNonEmpty(env.DASHBOARD_BRAND_SHORT_NAME, env.NEXT_PUBLIC_DASHBOARD_BRAND_SHORT_NAME) ||
    (slug === DEFAULT_DASHBOARD_BRAND.slug ? DEFAULT_DASHBOARD_BRAND.shortName : name);
  const locale = normalizeDashboardLocale(
    firstNonEmpty(
      env.DASHBOARD_BRAND_LOCALE,
      env.NEXT_PUBLIC_DASHBOARD_BRAND_LOCALE,
      env.DASHBOARD_LOCALE,
      env.NEXT_PUBLIC_DASHBOARD_LOCALE,
      DEFAULT_DASHBOARD_BRAND.locale
    )
  );
  const botName =
    firstNonEmpty(env.DEFAULT_BOT_NAME, env.DASHBOARD_BRAND_BOT_NAME) ||
    (slug === DEFAULT_DASHBOARD_BRAND.slug ? DEFAULT_DASHBOARD_BRAND.botName : name);
  const isKabosuBrand =
    slug === "kabosu" || name === "カボス" || shortName === "カボス" || botName === "カボス";
  const defaultIssueUrl =
    isKabosuBrand ? KABOSU_DASHBOARD_ISSUE_URL : DEFAULT_DASHBOARD_BRAND.issueUrl;

  return {
    name,
    shortName,
    slug,
    locale,
    botName,
    logoDark: firstNonEmpty(env.DASHBOARD_BRAND_LOGO_DARK, env.NEXT_PUBLIC_DASHBOARD_BRAND_LOGO_DARK),
    logoLight: firstNonEmpty(env.DASHBOARD_BRAND_LOGO_LIGHT, env.NEXT_PUBLIC_DASHBOARD_BRAND_LOGO_LIGHT),
    issueUrl:
      firstNonEmpty(env.DASHBOARD_BRAND_ISSUE_URL, env.NEXT_PUBLIC_DASHBOARD_BRAND_ISSUE_URL) ||
      defaultIssueUrl,
  };
}
