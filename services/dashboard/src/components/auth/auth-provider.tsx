"use client";

import { useEffect, useRef } from "react";
import { useRouter, usePathname } from "next/navigation";
import { useAuthStore } from "@/stores/auth-store";
import { savePendingMeetingUrl } from "@/lib/pending-meeting";
import { Loader2 } from "lucide-react";

// Routes that don't require authentication
const publicRoutes = ["/login", "/auth/verify", "/auth/zoom/callback"];

interface AuthProviderProps {
  children: React.ReactNode;
}

export function AuthProvider({ children }: AuthProviderProps) {
  const router = useRouter();
  const pathname = usePathname();
  const { isAuthenticated, isLoading, checkAuth, didLogout, signInSharedDashboard } = useAuthStore();
  const meetingUrlCaptured = useRef(false);
  const sharedLoginAttempted = useRef(false);

  // Capture meetingUrl from query string and save to localStorage before any redirect
  useEffect(() => {
    if (meetingUrlCaptured.current) return;
    meetingUrlCaptured.current = true;

    const params = new URLSearchParams(window.location.search);
    const meetingUrl = params.get("meetingUrl");
    if (meetingUrl) {
      savePendingMeetingUrl(meetingUrl);
    }
  }, []);

  // Check if current route is public
  const isPublicRoute = publicRoutes.some((route) => pathname?.startsWith(route));

  // Only verify session on protected routes to avoid 401 in console on /login, /auth/zoom/callback
  useEffect(() => {
    if (pathname == null) {
      checkAuth(); // path not yet known
    } else if (!publicRoutes.some((route) => pathname.startsWith(route))) {
      checkAuth(); // protected route
    }
  }, [pathname, checkAuth]);

  useEffect(() => {
    if (isLoading || isAuthenticated || isPublicRoute) return;

    let cancelled = false;

    const authenticateOrRedirect = async () => {
      if (!sharedLoginAttempted.current) {
        sharedLoginAttempted.current = true;
        const sharedResult = await signInSharedDashboard();
        if (cancelled || sharedResult.success) return;
      }

      const externalAuthUrl = process.env.NEXT_PUBLIC_EXTERNAL_AUTH_URL;
      if (externalAuthUrl && !didLogout) {
        // SSO: redirect to webapp for authentication
        const returnUrl = encodeURIComponent(window.location.href);
        window.location.href = `${externalAuthUrl}?returnUrl=${returnUrl}`;
      } else if (!didLogout) {
        // Self-hosted: show dashboard login
        router.push("/login");
      }
      // If didLogout: logout() already handles the redirect — do nothing here
    };

    authenticateOrRedirect();

    return () => {
      cancelled = true;
    };
  }, [isLoading, isAuthenticated, isPublicRoute, signInSharedDashboard, router, didLogout]);

  // If on a public route, just render children
  if (isPublicRoute) {
    return <>{children}</>;
  }

  // If loading or need to redirect, show loading state
  if (isLoading || !isAuthenticated) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    );
  }

  // User is authenticated, render children
  return <>{children}</>;
}
