"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { Menu, LogOut } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Logo } from "@/components/ui/logo";
import { VersionChip } from "@/components/version-chip";
import { ThemeToggle } from "@/components/theme-toggle";
import { useAuthStore } from "@/stores/auth-store";
import { useRuntimeConfig } from "@/hooks/use-runtime-config";
import { DEFAULT_DASHBOARD_BRAND } from "@/lib/dashboard-brand";
import { getDashboardCopy } from "@/lib/dashboard-copy";

interface HeaderProps {
  onMenuClick?: () => void;
}

export function Header({ onMenuClick }: HeaderProps) {
  const router = useRouter();
  const { logout } = useAuthStore();
  const { config, isLoading: isConfigLoading } = useRuntimeConfig();
  const brand = config?.brand || DEFAULT_DASHBOARD_BRAND;
  const copy = getDashboardCopy(brand.locale);
  const isSharedAuth = config?.sharedAuth?.enabled === true;
  const showLogout = !isConfigLoading && !isSharedAuth;

  const handleLogout = () => {
    if (isSharedAuth) return;
    logout();
    router.push("/login");
  };

  return (
    <header className="shrink-0 z-50 w-full border-b border-border/70 bg-card/80 backdrop-blur-md">
      <div className="flex h-14 items-center px-4 md:px-6">
        {/* Mobile menu button */}
        <Button
          variant="ghost"
          size="icon"
          className="mr-2 md:hidden"
          onClick={onMenuClick}
        >
          <Menu className="h-5 w-5" />
          <span className="sr-only">{copy.header.toggleMenu}</span>
        </Button>

        {/* Logo + version chip */}
        <div className="flex items-center gap-2.5">
          <Link href="/" className="flex items-center gap-2 group">
            <Logo size="md" showText={false} brand={brand} className="group-hover:scale-105 transition-transform" />
            <span className="hidden sm:inline-block text-[15px] font-semibold text-foreground">{brand.shortName}</span>
          </Link>
          <VersionChip className="hidden sm:inline-flex" />
        </div>

        {/* Spacer */}
        <div className="flex-1" />

        {/* Actions */}
        <div className="flex items-center gap-2">
          <ThemeToggle />

          {showLogout && (
            <Button
              variant="ghost"
              size="icon"
              onClick={handleLogout}
              className="text-muted-foreground hover:text-destructive"
            >
              <LogOut className="h-5 w-5" />
              <span className="sr-only">{copy.header.logout}</span>
            </Button>
          )}
        </div>
      </div>
    </header>
  );
}
