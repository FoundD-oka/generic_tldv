"use client";

import { useEffect, useRef } from "react";
import { useRouter, usePathname } from "next/navigation";
import { useAuthStore, removeLegacyBrowserAuthState } from "@/stores/auth-store";
import { savePendingMeetingUrl } from "@/lib/pending-meeting";
import { Loader2, XCircle } from "lucide-react";

// Routes that don't require authentication
const publicRoutes = ["/login", "/auth/verify", "/auth/zoom/callback"];

interface AuthProviderProps {
  children: React.ReactNode;
}

export function AuthProvider({ children }: AuthProviderProps) {
  const router = useRouter();
  const pathname = usePathname();
  const { isAuthenticated, isLoading, checkAuth, didLogout, signInSharedDashboard, authError } =
    useAuthStore();
  const meetingUrlCaptured = useRef(false);
  const sharedLoginAttempted = useRef(false);
  const loginRedirectIssued = useRef(false);

  // Drop legacy browser-readable auth artifacts once per mount so a stale browser
  // cannot keep re-seeding an unverified "logged in" look.
  useEffect(() => {
    removeLegacyBrowserAuthState();
  }, []);

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
    // The server was unreachable, so we do not know whether the session is valid.
    // Redirecting on an unknown answer is what produced the /login ↔ /meetings loop.
    if (authError === "network") return;
    if (loginRedirectIssued.current) return;

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
        // Self-hosted: show dashboard login. Exactly once — /login may bounce us back.
        loginRedirectIssued.current = true;
        router.push("/login");
      }
      // If didLogout: logout() already handles the redirect — do nothing here
    };

    authenticateOrRedirect();

    return () => {
      cancelled = true;
    };
  }, [
    isLoading,
    isAuthenticated,
    isPublicRoute,
    signInSharedDashboard,
    router,
    didLogout,
    authError,
  ]);

  // If on a public route, just render children
  if (isPublicRoute) {
    return <>{children}</>;
  }

  // Server unreachable: show a terminal error with a manual retry rather than an
  // endless spinner or a redirect.
  if (!isLoading && !isAuthenticated && authError === "network") {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-6 px-4">
        <div className="w-full max-w-md p-4 rounded-lg bg-destructive/10 border border-destructive/20">
          <div className="flex items-start gap-3">
            <XCircle className="h-5 w-5 text-destructive mt-0.5 flex-shrink-0" />
            <div className="flex-1">
              <h3 className="font-medium text-destructive">サーバーに接続できません</h3>
              <p className="text-sm text-muted-foreground mt-1">
                認証状態を確認できませんでした。ネットワークとサーバーの状態を確認してから再試行してください。
              </p>
            </div>
          </div>
        </div>
        <button
          type="button"
          onClick={() => {
            checkAuth();
          }}
          className="px-4 py-2 rounded-xl border border-border bg-card text-sm font-medium text-foreground hover:bg-accent transition-colors"
        >
          再試行
        </button>
      </div>
    );
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
