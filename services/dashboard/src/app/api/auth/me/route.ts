import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { getAuthCookieName } from "@/lib/auth-cookies";

const AUTH_ME_TIMEOUT_MS = 10_000;

function jsonResponse(body: object, status: number) {
  return NextResponse.json(body, {
    status,
    headers: { "Cache-Control": "no-store" },
  });
}

/**
 * Get current user info from token.
 * Auth chain: cookie only. No fallback to env vars.
 * User identity resolved via gateway /auth/me.
 */
export async function GET() {
  const VEXA_API_URL = process.env.VEXA_API_URL;
  if (!VEXA_API_URL) {
    return jsonResponse({ error: "VEXA_API_URL is required" }, 500);
  }

  const cookieStore = await cookies();
  const authCookieName = getAuthCookieName();
  const cookieToken = cookieStore.get(authCookieName)?.value;
  const token = cookieToken || "";

  if (!token) {
    return jsonResponse({ error: "Not authenticated" }, 401);
  }

  const controller = new AbortController();
  let rejectDeadline!: (reason: Error) => void;
  const deadline = new Promise<never>((_resolve, reject) => {
    rejectDeadline = reject;
  });
  const timer = setTimeout(() => {
    controller.abort();
    rejectDeadline(new DOMException("Authentication check timed out", "TimeoutError"));
  }, AUTH_ME_TIMEOUT_MS);

  try {
    // Keep the deadline active until the response body has been consumed.
    const response = await Promise.race([
      fetch(`${VEXA_API_URL}/auth/me`, {
        headers: { "X-API-Key": token },
        signal: controller.signal,
      }),
      deadline,
    ]);

    if (response.status === 401) {
      if (cookieToken) cookieStore.delete(authCookieName);
      return jsonResponse({ error: "Invalid token" }, 401);
    }
    if (!response.ok) {
      return jsonResponse({ error: "Authentication service unavailable" }, 503);
    }

    let data: unknown;
    try {
      data = await Promise.race([response.json(), deadline]);
    } catch (error) {
      if (error instanceof DOMException && error.name === "TimeoutError") throw error;
      return jsonResponse({ error: "Authentication service unavailable" }, 503);
    }

    if (!data || typeof data !== "object") {
      return jsonResponse({ error: "Authentication service unavailable" }, 503);
    }
    const identity = data as Record<string, unknown>;
    if (
      (typeof identity.user_id !== "string" && typeof identity.user_id !== "number") ||
      typeof identity.email !== "string" ||
      identity.email.length === 0
    ) {
      return jsonResponse({ error: "Authentication service unavailable" }, 503);
    }

    const user = {
      id: identity.user_id,
      email: identity.email,
      name: typeof identity.name === "string" && identity.name ? identity.name : identity.email,
    };

    return jsonResponse({ authenticated: true, user, token }, 200);
  } catch (error) {
    if (
      (error instanceof DOMException && error.name === "TimeoutError") ||
      (controller.signal.aborted && error instanceof DOMException && error.name === "AbortError")
    ) {
      return jsonResponse({ error: "Authentication check timed out" }, 504);
    }
    return jsonResponse({ error: "Failed to verify authentication" }, 503);
  } finally {
    clearTimeout(timer);
  }
}
