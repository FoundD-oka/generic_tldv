"use client";

import Image from "next/image";
import { cn } from "@/lib/utils";
import { withBasePath } from "@/lib/base-path";
import { DEFAULT_DASHBOARD_BRAND, type DashboardBrand } from "@/lib/dashboard-brand";
import { useRuntimeConfig } from "@/hooks/use-runtime-config";

interface LogoProps {
  className?: string;
  size?: "sm" | "md" | "lg";
  showText?: boolean;
  brand?: DashboardBrand;
}

const sizeClasses = {
  sm: "h-6 w-6",
  md: "h-8 w-8",
  lg: "h-12 w-12",
};

const textSizeClasses = {
  sm: "text-base",
  md: "text-lg",
  lg: "text-2xl",
};

const letterSizeClasses = {
  sm: "text-sm",
  md: "text-base",
  lg: "text-2xl",
};

function isExternalUrl(value: string): boolean {
  return /^https?:\/\//.test(value);
}

function resolveLogoSrc(value: string): string {
  return isExternalUrl(value) ? value : withBasePath(value);
}

export function Logo({ className, size = "md", showText = false, brand: brandProp }: LogoProps) {
  const { config } = useRuntimeConfig();
  const brand = brandProp || config?.brand || DEFAULT_DASHBOARD_BRAND;

  // Render the same markup on the server and first client pass. CSS handles
  // light/dark switching so the logo src cannot drift during hydration.
  const defaultLightLogoSrc = withBasePath("/icons/vexadark.svg");
  const defaultDarkLogoSrc = withBasePath("/icons/vexalight.svg");
  const configuredLightLogo = brand.logoDark || brand.logoLight;
  const configuredDarkLogo = brand.logoLight || brand.logoDark;
  const hasConfiguredLogo = Boolean(configuredLightLogo);
  const shouldUseDefaultLogo = !hasConfiguredLogo && brand.slug === DEFAULT_DASHBOARD_BRAND.slug;
  const brandInitial = (brand.shortName || brand.name || "V").trim().charAt(0).toUpperCase();
  const imageSize = size === "sm" ? 24 : size === "md" ? 32 : 48;

  return (
    <div className={cn("flex items-center gap-2", className)}>
      {shouldUseDefaultLogo ? (
        <>
          <Image
            src={defaultLightLogoSrc}
            alt={`${brand.name} ロゴ`}
            width={imageSize}
            height={imageSize}
            className={cn(sizeClasses[size], "object-contain dark:hidden")}
            priority
          />
          <Image
            src={defaultDarkLogoSrc}
            alt={`${brand.name} ロゴ`}
            width={imageSize}
            height={imageSize}
            className={cn(sizeClasses[size], "hidden object-contain dark:block")}
            priority
          />
        </>
      ) : hasConfiguredLogo ? (
        configuredLightLogo === configuredDarkLogo ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={resolveLogoSrc(configuredLightLogo!)}
            alt={`${brand.name} ロゴ`}
            width={imageSize}
            height={imageSize}
            className={cn(sizeClasses[size], "object-contain")}
          />
        ) : (
          <>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={resolveLogoSrc(configuredLightLogo!)}
              alt={`${brand.name} ロゴ`}
              width={imageSize}
              height={imageSize}
              className={cn(sizeClasses[size], "object-contain dark:hidden")}
            />
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={resolveLogoSrc(configuredDarkLogo!)}
              alt={`${brand.name} ロゴ`}
              width={imageSize}
              height={imageSize}
              className={cn(sizeClasses[size], "hidden object-contain dark:block")}
            />
          </>
        )
      ) : (
        <span
          aria-label={`${brand.name} ロゴ`}
          className={cn(
            sizeClasses[size],
            letterSizeClasses[size],
            "inline-flex items-center justify-center rounded-lg bg-primary text-primary-foreground font-semibold"
          )}
        >
          {brandInitial}
        </span>
      )}
      {showText && (
        <span className={cn("font-semibold", textSizeClasses[size])}>{brand.shortName}</span>
      )}
    </div>
  );
}
