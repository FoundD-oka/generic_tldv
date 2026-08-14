// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * 共有ダッシュボードの自動サインインが失敗したとき、UI が遷移を繰り返さずに
 * 静止した終端状態(エラー + 再試行)へ収束することを固定する。
 */

const push = vi.fn();
const replace = vi.fn();
let pathname = "/login";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace, prefetch: vi.fn(), back: vi.fn(), refresh: vi.fn() }),
  usePathname: () => pathname,
}));
vi.mock("next/image", () => ({
  default: ({ alt }: { alt?: string }) => <span data-testid="next-image" data-alt={alt ?? ""} />,
}));
vi.mock("next-auth/react", () => ({ signIn: vi.fn() }));
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));
vi.mock("@/hooks/use-runtime-config", () => ({
  useRuntimeConfig: () => ({ config: null, isLoading: false }),
}));

import LoginPage from "@/app/login/page";
import { AuthProvider } from "@/components/auth/auth-provider";
import { useAuthStore } from "@/stores/auth-store";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const HEALTH_BODY = {
  status: "ok",
  authMode: "direct",
  checks: {
    smtp: { configured: true },
    googleOAuth: { configured: false },
    adminApi: { configured: true, reachable: true },
    vexaApi: { configured: true, reachable: true },
  },
  missingConfig: [],
};

const SHARED_USER = {
  id: "7",
  email: "shared@example.com",
  name: "Shared",
  max_concurrent_bots: 1,
  created_at: "2026-01-01T00:00:00Z",
};

type SharedLoginOutcome = "ok" | "server-error" | "network-error";

function installFetch(sharedLogin: SharedLoginOutcome, meStatus: number | "network-error" = 401) {
  const handler = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();

    if (url.includes("/api/config")) {
      return new Response(JSON.stringify({ sharedAuth: { enabled: true } }), { status: 200 });
    }
    if (url.includes("/api/health")) {
      return new Response(JSON.stringify(HEALTH_BODY), { status: 200 });
    }
    if (url.includes("/api/auth/shared-login")) {
      if (sharedLogin === "network-error") throw new TypeError("Failed to fetch");
      if (sharedLogin === "server-error") {
        return new Response(JSON.stringify({ error: "boom" }), { status: 500 });
      }
      return new Response(JSON.stringify({ user: SHARED_USER, token: "SHARED-TOKEN" }), {
        status: 200,
      });
    }
    if (url.includes("/api/auth/me")) {
      if (meStatus === "network-error") throw new TypeError("Failed to fetch");
      return new Response(JSON.stringify({ error: "unauthorized" }), { status: meStatus });
    }
    return new Response("{}", { status: 200 });
  });

  vi.stubGlobal("fetch", handler);
  return handler;
}

let container: HTMLDivElement;
let root: Root;

async function render(element: React.ReactElement) {
  await act(async () => {
    root.render(element);
  });
  // Let effect-triggered promise chains settle.
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

beforeEach(() => {
  push.mockClear();
  replace.mockClear();
  pathname = "/login";
  localStorage.clear();
  sessionStorage.clear();
  useAuthStore.setState({
    user: null,
    token: null,
    isAuthenticated: false,
    isLoading: true,
    didLogout: false,
    authError: "none",
  });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => {
    root.unmount();
  });
  container.remove();
  vi.unstubAllGlobals();
});

describe("login page shared auto sign-in", () => {
  it("navigates to /meetings exactly once when shared login succeeds", async () => {
    installFetch("ok");

    await render(<LoginPage />);

    expect(replace.mock.calls).toEqual([["/meetings"]]);
    expect(push).not.toHaveBeenCalled();
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
  });

  it("does not navigate when shared login fails with 500 and offers a manual retry", async () => {
    installFetch("server-error");

    await render(<LoginPage />);

    expect(replace).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
    expect(container.textContent).toContain("自動サインインに失敗しました");
    expect(container.textContent).toContain("再試行");
    expect(container.querySelector(".animate-spin")).toBeNull();
    expect(useAuthStore.getState().authError).toBe("shared_login_failed");
  });

  it("does not navigate when shared login fails with a network error", async () => {
    installFetch("network-error");

    await render(<LoginPage />);

    expect(replace).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
    expect(container.textContent).toContain("自動サインインに失敗しました");
  });

  it("navigates only after a manual retry succeeds", async () => {
    installFetch("server-error");
    await render(<LoginPage />);
    expect(replace).not.toHaveBeenCalled();

    const retry = [...container.querySelectorAll("button")].find(
      (button) => button.textContent?.trim() === "再試行"
    );
    expect(retry).toBeDefined();

    installFetch("ok");
    await act(async () => {
      retry!.click();
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(replace.mock.calls).toEqual([["/meetings"]]);
  });
});

describe("auth provider on protected routes", () => {
  beforeEach(() => {
    pathname = "/meetings";
  });

  it("redirects to /login exactly once when session and shared login both fail", async () => {
    installFetch("server-error", 401);

    await render(
      <AuthProvider>
        <div>protected</div>
      </AuthProvider>
    );
    // Re-render to prove the redirect is not re-issued on every pass.
    await render(
      <AuthProvider>
        <div>protected</div>
      </AuthProvider>
    );

    expect(push.mock.calls).toEqual([["/login"]]);
  });

  it("shows a terminal error with retry instead of redirecting on network failure", async () => {
    installFetch("server-error", "network-error");

    await render(
      <AuthProvider>
        <div>protected</div>
      </AuthProvider>
    );

    expect(push).not.toHaveBeenCalled();
    expect(useAuthStore.getState().authError).toBe("network");
    expect(container.textContent).toContain("サーバーに接続できません");
    expect(container.textContent).toContain("再試行");
    expect(container.querySelector(".animate-spin")).toBeNull();
  });
});
