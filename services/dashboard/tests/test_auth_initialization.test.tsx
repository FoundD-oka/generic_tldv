import { afterEach, describe, expect, it, vi } from "vitest";

const USER_A = {
  id: "1",
  email: "a@example.com",
  name: "A",
  max_concurrent_bots: 1,
  created_at: "2026-01-01T00:00:00Z",
};
const USER_B = { ...USER_A, id: "2", email: "b@example.com", name: "B" };

function memoryStorage(): Storage {
  const values = new Map<string, string>();
  return {
    get length() { return values.size; },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => [...values.keys()][index] ?? null,
    removeItem: (key) => { values.delete(key); },
    setItem: (key, value) => { values.set(key, value); },
  };
}

async function loadStore() {
  vi.resetModules();
  vi.stubGlobal("localStorage", memoryStorage());
  vi.stubGlobal("sessionStorage", memoryStorage());
  return (await import("@/stores/auth-store")).useAuthStore;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.resetModules();
});

describe("R10 auth check reaches network terminal state", () => {
  it.each([503, 504])("treats %s as network and clears the unverified token", async (status) => {
    const useAuthStore = await loadStore();
    useAuthStore.setState({ user: USER_A, token: "STALE", isAuthenticated: true, isLoading: true });
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status })));

    await useAuthStore.getState().checkAuth();

    expect(useAuthStore.getState()).toMatchObject({
      user: USER_A,
      token: null,
      isAuthenticated: false,
      isLoading: false,
      authError: "network",
    });
  });

  it("terminates network failures and malformed 200 responses", async () => {
    const useAuthStore = await loadStore();
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("offline"); }));
    await useAuthStore.getState().checkAuth();
    expect(useAuthStore.getState()).toMatchObject({
      token: null, isAuthenticated: false, isLoading: false, authError: "network",
    });

    vi.stubGlobal("fetch", vi.fn(async () => new Response("not-json", { status: 200 })));
    await useAuthStore.getState().checkAuth();
    expect(useAuthStore.getState().authError).toBe("network");
  });

  it("uses one twelve-second deadline for headers and body", async () => {
    vi.useFakeTimers();
    const useAuthStore = await loadStore();
    const fetchMock = vi.fn(() => new Promise<Response>(() => undefined));
    vi.stubGlobal("fetch", fetchMock);

    const check = useAuthStore.getState().checkAuth();
    await vi.advanceTimersByTimeAsync(11_999);
    expect(useAuthStore.getState().isLoading).toBe(true);
    await vi.advanceTimersByTimeAsync(1);
    await check;

    expect(useAuthStore.getState()).toMatchObject({
      token: null, isAuthenticated: false, isLoading: false, authError: "network",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("keeps the deadline active while reading the /me body", async () => {
    vi.useFakeTimers();
    const useAuthStore = await loadStore();
    const response = new Response(JSON.stringify({ user: USER_A, token: "LATE" }), { status: 200 });
    vi.spyOn(response, "json").mockReturnValue(new Promise<never>(() => undefined));
    vi.stubGlobal("fetch", vi.fn(async () => response));

    const check = useAuthStore.getState().checkAuth();
    await vi.advanceTimersByTimeAsync(12_000);
    await check;

    expect(useAuthStore.getState()).toMatchObject({
      token: null, isAuthenticated: false, isLoading: false, authError: "network",
    });
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe("R10 auth checks share one request and ignore stale login", () => {
  it("shares a concurrent check and logout prevents its old success", async () => {
    const useAuthStore = await loadStore();
    const pending = deferred<Response>();
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      if (String(input).includes("/logout")) return Promise.resolve(new Response("{}", { status: 200 }));
      return pending.promise;
    });
    vi.stubGlobal("fetch", fetchMock);

    const first = useAuthStore.getState().checkAuth();
    const second = useAuthStore.getState().checkAuth();
    expect(first).toBe(second);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    useAuthStore.getState().logout();
    pending.resolve(new Response(JSON.stringify({ user: USER_A, token: "OLD" }), { status: 200 }));
    await first;

    expect(useAuthStore.getState()).toMatchObject({
      user: null, token: null, isAuthenticated: false, didLogout: true,
    });
  });

  it("setAuth prevents an old failed check from clearing the new identity", async () => {
    const useAuthStore = await loadStore();
    const pending = deferred<Response>();
    vi.stubGlobal("fetch", vi.fn(() => pending.promise));

    const oldCheck = useAuthStore.getState().checkAuth();
    useAuthStore.getState().setAuth(USER_B, "TOKEN-B");
    pending.reject(new TypeError("old request failed"));
    await oldCheck;

    expect(useAuthStore.getState()).toMatchObject({
      user: USER_B, token: "TOKEN-B", isAuthenticated: true, authError: "none",
    });
  });
});

describe("R10 shared outage does not redirect or retry", () => {
  it("shares one POST, returns network reason at 60 seconds, and never auto-retries", async () => {
    vi.useFakeTimers();
    const useAuthStore = await loadStore();
    const fetchMock = vi.fn(() => new Promise<Response>(() => undefined));
    vi.stubGlobal("fetch", fetchMock);

    const first = useAuthStore.getState().signInSharedDashboard();
    const second = useAuthStore.getState().signInSharedDashboard();
    expect(first).toBe(second);
    await vi.advanceTimersByTimeAsync(60_000);
    await expect(first).resolves.toMatchObject({ success: false, reason: "network" });
    expect(useAuthStore.getState().authError).toBe("network");
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(120_000);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("keeps disabled shared auth in the existing failure branch", async () => {
    const useAuthStore = await loadStore();
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error: "disabled" }), { status: 404 })));

    const result = await useAuthStore.getState().signInSharedDashboard();

    expect(result).toMatchObject({ success: false });
    expect(result.reason).toBeUndefined();
    expect(useAuthStore.getState().authError).toBe("shared_login_failed");
  });
});

describe("R10 oauth and healthy sessions retain behavior", () => {
  it("accepts a healthy /me identity and clears its deadline timer", async () => {
    vi.useFakeTimers();
    const useAuthStore = await loadStore();
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ user: USER_A, token: "HEALTHY" }), { status: 200 })));

    await useAuthStore.getState().checkAuth();

    expect(useAuthStore.getState()).toMatchObject({
      user: USER_A, token: "HEALTHY", isAuthenticated: true, authError: "none",
    });
    expect(vi.getTimerCount()).toBe(0);
  });

  it("uses the OAuth fallback under the same deadline", async () => {
    vi.useFakeTimers();
    const useAuthStore = await loadStore();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ authenticated: true }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ user: USER_A, token: "OAUTH" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await useAuthStore.getState().checkAuth();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(useAuthStore.getState()).toMatchObject({ token: "OAUTH", isAuthenticated: true });
    expect(vi.getTimerCount()).toBe(0);
  });
});
