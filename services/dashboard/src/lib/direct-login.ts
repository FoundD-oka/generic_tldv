import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { getAuthCookieName, getUserInfoCookieName } from "@/lib/auth-cookies";
import { getRegistrationConfig, validateEmailForRegistration } from "@/lib/registration";
import { createUser, createUserToken, findUserByEmail } from "@/lib/vexa-admin-api";

interface DirectLoginOptions {
  name?: string;
  bypassRegistrationPolicy?: boolean;
  mode?: "direct" | "shared";
}

function isSecureRequest(): boolean {
  // Secure cookies only on HTTPS. NODE_ENV=production is always true in Next.js
  // production builds, even when serving over HTTP (self-hosted).
  return process.env.NEXTAUTH_URL?.startsWith("https://") ||
         process.env.DASHBOARD_URL?.startsWith("https://") ||
         false;
}

export function isSharedDashboardAuthEnabled(): boolean {
  const raw = (process.env.DASHBOARD_SHARED_AUTH_ENABLED || "").toLowerCase();
  return ["1", "true", "yes"].includes(raw);
}

export function getSharedDashboardEmail(): string {
  return process.env.DASHBOARD_SHARED_AUTH_EMAIL || "tech@bonginkan.ai";
}

export function getSharedDashboardName(): string | undefined {
  return process.env.DASHBOARD_SHARED_AUTH_NAME || undefined;
}

/**
 * Direct login - authenticate user without email verification.
 * Used by self-hosted direct login and by the shared operation dashboard mode.
 */
export async function handleDirectLogin(
  email: string,
  options: DirectLoginOptions = {}
): Promise<NextResponse> {
  const findResult = await findUserByEmail(email);

  let user;
  let isNewUser = false;

  if (findResult.success && findResult.data) {
    user = findResult.data;
  } else if (findResult.error?.code === "NOT_FOUND") {
    if (!options.bypassRegistrationPolicy) {
      const config = getRegistrationConfig();
      const validationError = validateEmailForRegistration(email, false, config);

      if (validationError) {
        return NextResponse.json(
          { error: validationError },
          { status: 403 }
        );
      }
    }

    const createResult = await createUser({ email, name: options.name });

    if (!createResult.success || !createResult.data) {
      return NextResponse.json(
        { error: createResult.error?.message || "Failed to create user" },
        { status: 500 }
      );
    }

    user = createResult.data;
    isNewUser = true;
  } else if (findResult.error) {
    return NextResponse.json(
      { error: findResult.error.message, code: findResult.error.code },
      { status: 503 }
    );
  }

  const tokenResult = await createUserToken(user!.id);

  if (!tokenResult.success || !tokenResult.data) {
    return NextResponse.json(
      { error: tokenResult.error?.message || "Failed to create session" },
      { status: 500 }
    );
  }

  const apiToken = tokenResult.data.token;
  const cookieStore = await cookies();
  cookieStore.set(getAuthCookieName(), apiToken, {
    httpOnly: true,
    secure: isSecureRequest(),
    sameSite: "lax",
    maxAge: 60 * 60 * 24 * 30,
    path: "/",
  });
  cookieStore.set(getUserInfoCookieName(), JSON.stringify({ email: user!.email, name: user!.name }), {
    httpOnly: true,
    secure: isSecureRequest(),
    sameSite: "lax",
    maxAge: 60 * 60 * 24 * 30,
    path: "/",
  });

  return NextResponse.json({
    success: true,
    mode: options.mode || "direct",
    isNewUser,
    user: {
      id: user!.id,
      email: user!.email,
      name: user!.name,
      max_concurrent_bots: user!.max_concurrent_bots,
      created_at: user!.created_at,
    },
    token: apiToken,
  });
}
