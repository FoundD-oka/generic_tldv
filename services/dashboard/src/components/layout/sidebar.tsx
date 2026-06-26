"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { getWebappUrl } from "@/lib/docs/webapp-url";
import {
  Video,
  Plus,
  X,
  Zap,
  CreditCard,
  Bug,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useJoinModalStore } from "@/stores/join-modal-store";
import { useRuntimeConfig } from "@/hooks/use-runtime-config";
import { withBasePath } from "@/lib/base-path";
import { DEFAULT_DASHBOARD_BRAND } from "@/lib/dashboard-brand";
import { getDashboardCopy, type DashboardCopy } from "@/lib/dashboard-copy";

interface SidebarProps {
  isOpen?: boolean;
  onClose?: () => void;
}

// IS_HOSTED is determined at runtime via /api/config, not build time

function BillingStatus({ copy }: { copy: DashboardCopy }) {
  const [status, setStatus] = useState<{
    subscription_status: string | null;
    subscription_tier: string | null;
    subscription_trial_end: string | null;
    trial_days_left?: number;
  } | null>(null);

  useEffect(() => {
    fetch(withBasePath("/api/billing/status"))
      .then((r) => r.json())
      .then((data) => {
        const trialDaysLeft = data.subscription_trial_end
          ? Math.max(
              0,
              Math.ceil(
                (new Date(data.subscription_trial_end).getTime() - Date.now()) /
                  (1000 * 60 * 60 * 24)
              )
            )
          : undefined;
        setStatus({ ...data, trial_days_left: trialDaysLeft });
      })
      .catch(() => {});
  }, []);

  if (!status || !status.subscription_status) return null;

  const { subscription_status, subscription_tier, subscription_trial_end } =
    status;

  if (subscription_status === "trialing" && subscription_trial_end) {
    const daysLeft = status.trial_days_left ?? 0;
    return (
      <div className="px-3 py-1.5">
        <span className="text-xs font-medium text-amber-500">
          {copy.billing.trialPrefix}: {daysLeft} {daysLeft === 1 ? copy.billing.day : copy.billing.days} {copy.billing.left}
        </span>
      </div>
    );
  }

  if (
    subscription_status === "canceled" ||
    subscription_status === "expired"
  ) {
    return (
      <div className="px-3 py-1.5 flex items-center justify-between">
        <span className="text-xs font-medium text-red-500">{copy.billing.planExpired}</span>
        <a
          href={`${getWebappUrl()}/pricing`}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs font-medium text-primary hover:underline"
        >
          {copy.billing.subscribe}
        </a>
      </div>
    );
  }

  if (subscription_status === "active") {
    const label = subscription_tier
      ? subscription_tier.charAt(0).toUpperCase() + subscription_tier.slice(1)
      : "Active";
    return (
      <div className="px-3 py-1.5">
        <span className="text-xs font-medium text-muted-foreground">
          {label} {copy.billing.activePlanSuffix}
        </span>
      </div>
    );
  }

  return null;
}

export function Sidebar({ isOpen, onClose }: SidebarProps) {
  const pathname = usePathname();
  const openJoinModal = useJoinModalStore((state) => state.openModal);
  const { config } = useRuntimeConfig();
  const isHosted = config?.hostedMode ?? false;
  const brand = config?.brand || DEFAULT_DASHBOARD_BRAND;
  const copy = getDashboardCopy(brand.locale);
  const navigation = [
    { name: copy.nav.meetings, href: "/meetings", icon: Video },
    ...(process.env.NEXT_PUBLIC_TRACKER_ENABLED === "true"
      ? [{ name: copy.nav.tracker, href: "/tracker", icon: Zap }]
      : []),
  ];

  const handleJoinClick = () => {
    openJoinModal();
    onClose?.();
  };

  return (
    <>
      {/* Mobile overlay */}
      {isOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50 md:hidden"
          onClick={onClose}
        />
      )}

      {/* Sidebar - fixed on mobile, relative on desktop */}
      <aside
        className={cn(
          // Mobile: fixed, full height, slides in
          "fixed inset-y-0 left-0 z-50 w-64 bg-card border-r border-border",
          "transform transition-transform duration-200 ease-in-out",
          // Desktop: relative, part of flex layout
          "md:relative md:z-0 md:translate-x-0 md:flex md:flex-col md:shrink-0",
          // Mobile visibility
          isOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0"
        )}
      >
        <div className="flex h-full flex-col">
          {/* Mobile header */}
          <div className="flex h-14 items-center justify-between border-b px-4 md:hidden shrink-0">
            <span className="font-semibold">{copy.nav.menu}</span>
            <Button variant="ghost" size="icon" onClick={onClose}>
              <X className="h-5 w-5" />
            </Button>
          </div>

          {/* Navigation - scrollable area */}
          <ScrollArea className="flex-1">
            <nav className="space-y-1 p-4">
              {navigation.map((item) => {
                const isActive =
                  item.href === "/"
                    ? pathname === "/"
                    : pathname.startsWith(item.href);

                return (
                  <Link
                    key={item.name}
                    href={item.href}
                    onClick={onClose}
                    className={cn(
                      "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                      isActive
                        ? "bg-primary text-primary-foreground"
                        : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                    )}
                  >
                    <item.icon className="h-5 w-5" />
                    {item.name}
                  </Link>
                );
              })}
              {/* Join Meeting button */}
              <button
                onClick={handleJoinClick}
                className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors text-muted-foreground hover:bg-accent hover:text-accent-foreground"
              >
                <Plus className="h-5 w-5" />
                {copy.nav.joinMeeting}
              </button>
            </nav>
          </ScrollArea>

          {/* Footer */}
          <div className="border-t border-border p-4 shrink-0 space-y-2">
            {isHosted && (
              <>
                <BillingStatus copy={copy} />
                <a
                  href={`${config?.webappUrl || "https://vexa.ai"}/account`}
                  onClick={onClose}
                  className="flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition-colors text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                >
                  <CreditCard className="h-4 w-4" />
                  {copy.nav.accountBilling}
                </a>
              </>
            )}
            <a
              href={brand.issueUrl}
              target="_blank"
              rel="noopener noreferrer"
              onClick={onClose}
              className="flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition-colors text-muted-foreground hover:bg-accent hover:text-accent-foreground"
            >
              <Bug className="h-4 w-4" />
              {copy.nav.reportBug}
            </a>
          </div>
        </div>
      </aside>
    </>
  );
}
