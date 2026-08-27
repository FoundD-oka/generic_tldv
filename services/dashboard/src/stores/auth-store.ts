import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { VexaUser } from "@/types/vexa";
import { withBasePath } from "@/lib/base-path";

interface LoginResult {
  success: boolean;
  error?: string;
  mode?: "direct" | "magic-link" | "shared";
  user?: VexaUser;
  token?: string;
  isNewUser?: boolean;
}

/**
 * Why the last auth attempt did not end in an authenticated session.
 * Consumers use this to pick a terminal UI state instead of redirecting blindly,
 * which is what turned a failed shared login into a /login ↔ /meetings loop.
 */
export type AuthErrorReason = "none" | "shared_login_failed" | "network" | "unauthorized";

/**
 * Credential-shaped keys written by older Dashboard releases. They are never read
 * by the current code, but their presence lets a stale browser look "logged in".
 * `vexa-auth` itself is not listed: the persist `migrate` below rewrites it.
 */
export const LEGACY_BROWSER_AUTH_KEYS = [
  "vexa-token",
  "vexa-user",
  "vexa_user",
  "authToken",
  "api_key",
  "vexa-api-key",
] as const;

/** Drop legacy browser-readable auth artifacts from local/session storage. */
export function removeLegacyBrowserAuthState(): void {
  const storages: Array<Storage | undefined> = [
    typeof localStorage === "undefined" ? undefined : localStorage,
    typeof sessionStorage === "undefined" ? undefined : sessionStorage,
  ];

  for (const storage of storages) {
    if (!storage) continue;
    for (const key of LEGACY_BROWSER_AUTH_KEYS) {
      try {
        storage.removeItem(key);
      } catch {
        // Storage can be unavailable (private mode / disabled cookies) — ignore.
      }
    }
  }
}

// Run before zustand hydrates so no legacy artifact can survive into this session.
removeLegacyBrowserAuthState();

/**
 * Persisted auth state that is safe to trust before the server has confirmed it.
 * `token` and `isAuthenticated` are deliberately dropped: a rehydrated browser must
 * never be able to claim a session on its own.
 */
function keepServerVerifiableFields(persistedState: unknown): PersistedAuthState {
  const legacy = (persistedState ?? {}) as Partial<AuthState>;
  return {
    user: legacy.user ?? null,
    didLogout: legacy.didLogout ?? false,
  };
}

interface PersistedAuthState {
  user: VexaUser | null;
  didLogout: boolean;
}

interface AuthState {
  user: VexaUser | null;
  token: string | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  didLogout: boolean; // true after explicit logout — prevents SSO redirect loop
  authError: AuthErrorReason;

  // Actions
  sendMagicLink: (email: string) => Promise<LoginResult>;
  signInSharedDashboard: () => Promise<LoginResult>;
  setAuth: (user: VexaUser, token: string) => void;
  logout: () => void;
  setUser: (user: VexaUser | null) => void;
  setToken: (token: string | null) => void;
  checkAuth: () => Promise<void>;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      user: null,
      token: null,
      isLoading: true, // Start true so auth-provider waits for checkAuth() before redirecting
      isAuthenticated: false,
      didLogout: false,
      authError: "none",

      sendMagicLink: async (email: string): Promise<LoginResult> => {
        set({ isLoading: true });
        try {
          const response = await fetch(withBasePath("/api/auth/send-magic-link"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email }),
          });

          const data = await response.json();

          if (!response.ok) {
            set({ isLoading: false });
            return { success: false, error: data.error || "Failed to send magic link" };
          }

          // Check if this is a direct login response
          if (data.mode === "direct" && data.user && data.token) {
            // Direct login - set auth immediately
            set({
              user: data.user,
              token: data.token,
              isAuthenticated: true,
              isLoading: false,
              didLogout: false,
              authError: "none",
            });

            return {
              success: true,
              mode: "direct",
              user: data.user,
              token: data.token,
              isNewUser: data.isNewUser,
            };
          }

