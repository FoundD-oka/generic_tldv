/**
 * <VersionChip /> — small, discoverable label disclosing what OSS version
 * this hosted dashboard pairs with, plus the deploy date. Click → GitHub
 * release notes.
 *
 * Mirror of services/webapp's component, intentionally kept simple so it
 * can stay in sync without sharing a package.
 */

import { RELEASE, releaseUrl } from "@/lib/release-version";
import { DEFAULT_DASHBOARD_BRAND } from "@/lib/dashboard-brand";

type Variant = "full" | "compact" | "minimal";
type Look = "pill" | "text";

const VERSION_SUFFIX = "ａ";

export function VersionChip({
  variant = "minimal",
  look = "pill",
  className = "",
  brandName = DEFAULT_DASHBOARD_BRAND.name,
}: {
  variant?: Variant;
  look?: Look;
  className?: string;
  brandName?: string;
}) {
  const url = releaseUrl(RELEASE.version);
  const versionLabel = `${RELEASE.version}${VERSION_SUFFIX}`;

  let label: string;
  switch (variant) {
    case "full":
      label = `Running ${versionLabel} · updated ${RELEASE.releaseDate}`;
      break;
    case "compact":
      label = `${versionLabel} · ${RELEASE.releaseDate}`;
      break;
    case "minimal":
    default:
      label = versionLabel;
  }

  const baseClasses =
    look === "pill"
      ? "inline-flex items-center gap-1 px-2 py-0.5 rounded-full border border-border bg-background/60 text-[11px] text-muted-foreground hover:border-foreground/30 hover:text-foreground transition-colors"
      : "inline-flex items-center gap-1 text-[12px] text-muted-foreground hover:text-foreground transition-colors";

  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      title={`${brandName} ${versionLabel} · リリース日 ${RELEASE.releaseDate} · リリースノートを開く`}
      className={baseClasses + " " + className}
    >
      <span>{label}</span>
    </a>
  );
}
