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
  reason?: "network";
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

class AuthDeadlineError extends Error {
  constructor() {
    super("Authentication request timed out");
    this.name = "AuthDeadlineError";
  }
}

interface AuthDeadline {
  signal: AbortSignal;
  expired: Promise<never>;
  clear: () => void;
}

function createAuthDeadline(timeoutMs: number): AuthDeadline {
  const controller = new AbortController();
  let rejectDeadline!: (reason: Error) => void;
  const expired = new Promise<never>((_resolve, reject) => {
    rejectDeadline = reject;
  });
  const timer = setTimeout(() => {
    controller.abort();
    rejectDeadline(new AuthDeadlineError());
  }, timeoutMs);
  return {
    signal: controller.signal,
    expired,
    clear: () => clearTimeout(timer),
  };
}

async function fetchWithDeadline(
  url: string,
  init: RequestInit,
  deadline: AuthDeadline
): Promise<Response> {
  return Promise.race([
    fetch(url, { ...init, signal: deadline.signal }),
    deadline.expired,
  ]);
}

async function readJsonWithDeadline(response: Response, deadline: AuthDeadline): Promise<unknown> {
  return Promise.race([response.json(), deadline.expired]);
}

let authEpoch = 0;
let checkAuthFlight: Promise<void> | null = null;
let sharedLoginFlight: Promise<LoginResult> | null = null;

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

      signInSharedDashboard: (): Promise<LoginResult> => {
        if (sharedLoginFlight) return sharedLoginFlight;

        const epoch = ++authEpoch;
        set({ isLoading: true });
        const deadline = createAuthDeadline(60_000);
        const flight = (async (): Promise<LoginResult> => {
          try {
            const response = await fetchWithDeadline(
              withBasePath("/api/auth/shared-login"),
              { method: "POST" },
              deadline
            );
            if (response.status === 429 || response.status >= 500) {
              if (authEpoch === epoch) {
                set({
                  token: null,
                  isAuthenticated: false,
                  isLoading: false,
                  authError: "network",
                });
              }
              return {
                success: false,
                error: "Authentication service unavailable",
                reason: "network",
              };
            }

            if (!response.ok) {
              if (authEpoch === epoch) set({ isLoading: false, authError: "shared_login_failed" });
              return {
                success: false,
                error: "Shared dashboard auth is not available",
              };
            }

            const rawData = await readJsonWithDeadline(response, deadline);
            const data = rawData && typeof rawData === "object"
              ? rawData as Record<string, unknown>
              : {};
            if (!data.user || !data.token || typeof data.token !== "string") {
              if (authEpoch === epoch) {
                set({
                  token: null,
                  isAuthenticated: false,
                  isLoading: false,
                  authError: "network",
                });
              }
              return {
                success: false,
                error: "Invalid shared dashboard response",
                reason: "network",
              };
            }

            if (authEpoch === epoch) {
              set({
                user: data.user as VexaUser,
                token: data.token,
                isAuthenticated: true,
                isLoading: false,
                didLogout: false,
                authError: "none",
              });
            }

            return {
              success: true,
              mode: "shared",
              user: data.user as VexaUser,
              token: data.token,
              isNewUser: typeof data.isNewUser === "boolean" ? data.isNewUser : undefined,
            };
          } catch (error) {
            if (authEpoch === epoch) {
              set({
                token: null,
                isAuthenticated: false,
                isLoading: false,
                authError: "network",
              });
            }
            return {
              success: false,
              error: (error as Error).message,
              reason: "network",
            };
          } finally {
            deadline.clear();
          }
        })();

        const wrappedFlight = flight.finally(() => {
          if (sharedLoginFlight === wrappedFlight) sharedLoginFlight = null;
        });
        sharedLoginFlight = wrappedFlight;
        return wrappedFlight;
      },

      setAuth: (user: VexaUser, token: string) => {
        authEpoch += 1;
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
        authEpoch += 1;
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

      checkAuth: (): Promise<void> => {
        if (checkAuthFlight) return checkAuthFlight;

        const epoch = authEpoch;
        const deadline = createAuthDeadline(12_000);
        const setIfCurrent = (next: Partial<AuthState>) => {
          if (authEpoch === epoch) set(next);
        };

        const flight = (async () => {
          const initial = get();

          // An in-memory identity is only a rendering hint until the server confirms it.
          if (initial.user && initial.token) {
            setIfCurrent({ isAuthenticated: true, isLoading: false, didLogout: false });
          }

          try {
            const response = await fetchWithDeadline(
              withBasePath("/api/auth/me"),
              {},
              deadline
            );

            if (response.status === 401) {
              setIfCurrent({
                user: null,
                token: null,
                isAuthenticated: false,
                isLoading: false,
                authError: "unauthorized",
              });
              return;
            }
            if (!response.ok) {
              setIfCurrent({
                token: null,
                isAuthenticated: false,
                isLoading: false,
                authError: "network",
              });
              return;
            }
            const rawMeData = await readJsonWithDeadline(response, deadline);
            if (!rawMeData || typeof rawMeData !== "object") {
              setIfCurrent({
                token: null,
                isAuthenticated: false,
                isLoading: false,
                authError: "network",
              });
              return;
            }

            const meData = rawMeData as Record<string, unknown>;
            if (meData.user && typeof meData.token === "string" && meData.token) {
              setIfCurrent({
                user: meData.user as VexaUser,
                token: meData.token,
                isAuthenticated: true,
                isLoading: false,
                didLogout: false,
                authError: "none",
              });
              return;
            }

            // Preserve the existing OAuth fallback when /auth/me is a valid 200
            // without a complete identity.
            const current = get();
            if (!current.user || !current.token) {
              const oauthResponse = await fetchWithDeadline(
                withBasePath("/api/auth/oauth-callback"),
                {},
                deadline
              );
              if (oauthResponse.status === 401) {
                setIfCurrent({
                  user: null,
                  token: null,
                  isAuthenticated: false,
                  isLoading: false,
                  authError: "unauthorized",
                });
                return;
              }
              if (!oauthResponse.ok) {
                setIfCurrent({
                  token: null,
                  isAuthenticated: false,
                  isLoading: false,
                  authError: "network",
                });
                return;
              }
              const rawOauthData = await readJsonWithDeadline(oauthResponse, deadline);
              if (!rawOauthData || typeof rawOauthData !== "object") {
                setIfCurrent({
                  token: null,
                  isAuthenticated: false,
                  isLoading: false,
                  authError: "network",
                });
                return;
              }
              const oauthData = rawOauthData as Record<string, unknown>;
              if (oauthData.user && typeof oauthData.token === "string" && oauthData.token) {
                setIfCurrent({
                  user: oauthData.user as VexaUser,
                  token: oauthData.token,
                  isAuthenticated: true,
                  isLoading: false,
                  didLogout: false,
                  authError: "none",
                });
                return;
              }
            }

            const confirmed = get();
            if (confirmed.user && confirmed.token) {
              setIfCurrent({
                isAuthenticated: true,
                isLoading: false,
                didLogout: false,
                authError: "none",
              });
            } else {
              setIfCurrent({
                token: null,
                isAuthenticated: false,
                isLoading: false,
                authError: "network",
              });
            }
          } catch {
            setIfCurrent({
              token: null,
              isAuthenticated: false,
              isLoading: false,
              authError: "network",
            });
          } finally {
            deadline.clear();
          }
        })();

        const wrappedFlight = flight.finally(() => {
          if (checkAuthFlight === wrappedFlight) checkAuthFlight = null;
        });
        checkAuthFlight = wrappedFlight;
        return wrappedFlight;
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