          // Magic link mode - user needs to check email
          set({ isLoading: false });
          return {
            success: true,
            mode: "magic-link",
          };
        } catch (error) {
          set({ isLoading: false });
          return { success: false, error: (error as Error).message };
        }
      },

      signInSharedDashboard: async (): Promise<LoginResult> => {
        set({ isLoading: true });
        try {
          const response = await fetch(withBasePath("/api/auth/shared-login"), {
            method: "POST",
          });
          const data = await response.json().catch(() => ({}));

          if (!response.ok || !data.user || !data.token) {
            set({ isLoading: false, authError: "shared_login_failed" });
            return {
              success: false,
              error: data.error || "Shared dashboard auth is not available",
            };
          }

          set({
            user: data.user,
            token: data.token,
            isAuthenticated: true,
            isLoading: false,
            didLogout: false,
            authError: "none",
          });

          return {
            success: true,
            mode: "shared",
            user: data.user,
            token: data.token,
            isNewUser: data.isNewUser,
          };
        } catch (error) {
          set({ isLoading: false, authError: "shared_login_failed" });
          return { success: false, error: (error as Error).message };
        }
      },

      setAuth: (user: VexaUser, token: string) => {
        set({
          user,
          token,
          isAuthenticated: true,
          isLoading: false,
          didLogout: false,
          authError: "none",
        });
      },

      logout: () => {
        // Clear server-side cookie
        fetch(withBasePath("/api/auth/logout"), { method: "POST" });
        // Clear state
        set({
          user: null,
          token: null,
          isAuthenticated: false,
          didLogout: true,
          authError: "none",
        });
        // In hosted mode: redirect to webapp signout immediately
        // Don't wait for React re-render — avoids flash of "Invalid API token"
        const externalAuthUrl = process.env.NEXT_PUBLIC_EXTERNAL_AUTH_URL;
        if (externalAuthUrl) {
          const webappUrl = process.env.NEXT_PUBLIC_WEBAPP_URL || externalAuthUrl.replace(/\/account$/, '');
          window.location.href = `${webappUrl}/api/auth/signout?callbackUrl=${encodeURIComponent(webappUrl + '/signin')}`;
        }
      },

      setUser: (user) => set({ user, isAuthenticated: !!user }),
      setToken: (token) => set({ token }),

      checkAuth: async () => {
        const { token, user } = get();

        // Use localStorage as a quick pre-render hint so UI doesn't flash,
        // but ALWAYS verify with the server below.
        if (user && token) {
          set({ isAuthenticated: true, isLoading: false, didLogout: false });
        }

        // Always verify with server — localStorage may be stale (e.g. different
        // user logged in on the webapp since last dashboard visit).
        try {
          const response = await fetch(withBasePath("/api/auth/me"));
          if (response.ok) {
            const meData = await response.json();

            // SSO path: /api/auth/me returns user+token from shared cookies
            if (meData.user && meData.token) {
              set({
                user: meData.user,
                token: meData.token,
                isAuthenticated: true,
                isLoading: false,
                didLogout: false,
                authError: "none",
              });
              return;
            }

            // OAuth callback path (Dashboard's own auth flow)
            if (!get().user || !get().token) {
              try {
                const oauthResponse = await fetch(withBasePath("/api/auth/oauth-callback"));
                if (oauthResponse.ok) {
                  const oauthData = await oauthResponse.json();
                  if (oauthData.user && oauthData.token) {
                    set({
                      user: oauthData.user,
                      token: oauthData.token,
                      isAuthenticated: true,
                      isLoading: false,
                      didLogout: false,
                      authError: "none",
                    });
                    return;
                  }
                }
              } catch {
                // OAuth callback failed, but cookie is still valid
              }
            }
            // Cookie returned 200 but no user data — not truly authenticated
            // Only keep isAuthenticated if we already have local user+token
            const current = get();
            if (current.user && current.token) {
              set({ isAuthenticated: true, isLoading: false, didLogout: false, authError: "none" });
            } else {
              set({
                user: null,
                token: null,
                isAuthenticated: false,
                isLoading: false,
                authError: "unauthorized",
              });
            }
          } else {
            // Server returned 401 or error — clear stale local state.
            set({
              user: null,
              token: null,
              isAuthenticated: false,
              isLoading: false,
              authError: "unauthorized",
            });
          }
        } catch {
          // Network error. Never fall back to "authenticated" from unverified local
          // state — that is what let a stale browser bounce between /login and /meetings.
          set({ isAuthenticated: false, isLoading: false, authError: "network" });
        }
      },
    }),
    {
      name: "vexa-auth",
      version: 2,
      // v0/v1 persisted `token` and `isAuthenticated`, so a rehydrated browser
      // could claim to be logged in before the server ever confirmed it.
      // Keep only the fields that are safe without server verification.
      migrate: (persistedState) => keepServerVerifiableFields(persistedState),
      // `migrate` only runs when the stored payload carries a numeric `version`.
      // Releases before v2 wrote no version at all, so the sanitising has to happen
      // in `merge` as well — that is exactly the browser state that loops today.
      merge: (persistedState, currentState) => ({
        ...currentState,
        ...keepServerVerifiableFields(persistedState),
      }),
      partialize: (state) => ({
        user: state.user,
        didLogout: state.didLogout,
      }),
    }
  )
);
