import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * /login ↔ /meetings の無限リダイレクトを支えていた「サーバー未検証のクライアント
 * auth state」を潰すための回帰テスト。
 */

function memoryStorage(seed: Record<string, string> = {}): Storage {
  const values = new Map(Object.entries(seed));
  return {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key: string) => values.get(key) ?? null,
    key: (index: number) => [...values.keys()][index] ?? null,
    removeItem: (key: string) => {
      values.delete(key);
    },
    setItem: (key: string, value: string) => {
      values.set(key, value);
    },
  };
}

const LEGACY_USER = {
  id: "1",
  email: "stale@example.com",
  name: "Stale User",
  max_concurrent_bots: 1,
  created_at: "2026-01-01T00:00:00Z",
};

async function loadStoreWith(local: Storage, session: Storage) {
  vi.resetModules();
  vi.stubGlobal("localStorage", local);
  vi.stubGlobal("sessionStorage", session);
  return import("@/stores/auth-store");
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.resetModules();
});

describe("auth store rehydration cannot claim an unverified session", () => {
  it("legacy vexa-auth payload rehydrates as unauthenticated", async () => {
    const local = memoryStorage({
      "vexa-auth": JSON.stringify({
        state: {
          user: LEGACY_USER,
          token: "LEGACY-TOKEN",
          isAuthenticated: true,
          didLogout: false,
        },
      }),
    });

    const { useAuthStore } = await loadStoreWith(local, memoryStorage());
    const state = useAuthStore.getState();

    expect(state.isAuthenticated).toBe(false);
    expect(state.token).toBeNull();
    expect(state.user).toEqual(LEGACY_USER);
  });

  it("persisted payload never contains isAuthenticated or token", async () => {
    const local = memoryStorage();
    const { useAuthStore } = await loadStoreWith(local, memoryStorage());

    useAuthStore.getState().setAuth(LEGACY_USER, "FRESH-TOKEN");
    await Promise.resolve();

    const persisted = JSON.parse(local.getItem("vexa-auth") as string);
    expect(persisted.version).toBe(2);
    expect(Object.keys(persisted.state).sort()).toEqual(["didLogout", "user"]);
    expect(persisted.state).not.toHaveProperty("isAuthenticated");
    expect(persisted.state).not.toHaveProperty("token");
    expect(local.getItem("vexa-auth")).not.toContain("FRESH-TOKEN");
  });

  it("removes every legacy browser auth key from local and session storage", async () => {
    const legacySeed = {
      "vexa-token": "CANARY",
      "vexa-user": "CANARY",
      vexa_user: "CANARY",
      authToken: "CANARY",
      api_key: "CANARY",
      "vexa-api-key": "CANARY",
      "unrelated-key": "KEEP",
    };
    const local = memoryStorage({ ...legacySeed });
    const session = memoryStorage({ ...legacySeed });

    const { LEGACY_BROWSER_AUTH_KEYS } = await loadStoreWith(local, session);

    expect([...LEGACY_BROWSER_AUTH_KEYS]).toHaveLength(6);
    for (const key of LEGACY_BROWSER_AUTH_KEYS) {
      expect(local.getItem(key)).toBeNull();
      expect(session.getItem(key)).toBeNull();
    }
    expect(local.getItem("unrelated-key")).toBe("KEEP");
    expect(session.getItem("unrelated-key")).toBe("KEEP");
  });
});

describe("checkAuth reaches a terminal unauthenticated state on failure", () => {
  let useAuthStore: Awaited<ReturnType<typeof loadStoreWith>>["useAuthStore"];

  beforeEach(async () => {
    ({ useAuthStore } = await loadStoreWith(memoryStorage(), memoryStorage()));
    useAuthStore.setState({
      user: LEGACY_USER,
      token: "LOCAL-TOKEN",
      isAuthenticated: true,
      isLoading: true,
      authError: "none",
    });
  });

  it("401 clears local credentials and reports unauthorized", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ error: "unauthorized" }), { status: 401 }))
    );

    await useAuthStore.getState().checkAuth();
    const state = useAuthStore.getState();

    expect(state.isAuthenticated).toBe(false);
    expect(state.user).toBeNull();
    expect(state.token).toBeNull();
    expect(state.isLoading).toBe(false);
    expect(state.authError).toBe("unauthorized");
  });

  it("network failure does not fall back to authenticated", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      })
    );

    await useAuthStore.getState().checkAuth();
    const state = useAuthStore.getState();

    expect(state.isAuthenticated).toBe(false);
    expect(state.isLoading).toBe(false);
    expect(state.authError).toBe("network");
  });

  it("verified session clears the error state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ user: LEGACY_USER, token: "SERVER-TOKEN" }), {
            status: 200,
          })
      )
    );

    await useAuthStore.getState().checkAuth();
    const state = useAuthStore.getState();

    expect(state.isAuthenticated).toBe(true);
    expect(state.authError).toBe("none");
  });
});

describe("shared login response contract", () => {
  it("POST /api/auth/shared-login returns user and token on success", async () => {
    vi.resetModules();
    process.env.DASHBOARD_SHARED_AUTH_ENABLED = "true";
    process.env.DASHBOARD_SHARED_AUTH_EMAIL = "shared@example.com";

    vi.doMock("next/headers", () => ({
      cookies: async () => ({ set: vi.fn(), get: vi.fn(), delete: vi.fn() }),
    }));
    vi.doMock("@/lib/vexa-admin-api", () => ({
      findUserByEmail: vi.fn(async () => ({
        success: true,
        data: { ...LEGACY_USER, email: "shared@example.com" },
      })),
      createUser: vi.fn(),
      createUserToken: vi.fn(async () => ({ success: true, data: { token: "SHARED-TOKEN" } })),
    }));

    const { POST } = await import("@/app/api/auth/shared-login/route");
    const response = await POST();
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body.mode).toBe("shared");
    expect(body.user).toMatchObject({ email: "shared@example.com" });
    expect(body.token).toBe("SHARED-TOKEN");

    vi.doUnmock("next/headers");
    vi.doUnmock("@/lib/vexa-admin-api");
  });
});
