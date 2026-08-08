/**
 * <VersionChip /> — small, non-interactive label disclosing which build of
 * the dashboard is running, plus the deploy date.
 *
 * Mirror of services/webapp's component, intentionally kept simple so it
 * can stay in sync without sharing a package.
 */

import { RELEASE } from "@/lib/release-version";
import { DEFAULT_DASHBOARD_BRAND } from "@/lib/dashboard-brand";

type Variant = "full" | "compact" | "minimal";
type Look = "pill" | "text";

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
  const versionLabel = RELEASE.version;

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
    <span
      title={`${brandName} ${versionLabel} · リリース日 ${RELEASE.releaseDate}`}
      className={baseClasses + " " + className}
    >
      <span>{label}</span>
    </span>
  );
}
